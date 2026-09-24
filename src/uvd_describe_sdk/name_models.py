"""What a name resolution RETURNS — the contract, with no resolver attached.

This module is deliberately light: it imports nothing beyond the standard
library, so `DescribeClient.names` (the HTTP layer over `api.describe.net`) can
return the very same types without dragging in the `names` extra. The resolver
that fills them on-chain lives in `uvd_describe_sdk.names` and needs
`pip install uvd-describe-sdk[names]`.

════════════════════════════════════════════════════════════════════════════
WHY A RESULT OBJECT AND NOT `Optional[str]`
════════════════════════════════════════════════════════════════════════════
The three resolvers this replaces (measured 2026-09-24) all returned something
shaped like `Optional[str]`, and each lost a different fact on the way:

  * Execution Market (`mcp_server/integrations/ens/client.py`) cached the
    exception text as the answer and told "the RPC timed out" apart from "the
    name does not exist" only by reading prose.
  * karma-hello (`infrastructure/domain_resolver.py`) returned `None` for a
    timeout, for a missing name and for the Avvy HTTP API answering 503 — three
    facts, one value.
  * uvdweb (`src/components/WalletConnect.js`) swallowed every failure into "no
    name" with an empty `catch`.

It is R1 of this SDK in another place: `None` has to mean ONE thing. Here
`address is None` means "there is no address to give you", and `error` says why,
as a CODE you branch on (`NameErrorCode`), never as text:

    error is None and address  → resolved, and verified
    not_found                  → the name does not exist or points nowhere.
                                 An ANSWER, never an exception.
    invalid_name               → the input is not a valid name for its system
                                 (ENSIP-15 for the ENS family). No network used.
    reverse_mismatch           → a reverse record exists but its name does not
                                 resolve back to the address. The name is NOT
                                 returned: a reverse record is written by
                                 whoever owns the ADDRESS, and anybody can
                                 claim `vitalik.eth` for their own wallet.
    expired                    → the name exists but its registration lapsed.
                                 The records are still on-chain and resolve to
                                 the old address; that is precisely why it is
                                 not returned.
    unsupported_system         → a naming system this SDK does not resolve (yet),
                                 or one disabled by configuration.
    rpc_unavailable            → it could not ask: no RPC configured for the
                                 chain, a transport failure, the hard timeout, a
                                 gateway that did not answer. The only error that
                                 is never cached.

🔴 `address` is never the zero address. A resolver that answers
`0x0000000000000000000000000000000000000000` is saying "not set", and a payment
sent there is burned. It comes out as `not_found`.

════════════════════════════════════════════════════════════════════════════
`verified_onchain` — WHO VOUCHES FOR THE ADDRESS
════════════════════════════════════════════════════════════════════════════
`True` when this process read the answer from the chain itself (an `eth_call`
through the consumer's own RPC, CCIP-Read included: the gateway's answer is
verified by the resolver contract in the callback, ENSIP-10 / EIP-3668).
`False` when it came over an HTTP API that did the reading for us — today, only
`DescribeClient.names` against `api.describe.net`. karma-hello resolved `.avax`
through `api.avvy.domains` with nothing checked on-chain (and that API answered
503); this flag is how that case becomes visible instead of silent.

A payment destination must demand `True`: `require_onchain_address(result)`
returns the address only then, and raises `NameNotVerifiedError` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple


class NameErrorCode:
    """The six `error` codes of a name result. Constants, not an `Enum`.

    Same reasoning as `CaveatCode`: `NameResolution.error` is typed `str`, so a
    code added later arrives whole instead of breaking the parse. You compare
    against these constants so a typo cannot silently never match.
    """

    NOT_FOUND = "not_found"
    INVALID_NAME = "invalid_name"
    REVERSE_MISMATCH = "reverse_mismatch"
    EXPIRED = "expired"
    UNSUPPORTED_SYSTEM = "unsupported_system"
    RPC_UNAVAILABLE = "rpc_unavailable"

    def __init__(self) -> None:  # pragma: no cover - a namespace, never built
        raise TypeError("NameErrorCode is a namespace of constants")


#: Every code a name result can carry today.
NAME_ERROR_CODES: FrozenSet[str] = frozenset(
    {
        NameErrorCode.NOT_FOUND,
        NameErrorCode.INVALID_NAME,
        NameErrorCode.REVERSE_MISMATCH,
        NameErrorCode.EXPIRED,
        NameErrorCode.UNSUPPORTED_SYSTEM,
        NameErrorCode.RPC_UNAVAILABLE,
    }
)


class NameSystem:
    """Which naming system answered (or was asked). Constants, not an `Enum`.

    * `ENS` — ENS on Ethereum L1: `.eth` and its subnames (wildcard and
      CCIP-Read included, ENSIP-10).
    * `BASENAMES` — `*.base.eth`, resolved through L1 + CCIP-Read, with the
      registration checked on Base and the primary name read on Base (ENSIP-19).
    * `ENS_DNS` — a DNS name imported into ENS (DNSSEC / the offchain DNS
      resolver), resolved through the same L1 registry.
    * `UNSTOPPABLE` — Unstoppable Domains (UNS), read on-chain on Polygon and L1.
    * `AVVY` — Avvy Domains (`.avax`), read on-chain on Avalanche C-Chain.
    * `SNS` — Solana Name Service (`.sol`). Detected, NOT resolved: see
      `uvd_describe_sdk.names` for why it is `unsupported_system` today.
    """

    ENS = "ens"
    BASENAMES = "basenames"
    ENS_DNS = "ens-dns"
    UNSTOPPABLE = "unstoppable"
    AVVY = "avvy"
    SNS = "sns"

    def __init__(self) -> None:  # pragma: no cover - a namespace, never built
        raise TypeError("NameSystem is a namespace of constants")


#: Every system this SDK can NAME. Being here does not mean it resolves: `SNS`
#: is detected so the answer can say `unsupported_system` instead of guessing.
KNOWN_NAME_SYSTEMS: FrozenSet[str] = frozenset(
    {
        NameSystem.ENS,
        NameSystem.BASENAMES,
        NameSystem.ENS_DNS,
        NameSystem.UNSTOPPABLE,
        NameSystem.AVVY,
        NameSystem.SNS,
    }
)


class NameFamily:
    """The ADDRESS family of the result: which kind of wallet it names.

    describe.net searches an `evm_wallet` and a `solana_wallet` differently
    (`site/agent.html:1030-1071`), so a consumer must know which one it got
    without parsing the address.
    """

    EVM = "evm"
    SOLANA = "solana"

    def __init__(self) -> None:  # pragma: no cover - a namespace, never built
        raise TypeError("NameFamily is a namespace of constants")


@dataclass(frozen=True)
class NameResolution:
    """The answer of `resolve()` and `reverse()`.

    For `resolve(name)`: `input` is the name as given, `normalized` the name as
    it was looked up, `address` the address it points to.

    For `reverse(address)`: `input` is the address as given, `normalized` the
    PRIMARY NAME (already confirmed forward, and normalized), `address` the
    address in its canonical form (EIP-55 for EVM).

    `tried` lists the systems that were asked, in order — it is what lets a
    search box say "we looked in ENS and Unstoppable" instead of "not found".
    `detail` is prose for a human; branch on `error`, never on `detail`.
    """

    input: str
    normalized: Optional[str]
    address: Optional[str]
    family: Optional[str]
    system: Optional[str]
    verified_onchain: bool
    cached: bool = False
    error: Optional[str] = None
    tried: Tuple[str, ...] = ()
    detail: Optional[str] = None
    #: The body as the HTTP API sent it. `None` for an on-chain result.
    raw: Optional[Dict[str, Any]] = field(default=None, compare=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """The JSON shape — the one `api.describe.net/v1/names` serves.

        `raw` is left out: it is what we received, not what we answer.
        """
        return {
            "input": self.input,
            "normalized": self.normalized,
            "address": self.address,
            "family": self.family,
            "system": self.system,
            "verified_onchain": self.verified_onchain,
            "cached": self.cached,
            "error": self.error,
            "tried": list(self.tried),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class NameRecord:
    """The answer of `text()` and `avatar()`.

    `value is None and error is None` is an ANSWER: the name exists and that
    record is not set. `error == "not_found"` is a different fact: the NAME is
    not there. The two are never folded into one, for the same reason `None` is
    never `0` in this SDK.

    For `avatar()`, `value` is the final URL (ENSIP-12: `ipfs://`, `ar://` and
    NFT avatars already turned into something a browser can load) and
    `raw_value` is the record exactly as it is stored.
    """

    input: str
    normalized: Optional[str]
    key: str
    value: Optional[str]
    family: Optional[str]
    system: Optional[str]
    verified_onchain: bool
    cached: bool = False
    error: Optional[str] = None
    tried: Tuple[str, ...] = ()
    detail: Optional[str] = None
    raw_value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input,
            "normalized": self.normalized,
            "key": self.key,
            "value": self.value,
            "family": self.family,
            "system": self.system,
            "verified_onchain": self.verified_onchain,
            "cached": self.cached,
            "error": self.error,
            "tried": list(self.tried),
            "detail": self.detail,
            "raw_value": self.raw_value,
        }


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value != "" else None


def parse_name_resolution(body: Any) -> NameResolution:
    """Read the `api.describe.net/v1/names` body into a `NameResolution`.

    🔴 `verified_onchain` comes out **False whatever the body says**. The server
    may well have verified it on-chain, but THIS process did not: it received an
    HTTP answer, and an HTTP answer is exactly what the flag exists to tell apart
    from a chain read. What the server claimed stays in `raw`.

    `cached` comes out False for the same reason: it describes this process's
    cache, and this process cached nothing. A body that is not a JSON object is
    `ValueError` — the client turns it into its R4 taxonomy.
    """
    if not isinstance(body, dict):
        raise ValueError("a names answer must be a JSON object")
    tried_raw = body.get("tried")
    tried: Tuple[str, ...] = (
        tuple(t for t in tried_raw if isinstance(t, str)) if isinstance(tried_raw, list) else ()
    )
    address = _opt_str(body.get("address"))
    if address is not None and address.lower() == "0x" + "0" * 40:
        # R1's cousin: a server that sends the zero address sends "not set".
        address = None
    given = body.get("input")
    return NameResolution(
        input=given if isinstance(given, str) else "",
        normalized=_opt_str(body.get("normalized")),
        address=address,
        family=_opt_str(body.get("family")),
        system=_opt_str(body.get("system")),
        verified_onchain=False,
        cached=False,
        error=_opt_str(body.get("error")),
        tried=tried,
        detail=_opt_str(body.get("detail")),
        raw=body,
    )


class NameNotVerifiedError(Exception):
    """`require_onchain_address()` refused: this result cannot be a payee.

    🔴 **NOT a `DescribeError`**, for the reason `CaveatsNotComputedError` gives:
    consumers wrap `DescribeError` in their own fail-open, and a refusal caught
    there would degrade into "describe is down" — the one outcome a payment gate
    must never read as "go ahead".

    `reason` is the code to branch on: the result's own `error`, or
    `"not_verified_onchain"` when the address came over an HTTP API.
    """

    #: A class constant that interpolates nothing (same guard as `errors.py`).
    recovery = (
        "Resolve the name with `uvd_describe_sdk.names.NameResolver` against your "
        "own RPC: it reads the chain and returns `verified_onchain=True`. An HTTP "
        "answer (`DescribeClient.names`) is fine to SHOW a name, never to pay one."
    )

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def require_onchain_address(result: NameResolution) -> str:
    """The address, only if it may receive a payment. Raises otherwise.

    Three conditions, all required: no `error`, an `address`, and
    `verified_onchain is True`. The last one is the point: an address this
    process did not read from the chain is somebody else's claim.
    """
    if not isinstance(result, NameResolution):
        raise TypeError(
            f"require_onchain_address() takes a NameResolution, not {type(result).__name__}"
        )
    if result.error is not None or result.address is None:
        raise NameNotVerifiedError(
            f"{result.input!r} has no payable address (error={result.error})",
            reason=result.error or NameErrorCode.NOT_FOUND,
        )
    if result.verified_onchain is not True:
        raise NameNotVerifiedError(
            f"{result.input!r} resolved over an HTTP API, not on-chain",
            reason="not_verified_onchain",
        )
    return result.address
