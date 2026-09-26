"""ENS, Basenames and DNS-imported names: one registry on L1, three ways in.

Every address and selector below was read on-chain on 2026-09-24 before this
was written (the recording script replays them; `scripts/grabar_fixtures_names.py`).

FORWARD (ENSIP-10, then EIP-3668)
---------------------------------
1. Walk the L1 registry from the name up until a resolver is set. Execution
   Market and karma-hello asked web3 for the resolver of the exact name only,
   which is why a wildcard name (every `*.base.eth`, every `*.uni.eth`) came back
   empty for them.
2. If that resolver implements `resolve(bytes,bytes)` (interface `0x9061b923`),
   call it with the DNS-encoded name — also when the match is exact, as ENSIP-10
   says. Otherwise call the record directly, and only on an exact match.
3. If it reverts with `OffchainLookup`, follow EIP-3668 (`_proto.ccip_call`).
   That is how `jesse.base.eth` resolves from L1: the Basenames resolver at
   `base.eth` sends the lookup to Coinbase's gateway and verifies the signed
   answer in its callback. Nothing is read from an ENS registry on Base, because
   there is none — karma-hello's bug.

EXPIRY — THE RECORDS OUTLIVE THE NAME
-------------------------------------
When a `.eth` registration lapses, its resolver and its address stay on-chain
and keep resolving to the previous owner. Neither Execution Market nor
karma-hello checked. Here the second-level registration is read first:
`nameExpires(labelhash)` on the ETH registrar (L1) for `*.eth`, and on the
Basenames registrar (Base) for `*.base.eth`. `0` is `not_found`; a past date is
`expired`, grace period included — a name its owner can still renew but has not
is not a payee.

REVERSE, ALWAYS CONFIRMED FORWARD
---------------------------------
* L1: the name of `<addr>.addr.reverse`, found with the same registry walk. On
  2026-09-24 `addr.reverse` itself has no resolver and `reverse` has the ENSIP-19
  default resolver (`0xa7d6…0dcf`), so the walk falls back to the address's
  DEFAULT primary name exactly as ENSIP-19 prescribes, with no special case here.
* Base (ENSIP-19, coin type `0x80002105`): the chain's `L2ReverseRegistrar`
  (`0x0000…1664` on Base), `nameForAddr(addr)`, then the default name on L1 when
  it is empty — the same two reads L1's `ChainReverseResolver` performs through
  its gateway. That L1 path reverted on 2026-09-24 (`0xeb57ceb9`, not an error of
  its own ABI), so the read goes to Base directly; the trust is the consumer's
  Base RPC, the same as for the registrar.
* Then the name is resolved FORWARD, and it is only returned if it points back
  to the address (for Base, with `addr(node, 0x80002105)`, as the universal
  resolver of ENSIP-19 does). Execution Market did this for L1
  (`client.py:193-203`); uvdweb did not. And a name that is not in ENSIP-15 normal
  form is never shown, even if it resolves: that is how look-alikes get in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from ..name_models import NameErrorCode, NameSystem
from . import _abi
from ._hash import (
    ZERO_ADDRESS,
    dns_encode,
    labelhash,
    namehash,
    selector,
    to_checksum_address,
)
from ._normalize import classify, ensip15_is_normalized, too_long
from ._proto import BASE, ETHEREUM, Call, Outcome, Reverted, Step, Unavailable, ccip_call

#: The ENS registry, the same address on every chain ENS deployed it to.
REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
#: The `.eth` BaseRegistrar on L1 (`nameExpires(uint256)`).
ETH_REGISTRAR = "0x57f1887a8BF19b14fC0dF6Fd9B2acc9Af147eA85"
#: The Basenames BaseRegistrar on Base (`nameExpires(uint256)`); `jesse` read
#: `2510959025` (year 2049) on 2026-09-24.
BASENAMES_REGISTRAR = "0x03c4738Ee98aE44591e1A4A4F3CaB6641d95DD9a"
#: ENS's L2ReverseRegistrar on Base: `nameForAddr(jesse's wallet)` read
#: `jesse.base.eth` on 2026-09-24.
BASE_REVERSE_REGISTRAR = "0x0000000000D8e504002cC26E3Ec46D81971C1664"

#: ENSIP-11/19: an EVM chain's coin type is `0x80000000 | chainId`.
COIN_TYPE_ETH = 60
COIN_TYPE_BASE = 0x80000000 | 8453

_EXTENDED = bytes.fromhex("9061b923")
_SEL_RESOLVER = selector("resolver(bytes32)")
_SEL_SUPPORTS = selector("supportsInterface(bytes4)")
_SEL_RESOLVE = selector("resolve(bytes,bytes)")
_SEL_ADDR = selector("addr(bytes32)")
_SEL_ADDR_COIN = selector("addr(bytes32,uint256)")
_SEL_NAME = selector("name(bytes32)")
_SEL_TEXT = selector("text(bytes32,string)")
_SEL_NAME_EXPIRES = selector("nameExpires(uint256)")
_SEL_NAME_FOR_ADDR = selector("nameForAddr(address)")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_expiry(name: str, system: str, now: float) -> Step[None]:
    """`not_found` / `expired` for a lapsed second-level registration."""
    labels = name.split(".")
    if system == NameSystem.ENS and len(labels) >= 2:
        chain, registrar, label = ETHEREUM, ETH_REGISTRAR, labels[-2]
        owner = f"{label}.eth"
    elif system == NameSystem.BASENAMES and len(labels) >= 3:
        chain, registrar, label = BASE, BASENAMES_REGISTRAR, labels[-3]
        owner = f"{label}.base.eth"
    else:
        return
    try:
        out: bytes = yield Call(chain, registrar, _SEL_NAME_EXPIRES + labelhash(label))
    except Reverted:
        # SDK-7 (round 1 of PR 7, from describe-net's refuter): `nameExpires`
        # CANNOT revert. In ENS's BaseRegistrarImplementation it is `return
        # expiries[id]` and in Basenames' BaseRegistrar the getter of a public
        # mapping — for a label never registered both answer 0, and the recording
        # of `0xultravioletadao.eth` (2026-09-24, mainnet) got exactly 0x00…0. A
        # revert here is an RPC that reverts everything, and it used to come out
        # as `not_found` with `verified_onchain=True`. Mutation DN.
        raise Unavailable(f"the {chain} RPC reverted nameExpires, which cannot revert") from None
    try:
        (expires,) = _abi.decode(["uint256"], out)
    except _abi.AbiError:
        raise Outcome(
            NameErrorCode.NOT_FOUND, f"the registrar did not answer for {owner}"
        ) from None
    if expires == 0:
        raise Outcome(NameErrorCode.NOT_FOUND, f"{owner} is not registered")
    if expires <= now:
        raise Outcome(NameErrorCode.EXPIRED, f"{owner} expired on {_iso(expires)}")


def find_resolver(name: str) -> Step[Tuple[Optional[str], bool]]:
    """ENSIP-10: the resolver of the name or of its closest ancestor."""
    labels = name.split(".")
    for i in range(len(labels)):
        node = namehash(".".join(labels[i:]))
        out: bytes = yield Call(ETHEREUM, REGISTRY, _SEL_RESOLVER + node)
        try:
            (resolver,) = _abi.decode(["address"], out)
        except _abi.AbiError:
            raise Outcome(NameErrorCode.NOT_FOUND, "the registry answered garbage") from None
        if resolver != ZERO_ADDRESS:
            return resolver, i == 0
    return None, False


def _supports_extended(resolver: str) -> Step[bool]:
    try:
        out: bytes = yield Call(ETHEREUM, resolver, _SEL_SUPPORTS + _EXTENDED.ljust(32, b"\x00"))
        (flag,) = _abi.decode(["bool"], out)
    except (Reverted, _abi.AbiError):
        return False
    return bool(flag)


def resolve_record(name: str, inner: bytes) -> Step[bytes]:
    """The ABI-encoded answer of `inner` (a resolver call) for `name`."""
    resolver, exact = yield from find_resolver(name)
    if resolver is None:
        raise Outcome(NameErrorCode.NOT_FOUND, f"no resolver is set for {name} or any parent")
    extended = yield from _supports_extended(resolver)
    try:
        if extended:
            try:
                wire = dns_encode(name)
            except ValueError:
                raise Outcome(
                    NameErrorCode.INVALID_NAME, "a label is too long to DNS-encode"
                ) from None
            out: bytes = yield from ccip_call(
                ETHEREUM, resolver, _SEL_RESOLVE + _abi.encode(["bytes", "bytes"], [wire, inner])
            )
            (answer,) = _abi.decode(["bytes"], out)
            result: bytes = answer
            return result
        if not exact:
            raise Outcome(
                NameErrorCode.NOT_FOUND,
                f"{name} has no resolver of its own and its parent's "
                "does not do wildcards (ENSIP-10)",
            )
        direct: bytes = yield Call(ETHEREUM, resolver, inner)
        return direct
    except Reverted as rev:
        raise Outcome(
            NameErrorCode.NOT_FOUND,
            f"the resolver refused the lookup ({rev.data[:4].hex() or 'no data'})",
        ) from None
    except _abi.AbiError:
        raise Outcome(NameErrorCode.NOT_FOUND, "the resolver answered garbage") from None


def forward(name: str, system: str, now: float, coin_type: int = COIN_TYPE_ETH) -> Step[str]:
    """The EIP-55 address `name` points to for `coin_type`. Raises `Outcome`."""
    yield from check_expiry(name, system, now)
    node = namehash(name)
    if coin_type == COIN_TYPE_ETH:
        out = yield from resolve_record(name, _SEL_ADDR + node)
        try:
            (address,) = _abi.decode(["address"], out)
        except _abi.AbiError:
            # An empty answer is what a wildcard resolver gives for "not set".
            raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no address") from None
    else:
        out = yield from resolve_record(
            name, _SEL_ADDR_COIN + _abi.encode(["bytes32", "uint256"], [node, coin_type])
        )
        try:
            (raw,) = _abi.decode(["bytes"], out)
        except _abi.AbiError:
            raise Outcome(NameErrorCode.NOT_FOUND, f"{name} has no address") from None
        if len(raw) != 20:
            raise Outcome(
                NameErrorCode.NOT_FOUND, f"{name} has no address for coin type {coin_type:#x}"
            )
        address = "0x" + raw.hex()
    if address == ZERO_ADDRESS:
        # 🔴 "Not set", never a destination. Paying it burns the funds.
        raise Outcome(NameErrorCode.NOT_FOUND, f"{name} points to the zero address (not set)")
    return to_checksum_address(address)


def text(name: str, system: str, now: float, key: str) -> Step[Optional[str]]:
    """ENSIP-5 text record. `None` = the name exists and the key is not set."""
    yield from check_expiry(name, system, now)
    out = yield from resolve_record(
        name, _SEL_TEXT + _abi.encode(["bytes32", "string"], [namehash(name), key])
    )
    if not out:
        return None
    try:
        (value,) = _abi.decode(["string"], out)
    except _abi.AbiError:
        raise Outcome(NameErrorCode.NOT_FOUND, "the resolver answered garbage") from None
    return value or None


def _reverse_name_l1(reverse_name: str) -> Step[Optional[str]]:
    try:
        out = yield from resolve_record(reverse_name, _SEL_NAME + namehash(reverse_name))
        (name,) = _abi.decode(["string"], out)
    except Outcome as outcome:
        if outcome.code == NameErrorCode.NOT_FOUND:
            return None
        raise
    except _abi.AbiError:
        return None
    return name or None


def claimed_l1(address: str) -> Step[Optional[str]]:
    """The L1 primary name `address` CLAIMS (default fallback included). Unconfirmed."""
    name: Optional[str] = yield from _reverse_name_l1(f"{address[2:].lower()}.addr.reverse")
    return name


def claimed_base(address: str) -> Step[Optional[str]]:
    """The Base primary name `address` CLAIMS (ENSIP-19). Unconfirmed."""
    try:
        out: bytes = yield Call(
            BASE, BASE_REVERSE_REGISTRAR, _SEL_NAME_FOR_ADDR + _abi.encode(["address"], [address])
        )
        (name,) = _abi.decode(["string"], out)
    except (Reverted, _abi.AbiError):
        name = ""
    if name:
        result: Optional[str] = name
        return result
    default: Optional[str] = yield from _reverse_name_l1(f"{address[2:].lower()}.default.reverse")
    return default


def confirm(claimed: str, address: str, now: float, coin_type: int) -> Step[str]:
    """Resolve `claimed` forward; return it only if it points back to `address`.

    Raises `Outcome(reverse_mismatch)` — or `expired`, which says more — and
    lets `Unavailable` through: a name that could not be checked is not shown.
    """
    if too_long(claimed):
        # The owner of the address writes this string; ENSIP-15 over 900 KB of
        # combining marks took 67.5 s (R2). Refused before normalizing. Mutation DU.
        raise Outcome(NameErrorCode.REVERSE_MISMATCH, "the reverse record is too long")
    if not ensip15_is_normalized(claimed):
        raise Outcome(NameErrorCode.REVERSE_MISMATCH, "the reverse record is not a normalized name")
    found = classify(claimed)
    if found.error or found.system not in (
        NameSystem.ENS,
        NameSystem.BASENAMES,
        NameSystem.ENS_DNS,
    ):
        raise Outcome(NameErrorCode.REVERSE_MISMATCH, "the reverse record is not an ENS name")
    try:
        pointed = yield from forward(claimed, found.system or NameSystem.ENS, now, coin_type)
    except Outcome as outcome:
        if outcome.code == NameErrorCode.EXPIRED:
            raise
        raise Outcome(
            NameErrorCode.REVERSE_MISMATCH, "the reverse record names a name that does not resolve"
        ) from None
    if pointed.lower() != address.lower():
        raise Outcome(
            NameErrorCode.REVERSE_MISMATCH, "the reverse record names a name that points elsewhere"
        )
    return claimed
