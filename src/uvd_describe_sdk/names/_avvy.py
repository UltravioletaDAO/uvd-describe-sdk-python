"""Avvy Domains (`.avax`), read on-chain on Avalanche C-Chain.

karma-hello resolved `.avax` through `api.avvy.domains` (`domain_resolver.py:
337-349`), with nothing checked on-chain — and on 2026-09-24 that API answered
503. This reads the chain, the way Avvy's own clients do (`python-client`
`avvy/client.py` and `js-client` `src/index.js`, read 2026-09-24):

* The name hash is Poseidon, not keccak, and it is computed ON-CHAIN by Avvy's
  `Poseidon` contract (`poseidon(uint256[3])`), one call per label, exactly as
  their Python client does. No Poseidon constants are reimplemented here. The
  hash of the TLD `avax` is the constant their clients pre-cache; the recording
  script re-reads it from the contract and a test pins that they agree.
* Expiry comes first: `Domain.getDomainExpiry(hash of the second-level name)`.
  `0` is `not_found`, a past date is `expired`.
* Then `ResolverRegistryV1.get(domain hash, name hash)` gives the resolver and
  its dataset, and `resolveStandard(dataset, name hash, 3)` the EVM address
  (standard key 3, `EVM`, from `client-common/records/records.json`).
* Reverse: `ReverseResolverRegistryV1.getResolver(3)` → the EVM reverse resolver
  → `get(address)` → the name hash → `RainbowTableV1.lookup(hash)` → the name.
  Then confirmed forward.

Contract addresses: `client-common/contracts/43114.json` at `f99e2c1`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ..name_models import NameErrorCode
from . import _abi
from ._hash import ZERO_ADDRESS, is_hex_address, selector, to_checksum_address
from ._proto import AVALANCHE, Call, Outcome, Reverted, Step

POSEIDON = "0x6e03620156860584870b6415447770BbC7D6eB0E"
DOMAIN = "0x797AC669A1908ca68CD9854994345f570495541A"
RESOLVER_REGISTRY = "0x3947d4c62C108A8A7bA3ED53AbaDcFF5D8998637"
REVERSE_RESOLVER_REGISTRY = "0x87388F6EAAfA4bB970EEefd97D29e487949fBbBd"
RAINBOW_TABLE = "0x3b17bAcEDF86f4d36563d2920771ed105D8B6636"

#: The `EVM` standard record key (`records.json`: key 3, "C-Chain / EVM Address").
EVM_KEY = 3

#: `poseidon([0, 2019653217, 0])`: the hash of the TLD `avax`, pre-cached by both
#: official clients. `tests/` pins it against the value read from the contract.
AVAX_TLD_HASH = 4272832630669137235923015693490068373911885005413996126751674003559469537065

_SEL_POSEIDON = selector("poseidon(uint256[3])")
_SEL_EXPIRY = selector("getDomainExpiry(uint256)")
_SEL_REGISTRY_GET = selector("get(uint256,uint256)")
_SEL_RESOLVE_STANDARD = selector("resolveStandard(uint256,uint256,uint256)")
_SEL_RESOLVE = selector("resolve(uint256,uint256,string)")
_SEL_GET_RESOLVER = selector("getResolver(uint256)")
_SEL_REVERSE_GET = selector("get(address)")
_SEL_LOOKUP = selector("lookup(uint256)")


def _pack(chars: List[int]) -> int:
    # `_prep_preimage_signal`: the 31 bytes reversed, read as one big number.
    return int.from_bytes(bytes(reversed(chars)), "big")


def _label_inputs(label: str) -> List[int]:
    chars = [ord(c) for c in label] + [0] * (62 - len(label))
    return [_pack(chars[:31]), _pack(chars[31:])]


def poseidon(triad: List[int]) -> Step[int]:
    if triad == [0, 2019653217, 0]:
        return AVAX_TLD_HASH
    out: bytes = yield Call(
        AVALANCHE, POSEIDON, _SEL_POSEIDON + _abi.encode(["uint256[3]"], [triad])
    )
    (value,) = _abi.decode(["uint256"], out)
    result: int = value
    return result


def name_hashes(name: str) -> Step[Tuple[int, int]]:
    """Avvy's `name_hash`, TLD first: `(hash of the second-level name, hash of name)`.

    One Poseidon call per label below the TLD; the second-level hash is the one
    the `Domain` contract keys its expiry by.
    """
    value = 0
    second_level = 0
    for depth, label in enumerate(reversed(name.split("."))):
        value = yield from poseidon([value] + _label_inputs(label))
        if depth == 1:
            second_level = value
    return second_level, value


def _second_level(name: str) -> str:
    labels = name.split(".")
    return ".".join(labels[-2:])


def _prepare(name: str, now: float) -> Step[Tuple[str, int, int]]:
    """Expiry, then the resolver and dataset. Returns `(resolver, dataset, hash)`."""
    try:
        domain_hash, full_hash = yield from name_hashes(name)
        out: bytes = yield Call(
            AVALANCHE, DOMAIN, _SEL_EXPIRY + _abi.encode(["uint256"], [domain_hash])
        )
        (expiry,) = _abi.decode(["uint256"], out)
    except (Reverted, _abi.AbiError):
        raise Outcome(
            NameErrorCode.NOT_FOUND, "the Avvy contracts did not answer for this name"
        ) from None
    if expiry == 0:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{_second_level(name)} is not registered in Avvy")
    if expiry <= now:
        when = datetime.fromtimestamp(expiry, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raise Outcome(NameErrorCode.EXPIRED, f"{_second_level(name)} expired on {when}")
    try:
        out = yield Call(
            AVALANCHE,
            RESOLVER_REGISTRY,
            _SEL_REGISTRY_GET + _abi.encode(["uint256", "uint256"], [domain_hash, full_hash]),
        )
        resolver, dataset = _abi.decode(["address", "uint256"], out)
    except Reverted:
        # "ResolverRegistry: resolver not set" — what the official clients map to None.
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no resolver in Avvy") from None
    except _abi.AbiError:
        raise Outcome(
            NameErrorCode.NOT_FOUND, "the Avvy resolver registry answered garbage"
        ) from None
    if resolver == ZERO_ADDRESS:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no resolver in Avvy")
    return resolver, dataset, full_hash


def forward(name: str, now: float) -> Step[str]:
    resolver, dataset, full_hash = yield from _prepare(name, now)
    try:
        out: bytes = yield Call(
            AVALANCHE,
            resolver,
            _SEL_RESOLVE_STANDARD
            + _abi.encode(["uint256", "uint256", "uint256"], [dataset, full_hash, EVM_KEY]),
        )
        (value,) = _abi.decode(["string"], out)
    except (Reverted, _abi.AbiError):
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no EVM record in Avvy") from None
    value = value.strip()
    if not value:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no EVM record in Avvy")
    if not is_hex_address(value):
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has an EVM record that is not an address")
    if value.lower() == ZERO_ADDRESS:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} points to the zero address (not set)")
    return to_checksum_address(value)


def text(name: str, now: float, key: str) -> Step[Optional[str]]:
    resolver, dataset, full_hash = yield from _prepare(name, now)
    try:
        out: bytes = yield Call(
            AVALANCHE,
            resolver,
            _SEL_RESOLVE + _abi.encode(["uint256", "uint256", "string"], [dataset, full_hash, key]),
        )
        (value,) = _abi.decode(["string"], out)
    except (Reverted, _abi.AbiError):
        return None
    return value or None


def _decode_signals(signals: List[int]) -> str:
    """`decode_name_hash_input_signals` of the official Python client."""
    labels = []
    for i in range(0, len(signals) - 1, 2):
        raw = b"".join(s.to_bytes(31, "big") for s in (signals[i], signals[i + 1]))
        # Each half was packed reversed; undoing it yields the label, NUL-padded.
        halves = raw[:31][::-1] + raw[31:][::-1]
        labels.append(halves.rstrip(b"\x00").decode("ascii", errors="replace"))
    return ".".join(reversed(labels))


def claimed(address: str) -> Step[Optional[str]]:
    """The Avvy primary name `address` claims. Unconfirmed."""
    try:
        out: bytes = yield Call(
            AVALANCHE,
            REVERSE_RESOLVER_REGISTRY,
            _SEL_GET_RESOLVER + _abi.encode(["uint256"], [EVM_KEY]),
        )
        (reverse_resolver,) = _abi.decode(["address"], out)
        if reverse_resolver == ZERO_ADDRESS:
            return None
        out = yield Call(
            AVALANCHE, reverse_resolver, _SEL_REVERSE_GET + _abi.encode(["address"], [address])
        )
        _domain, name_hash_value = _abi.decode(["uint256", "uint256"], out)
        out = yield Call(
            AVALANCHE, RAINBOW_TABLE, _SEL_LOOKUP + _abi.encode(["uint256"], [name_hash_value])
        )
        (signals,) = _abi.decode(["uint256[]"], out)
    except (Reverted, _abi.AbiError):
        # "EVMReverseResolverV1: does not exist" / "RainbowTableV1: entry not found".
        return None
    name = _decode_signals(list(signals))
    return name or None
