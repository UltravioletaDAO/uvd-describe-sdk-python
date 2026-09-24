"""Unstoppable Domains (UNS), read on-chain through UD's ProxyReader contracts.

Mirrors `@unstoppabledomains/resolution` (read 2026-09-24 at `8f9fb66`):

* The token id is the EIP-137 namehash of the name (their `eip137Namehash`,
  keccak through `crypto-js/sha3`, which is Keccak and not FIPS SHA-3).
* A name is read on its L2 FIRST — Polygon for most TLDs, Base for the TLDs UD
  registers there (`UNS_BASE` in their library) — and on L1 only if the L2 has
  no owner for it. Same order as their `Uns.get()`.
* The EVM address is the record `crypto.ETH.address`, which is what their
  `addr(domain, "ETH")` reads.
* Reverse: `reverseNameOf(address)` on L1, then on the L2s. Their library reads
  the token id with `reverseOf` and then turns it into a name through UD's HTTP
  metadata API; `reverseNameOf` does it on-chain, so nothing here leaves the
  chain. The claimed name is then confirmed forward, like every reverse here.

The ProxyReader addresses are UD's `uns-config.json` (version 0.9.11 in that
commit) and were read live on 2026-09-24 by the recording script.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..name_models import NameErrorCode
from . import _abi
from ._hash import ZERO_ADDRESS, is_hex_address, namehash, selector, to_checksum_address
from ._normalize import UNS_L2_BY_TLD
from ._proto import BASE, ETHEREUM, POLYGON, Call, Outcome, Reverted, Step

PROXY_READERS: Dict[str, str] = {
    ETHEREUM: "0x578853aa776Eef10CeE6c4dd2B5862bdcE767A8B",
    POLYGON: "0x91EDd8708062bd4233f4Dd0FCE15A7cb4d500091",
    BASE: "0x78c4b414e1abdf0de267deda01dffd4cd0817a16",
}

ETH_ADDRESS_KEY = "crypto.ETH.address"

_SEL_GET_DATA = selector("getData(string[],uint256)")
_SEL_REVERSE_NAME = selector("reverseNameOf(address)")


def chains_for(name: str) -> Tuple[str, ...]:
    """The chains a UNS name is read on, L2 first. Empty = not an EVM UNS TLD."""
    l2 = UNS_L2_BY_TLD.get(name.rsplit(".", 1)[-1])
    return (l2, ETHEREUM) if l2 else ()


def get_records(name: str, keys: List[str]) -> Step[Tuple[str, str, List[str]]]:
    """`(resolver, owner, values)` from the first layer that owns the name."""
    chains = chains_for(name)
    if not chains:
        raise Outcome(
            NameErrorCode.UNSUPPORTED_SYSTEM,
            "this Unstoppable TLD is registered off the EVM chains UD's readers cover",
        )
    token = int.from_bytes(namehash(name), "big")
    for chain in chains:
        try:
            out: bytes = yield Call(
                chain,
                PROXY_READERS[chain],
                _SEL_GET_DATA + _abi.encode(["string[]", "uint256"], [keys, token]),
            )
            resolver, owner, values = _abi.decode(["address", "address", "string[]"], out)
        except (Reverted, _abi.AbiError):
            raise Outcome(
                NameErrorCode.NOT_FOUND, f"the UNS reader on {chain} answered garbage"
            ) from None
        if owner != ZERO_ADDRESS:
            return resolver, owner, list(values)
    raise Outcome(NameErrorCode.NOT_FOUND, f"{name} is not registered in UNS")


def forward(name: str) -> Step[str]:
    """The EIP-55 address of `crypto.ETH.address`. Raises `Outcome`."""
    resolver, _owner, values = yield from get_records(name, [ETH_ADDRESS_KEY])
    if resolver == ZERO_ADDRESS:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no resolver in UNS")
    value = values[0].strip() if values else ""
    if not value:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no {ETH_ADDRESS_KEY} record")
    if not is_hex_address(value):
        raise Outcome(
            NameErrorCode.NOT_FOUND, f"{name} has a {ETH_ADDRESS_KEY} that is not an address"
        )
    if value.lower() == ZERO_ADDRESS:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} points to the zero address (not set)")
    return to_checksum_address(value)


def text(name: str, key: str) -> Step[Optional[str]]:
    """A UNS record. `None` = the name exists and the key is not set."""
    resolver, _owner, values = yield from get_records(name, [key])
    if resolver == ZERO_ADDRESS:
        return None
    value = values[0] if values else ""
    return value or None


#: Where a UNS reverse record can live, in the order they are read.
REVERSE_CHAINS = (ETHEREUM, POLYGON, BASE)


def claimed(address: str, chains: Tuple[str, ...] = REVERSE_CHAINS) -> Step[Optional[str]]:
    """The UNS primary name `address` claims, L1 first. Unconfirmed."""
    for chain in chains:
        try:
            out: bytes = yield Call(
                chain, PROXY_READERS[chain], _SEL_REVERSE_NAME + _abi.encode(["address"], [address])
            )
            (name,) = _abi.decode(["string"], out)
        except (Reverted, _abi.AbiError):
            continue
        if name:
            found: Optional[str] = name
            return found
    return None
