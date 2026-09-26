"""`NameResolver` — the public face: dispatch, cache, and the ONE engine in two flavours."""

from __future__ import annotations

import re
import ssl
import threading
import time
from dataclasses import dataclass, replace
from typing import (
    Callable,
    FrozenSet,
    Generic,
    Hashable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    cast,
)

import httpx

from ..name_models import (
    KNOWN_NAME_SYSTEMS,
    NameErrorCode,
    NameFamily,
    NameRecord,
    NameResolution,
    NameSystem,
)
from ..version import default_user_agent
from . import _avvy, _ens, _uns
from ._avatar import NoAvatar, nft_image, nft_reference, plain_url
from ._cache import NameCache
from ._hash import ZERO_ADDRESS, is_hex_address, to_checksum_address
from ._normalize import (
    ENS_FAMILY,
    FAMILY_OF,
    Classified,
    InvalidNameError,
    classify,
    too_long,
)
from ._proto import (
    AVALANCHE,
    BASE,
    ETHEREUM,
    Outcome,
    Reverted,
    Step,
    Unavailable,
    run_blocking,
    run_bounded,
)

#: 10 s for the WHOLE call (a reverse may walk four systems and two CCIP hops).
#: karma-hello's production setting (`config.yaml:2886-2898`,
#: `timeout_seconds: 10`), and far enough under the 29 s API Gateway ceiling
#: that a lookup inside a describe-net request never eats the request.
DEFAULT_TIMEOUT_S = 10.0

#: Every system this SDK resolves today. `sns` is detected but not here: see
#: `SNS_UNSUPPORTED_DETAIL`.
DEFAULT_SYSTEMS: Tuple[str, ...] = (
    NameSystem.ENS,
    NameSystem.BASENAMES,
    NameSystem.ENS_DNS,
    NameSystem.UNSTOPPABLE,
    NameSystem.AVVY,
)

#: Where `ipfs://` avatars are served from. Configurable; not a secret.
DEFAULT_IPFS_GATEWAY = "https://ipfs.io"

#: Why `.sol` is `unsupported_system`, in words — branch on the code, not this.
SNS_UNSUPPORTED_DETAIL = (
    "SNS is not resolved by this SDK yet: on 2026-09-24 the official sns-sdk "
    "(536f0cb) is migrating .sol to a new registry (SRS) — legacy .sol "
    "resolution stops at finalized slot 452,825,395 (about 2026-10-15) and the "
    "SRS path is still disabled upstream"
)

#: The chains each system needs to answer a reverse lookup at all. A system
#: whose chains the consumer did not configure is skipped (and named in
#: `detail`), not failed: leaving Avalanche out is a choice, not an outage.
#: But «no primary name» is an answer only when SOME system gave it: a reverse
#: that could ask none is `rpc_unavailable`, never `not_found` (SDK-6). And a
#: skipped system still RANKS: it might hold the primary name, so a lower one's
#: name is not given (`rpc_unavailable`), and a negative is `verified_onchain`
#: only if nothing was skipped (round 1 of PR 7).
_REVERSE_NEEDS = {
    NameSystem.ENS: (ETHEREUM,),
    NameSystem.BASENAMES: (BASE, ETHEREUM),
    NameSystem.UNSTOPPABLE: (ETHEREUM,),
    NameSystem.AVVY: (AVALANCHE,),
}
_REVERSE_ORDER = (NameSystem.ENS, NameSystem.BASENAMES, NameSystem.UNSTOPPABLE, NameSystem.AVVY)
_CAIP2 = re.compile(r"(eip155:[0-9]+|solana:[1-9A-HJ-NP-Za-km-z]{1,64})")

R = TypeVar("R", NameResolution, NameRecord)


@dataclass(frozen=True)
class _Plan(Generic[R]):
    """What one public call needs: an answer decided offline, or steps to run."""

    raw: str
    early: Optional[R]
    key: Optional[Hashable] = None
    steps: Optional[Callable[[float], Step[R]]] = None
    #: The answer when the deadline cancels the steps: `rpc_unavailable`, with
    #: whatever the steps had recorded (the systems a reverse already tried).
    timed_out: Optional[Callable[[str], R]] = None


class NameResolver:
    """The one name resolver of the stack: name → address, address → name.

        from uvd_describe_sdk import require_onchain_address
        from uvd_describe_sdk.names import NameResolver

        names = NameResolver(rpc={
            "eip155:1": ETH_RPC_URL,        # ENS, DNS names, UNS (L1)
            "eip155:8453": BASE_RPC_URL,    # Basenames, UNS on Base
            "eip155:137": POLYGON_RPC_URL,  # UNS
            "eip155:43114": AVAX_RPC_URL,   # Avvy
        })
        r = names.resolve_sync("jesse.base.eth")
        if r.error is None:
            pay_to = require_onchain_address(r)

    Every lookup has an async form and a `_sync` form, and they are ONE engine:
    the `_sync` form runs the async one on a private event loop, under the same
    hard deadline (`_proto.run_bounded` / `run_blocking`).
    🔴 The SDK carries NO RPC URL — a public default would be a shared,
    rate-limited endpoint nobody chose, and a keyed one would be a leaked key.
    A system whose chain is not in `rpc` answers `rpc_unavailable`. Proxies from
    the environment (`HTTP(S)_PROXY`, `NO_PROXY`) ARE honoured, as `DescribeClient`
    honours them — httpx's default; a `transport=` passed here replaces them.
    """

    def __init__(
        self,
        *,
        rpc: Mapping[str, str],
        systems: Sequence[str] = DEFAULT_SYSTEMS,
        cache: NameCache | bool = True,
        timeout: float = DEFAULT_TIMEOUT_S,
        ipfs_gateway: str = DEFAULT_IPFS_GATEWAY,
        user_agent: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        async_transport: Optional[httpx.AsyncBaseTransport] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            rpc: CAIP-2 chain id → JSON-RPC URL (`"eip155:1"`, `"eip155:8453"`,
                `"eip155:137"`, `"eip155:43114"`). The URLs never appear in a
                result, a log line or an exception: they often carry a key.
            systems: which systems to resolve (`NameSystem.*`). A name of a
                system left out answers `unsupported_system`.
            cache: `True` (a fresh `NameCache()`), `False` (none), or a
                `NameCache` to share or tune.
            timeout: seconds for the WHOLE call, hard, in both flavours. See
                `_proto.py`.
            transport / async_transport: for the tests (`httpx.MockTransport`).
                Both flavours run on the async engine, so the transport used is
                `async_transport` or, failing that, `transport` — which must then
                also be an `httpx.AsyncBaseTransport` (`MockTransport` is). A
                sync-only transport (`httpx.HTTPTransport`) is refused here, not
                at the first request. The `_sync` flavour runs each call on a
                fresh event loop: a transport passed here must not keep pooled
                connections from one call to the next (an in-memory one does not;
                the default one is built per call). Passing BOTH is accepted only
                if they are the same object: one engine, one transport — two
                different ones would leave one of them unused in silence.
            clock: wall clock (epoch seconds) for expiry checks, for the tests.
        """
        if transport is not None and not isinstance(transport, httpx.AsyncBaseTransport):
            raise ValueError(
                "transport= must also be an httpx.AsyncBaseTransport: the _sync "
                "variants run on the async engine (pass async_transport=)"
            )
        # Round 5 of PR #6: two different ones used to be accepted, and
        # `transport=` was ignored in silence, in BOTH flavours. Mutation CC.
        both = transport is not None and async_transport is not None
        if both and transport is not async_transport:
            raise ValueError(
                "pass ONE transport: both flavours run on the same engine, so "
                "transport= and async_transport= would be the same one"
            )
        engine_transport = async_transport if async_transport is not None else transport
        bad = [key for key in rpc if not isinstance(key, str) or not _CAIP2.fullmatch(key)]
        if bad:
            raise ValueError(
                f"rpc keys must be CAIP-2 chain ids like 'eip155:1'; got {sorted(map(str, bad))}"
            )
        unknown = [s for s in systems if s not in KNOWN_NAME_SYSTEMS]
        if unknown:
            raise ValueError(f"unknown naming systems: {unknown}")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._rpc = {key: url for key, url in rpc.items() if url}
        self._systems: FrozenSet[str] = frozenset(systems)
        self._cache: Optional[NameCache] = (
            cache if isinstance(cache, NameCache) else (NameCache() if cache else None)
        )
        self._timeout = timeout
        self._ipfs_gateway = ipfs_gateway
        self._user_agent = user_agent or default_user_agent("names")
        self._transport: Optional[httpx.AsyncBaseTransport] = engine_transport
        self._clock = clock
        # REENTRANT, and it is not a detail: `_async_client()` holds it while it
        # builds the client, and building the default one calls `_ssl_context()`,
        # which takes it again. With a plain `Lock` that was a self-deadlock —
        # `await resolve()` with the default transport hung forever (round 5 of
        # PR #6; py3.9/3.12/3.13), and no test ran the async flavour without a
        # `transport=`. Mutation CA.
        self._lock = threading.RLock()
        self._aclient: Optional[httpx.AsyncClient] = None
        self._ssl: Optional[ssl.SSLContext] = None
        #: Chains whose RPC already answered the right `eth_chainId` (once per
        #: chain and per resolver; see `_proto.py`). A mismatch is not stored:
        #: a misconfigured key keeps failing, loudly.
        self._verified_chains: Set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _new_client(self) -> httpx.AsyncClient:
        # httpx's defaults otherwise: environment proxies are honoured, as in
        # `DescribeClient`, unless a transport is passed (then httpx mounts none).
        if self._transport is not None:
            return httpx.AsyncClient(
                headers={"User-Agent": self._user_agent},
                transport=self._transport,
                follow_redirects=False,
            )
        return httpx.AsyncClient(
            headers={"User-Agent": self._user_agent},
            follow_redirects=False,
            verify=self._ssl_context(),
        )

    def _ssl_context(self) -> ssl.SSLContext:
        """ONE TLS context per resolver. Measured 2026-09-24: `AsyncClient()`
        builds one each time and that cost 0.34 s (loading the CA bundle); with
        this one reused, 0.3 ms. The `_sync` flavour opens a client per call."""
        with self._lock:
            if self._ssl is None:
                self._ssl = httpx.create_ssl_context()
            return self._ssl

    def _async_client(self) -> httpx.AsyncClient:
        """The client of the async flavour, kept for the caller's event loop."""
        with self._lock:
            if self._aclient is None:
                self._aclient = self._new_client()
            return self._aclient

    def close(self) -> None:
        """Nothing to release for the `_sync` flavour: each call opens its own
        client on its own loop and closes it before returning. Kept so `with
        NameResolver(...)` keeps working; the async client is `aclose()`'s."""

    async def aclose(self) -> None:
        if self._aclient is not None:
            await self._aclient.aclose()
            self._aclient = None

    def __enter__(self) -> NameResolver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def __aenter__(self) -> NameResolver:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @property
    def cache(self) -> Optional[NameCache]:
        return self._cache

    # ------------------------------------------------------------------
    # Pure: no network
    # ------------------------------------------------------------------

    def detect(self, name: str) -> Optional[str]:
        """The system a name belongs to by its suffix (`NameSystem.*`), or `None`."""
        return classify(name).system

    def normalize(self, name: str) -> str:
        """The name as it is looked up. Raises `InvalidNameError`.

        A name under a colliding TLD (`UNS_ICANN_COLLISIONS`) is well formed and
        is returned: it is its SYSTEM that is ambiguous, which `resolve()` answers
        with `unsupported_system`.
        """
        found = classify(name)
        if found.error == NameErrorCode.INVALID_NAME or found.normalized is None:
            raise InvalidNameError(found.detail or "not a valid name")
        return found.normalized

    # ------------------------------------------------------------------
    # The four lookups, each in two flavours over one set of steps
    # ------------------------------------------------------------------

    def resolve_sync(self, name: str) -> NameResolution:
        """Name → address. `not_found` is an answer, never an exception."""
        return self._sync(self._plan_resolve(name))

    async def resolve(self, name: str) -> NameResolution:
        """Name → address. `not_found` is an answer, never an exception."""
        return await self._async(self._plan_resolve(name))

    def reverse_sync(self, address: str) -> NameResolution:
        """Address → primary name, CONFIRMED forward. See `name_models`."""
        return self._sync(self._plan_reverse(address))

    async def reverse(self, address: str) -> NameResolution:
        """Address → primary name, CONFIRMED forward. See `name_models`."""
        return await self._async(self._plan_reverse(address))

    def text_sync(self, name: str, key: str) -> NameRecord:
        """A text record: ENSIP-5 for the ENS family; UNS and Avvy records too."""
        return self._sync(self._plan_record(name, key, avatar=False))

    async def text(self, name: str, key: str) -> NameRecord:
        """A text record: ENSIP-5 for the ENS family; UNS and Avvy records too."""
        return await self._async(self._plan_record(name, key, avatar=False))

    def avatar_sync(self, name: str) -> NameRecord:
        """The avatar as a final URL (ENSIP-12), NFT ownership checked."""
        return self._sync(self._plan_record(name, "avatar", avatar=True))

    async def avatar(self, name: str) -> NameRecord:
        """The avatar as a final URL (ENSIP-12), NFT ownership checked."""
        return await self._async(self._plan_record(name, "avatar", avatar=True))

    # ------------------------------------------------------------------
    # The shared machinery
    # ------------------------------------------------------------------

    def _sync(self, plan: _Plan[R]) -> R:
        """The async engine, run from sync code: `_bounded` on a private loop."""
        if plan.early is not None or plan.steps is None:
            return cast(R, plan.early)
        hit = self._cached(plan)
        if hit is not None:
            return hit
        started = time.monotonic()  # the budget counts from HERE, client included

        async def one_call() -> R:
            client = self._new_client()
            try:
                return await self._bounded(plan, client, started)
            finally:
                await client.aclose()

        return self._store(plan, run_blocking(one_call))

    async def _async(self, plan: _Plan[R]) -> R:
        if plan.early is not None or plan.steps is None:
            return cast(R, plan.early)
        hit = self._cached(plan)
        if hit is not None:
            return hit
        started = time.monotonic()
        return self._store(plan, await self._bounded(plan, self._async_client(), started))

    async def _bounded(self, plan: _Plan[R], client: httpx.AsyncClient, started: float) -> R:
        """The steps under THE deadline; its expiry is `rpc_unavailable`."""
        assert plan.steps is not None and plan.timed_out is not None
        left = self._timeout - (time.monotonic() - started)
        budget_gone = f"the {self._timeout:g}s budget of this call ran out"
        if left <= 0:
            return plan.timed_out(budget_gone)
        try:
            return await run_bounded(
                plan.steps(self._clock()),
                rpc=self._rpc,
                client=client,
                timeout=left,
                verified_chains=self._verified_chains,
            )
        except Unavailable:
            return plan.timed_out(budget_gone)

    def _cached(self, plan: _Plan[R]) -> Optional[R]:
        if self._cache is None or plan.key is None:
            return None
        hit = self._cache.get(plan.key)
        if hit is None:
            return None
        return cast(R, replace(hit, input=plan.raw, cached=True))

    def _store(self, plan: _Plan[R], result: R) -> R:
        if self._cache is not None and plan.key is not None:
            self._cache.put(plan.key, result)
        return result

    def _refusal(self, found: Classified) -> Optional[Tuple[str, Optional[str]]]:
        """`(code, detail)` decided without the network, or `None` to go ask."""
        if found.error:
            return found.error, found.detail
        if found.system == NameSystem.SNS:
            return NameErrorCode.UNSUPPORTED_SYSTEM, SNS_UNSUPPORTED_DETAIL
        if found.system not in self._systems:
            return NameErrorCode.UNSUPPORTED_SYSTEM, f"{found.system} is disabled in this resolver"
        return None

    # -- resolve ---------------------------------------------------------

    def _plan_resolve(self, raw: str) -> _Plan[NameResolution]:
        found = classify(raw)
        system = found.system or ""
        template = NameResolution(
            input=raw,
            normalized=None if found.error else found.normalized,
            address=None,
            family=FAMILY_OF.get(system),
            system=found.system,
            verified_onchain=False,
            tried=(system,) if system else (),
        )
        refusal = self._refusal(found)
        if refusal is not None:
            code, detail = refusal
            return _Plan(raw, replace(template, error=code, detail=detail, tried=()))
        return _Plan(
            raw,
            None,
            ("resolve", found.system, found.normalized),
            lambda now: self._resolve_steps(template, now),
            lambda detail: replace(template, error=NameErrorCode.RPC_UNAVAILABLE, detail=detail),
        )

    def _resolve_steps(self, template: NameResolution, now: float) -> Step[NameResolution]:
        system = template.system or ""
        name = template.normalized or ""
        try:
            if system in ENS_FAMILY:
                address = yield from _ens.forward(name, system, now)
            elif system == NameSystem.UNSTOPPABLE:
                address = yield from _uns.forward(name)
            else:
                address = yield from _avvy.forward(name, now)
        except Outcome as outcome:
            return replace(
                template,
                error=outcome.code,
                detail=outcome.detail,
                verified_onchain=outcome.code != NameErrorCode.UNSUPPORTED_SYSTEM,
            )
        except Unavailable as failure:
            return replace(template, error=NameErrorCode.RPC_UNAVAILABLE, detail=str(failure))
        except Reverted as rev:
            return replace(
                template, error=NameErrorCode.NOT_FOUND, detail=str(rev), verified_onchain=True
            )
        return replace(template, address=address, verified_onchain=True)

    # -- reverse ---------------------------------------------------------

    def _plan_reverse(self, raw: str) -> _Plan[NameResolution]:
        text = raw.strip() if isinstance(raw, str) else ""
        if not is_hex_address(text):
            return _Plan(
                raw,
                NameResolution(
                    input=raw,
                    normalized=None,
                    address=None,
                    family=None,
                    system=None,
                    verified_onchain=False,
                    error=NameErrorCode.INVALID_NAME,
                    detail="reverse() takes an EVM address (0x + 40 hex); "
                    "SNS reverse is not supported yet",
                ),
            )
        address = to_checksum_address(text)
        if address == ZERO_ADDRESS:
            return _Plan(
                raw,
                NameResolution(
                    input=raw,
                    normalized=None,
                    address=None,
                    family=NameFamily.EVM,
                    system=None,
                    verified_onchain=False,
                    error=NameErrorCode.NOT_FOUND,
                    detail="the zero address has no name",
                ),
            )
        if not any(system in self._systems for system in _REVERSE_ORDER):
            # Configuration, not an outage, and decided without the network —
            # what `resolve()` answers for a disabled system. Before SDK-6 this
            # was a `not_found` with nothing tried. Mutation DI.
            return _Plan(
                raw,
                NameResolution(
                    input=raw,
                    normalized=None,
                    address=address,
                    family=NameFamily.EVM,
                    system=None,
                    verified_onchain=False,
                    error=NameErrorCode.UNSUPPORTED_SYSTEM,
                    detail="no system that answers reverse lookups is enabled in this resolver",
                ),
            )
        # Shared with the steps, so an expired deadline still says what was tried.
        tried: List[str] = []

        def timed_out(detail: str) -> NameResolution:
            return NameResolution(
                input=raw,
                normalized=None,
                address=address,
                family=NameFamily.EVM,
                system=tried[-1] if tried else None,
                verified_onchain=False,
                error=NameErrorCode.RPC_UNAVAILABLE,
                tried=tuple(tried),
                detail=detail,
            )

        return _Plan(
            raw,
            None,
            ("reverse", address.lower()),
            lambda now: self._reverse_steps(raw, address, now, tried),
            timed_out,
        )

    def _reverse_steps(
        self, raw: str, address: str, now: float, tried: List[str]
    ) -> Step[NameResolution]:
        template = NameResolution(
            input=raw,
            normalized=None,
            address=address,
            family=NameFamily.EVM,
            system=None,
            verified_onchain=True,
        )
        skipped: List[str] = []
        refused: Optional[Outcome] = None
        for system in _REVERSE_ORDER:
            if system not in self._systems:
                continue
            missing = [c for c in _REVERSE_NEEDS[system] if c not in self._rpc]
            if missing:
                skipped.append(f"{system} (no RPC for {', '.join(missing)})")
                continue
            tried.append(system)
            try:
                name = yield from self._reverse_one(system, address, now)
            except Outcome as outcome:
                if (
                    outcome.code in (NameErrorCode.REVERSE_MISMATCH, NameErrorCode.EXPIRED)
                    and refused is None
                ):
                    refused = outcome
                continue
            except (Unavailable, Reverted) as failure:
                # Strict order: a system above that could not be asked might
                # hold the primary name, so a lower one's answer is not given.
                # `Reverted` belongs here too (round 2 of PR #6): the ENS
                # registry does not revert on `resolver()`, so a revert there is
                # an RPC that answers "execution reverted" to everything — it
                # could not be asked. Before, it escaped as a private exception.
                return replace(
                    template,
                    system=system,
                    verified_onchain=False,
                    error=NameErrorCode.RPC_UNAVAILABLE,
                    tried=tuple(tried),
                    detail=str(failure),
                )
            if name is None:
                continue
            if skipped:
                # Strict order, for a system above that was SKIPPED too (round 1
                # of PR 7, from describe-net's refuter, measured with a double
                # 2026-09-26): with no RPC for eip155:1, ENS, Basenames and UNS
                # were skipped and Avvy's name came out as the primary, with
                # `verified_onchain=True`. A skipped system might hold the
                # primary name exactly like one that could not be asked, so the
                # lower answer is not given — nor named. Mutation DM.
                return replace(
                    template,
                    verified_onchain=False,
                    error=NameErrorCode.RPC_UNAVAILABLE,
                    tried=tuple(tried),
                    detail="a system that ranks above could not be asked, so the primary "
                    f"name cannot be told; skipped: {', '.join(skipped)}",
                )
            return replace(
                template,
                normalized=name,
                system=classify(name).system or system,
                tried=tuple(tried),
            )
        note = f"; skipped: {', '.join(skipped)}" if skipped else ""
        # A negative is VERIFIED only when every enabled system was asked: with
        # one skipped, the name it might hold was never read (round 1 of PR 7:
        # no RPC for Base gave `not_found` with `verified_onchain=True`, and a
        # consumer cached it as the truth). The code stays; the flag says it.
        # Mutations DK (not_found) and DL (reverse_mismatch / expired).
        all_asked = not skipped
        if refused is not None:
            return replace(
                template,
                error=refused.code,
                verified_onchain=all_asked,
                tried=tuple(tried),
                detail=refused.detail + note,
            )
        if not tried:
            # SDK-6 (describe-net's review of its PR 62, 2026-09-25): every
            # system was skipped for want of an RPC. This used to be `not_found`
            # with `verified_onchain=False` — the one `not_found` that meant «I
            # do not know», and the cache kept it 60 s. `rpc_unavailable` is
            # what the code means («no RPC configured for the chain») and it is
            # never cached. Mutation DH.
            return replace(
                template,
                verified_onchain=False,
                error=NameErrorCode.RPC_UNAVAILABLE,
                detail="no system could be asked" + note,
            )
        return replace(
            template,
            error=NameErrorCode.NOT_FOUND,
            verified_onchain=all_asked,
            tried=tuple(tried),
            detail="no primary name" + note,
        )

    def _reverse_one(self, system: str, address: str, now: float) -> Step[Optional[str]]:
        """The confirmed primary name in ONE system, or `None` if it claims none."""
        if system in (NameSystem.ENS, NameSystem.BASENAMES):
            if system == NameSystem.ENS:
                claimed = yield from _ens.claimed_l1(address)
                coin_type = _ens.COIN_TYPE_ETH
            else:
                claimed = yield from _ens.claimed_base(address)
                coin_type = _ens.COIN_TYPE_BASE
            if claimed is None:
                return None
            confirmed: str = yield from _ens.confirm(claimed, address, now, coin_type)
            return confirmed
        if system == NameSystem.UNSTOPPABLE:
            chains = tuple(c for c in _uns.REVERSE_CHAINS if c in self._rpc)
            claimed = yield from _uns.claimed(address, chains)
        else:
            claimed = yield from _avvy.claimed(address)
        if claimed is None:
            return None
        if too_long(claimed):
            # Same cap as `_ens.confirm` (R2): refused before classifying. Mutation DV.
            raise Outcome(NameErrorCode.REVERSE_MISMATCH, "the reverse record is too long")
        found = classify(claimed)
        if found.error or found.system != system or found.normalized != claimed:
            raise Outcome(
                NameErrorCode.REVERSE_MISMATCH,
                "the reverse record is not a valid name of its system",
            )
        try:
            if system == NameSystem.UNSTOPPABLE:
                pointed = yield from _uns.forward(claimed)
            else:
                pointed = yield from _avvy.forward(claimed, now)
        except Outcome as outcome:
            if outcome.code == NameErrorCode.EXPIRED:
                raise
            raise Outcome(
                NameErrorCode.REVERSE_MISMATCH,
                "the reverse record names a name that does not resolve",
            ) from None
        if pointed.lower() != address.lower():
            raise Outcome(
                NameErrorCode.REVERSE_MISMATCH,
                "the reverse record names a name that points elsewhere",
            )
        return claimed

    # -- text and avatar -------------------------------------------------

    def _plan_record(self, raw: str, key: str, *, avatar: bool) -> _Plan[NameRecord]:
        found = classify(raw)
        system = found.system or ""
        template = NameRecord(
            input=raw,
            normalized=None if found.error else found.normalized,
            key=key,
            value=None,
            family=FAMILY_OF.get(system),
            system=found.system,
            verified_onchain=False,
            tried=(system,) if system else (),
        )
        refusal = self._refusal(found)
        if refusal is None and avatar and system not in ENS_FAMILY:
            refusal = (
                NameErrorCode.UNSUPPORTED_SYSTEM,
                "ENSIP-12 avatars are an ENS-family record",
            )
        if refusal is not None:
            code, detail = refusal
            return _Plan(raw, replace(template, error=code, detail=detail, tried=()))
        op = "avatar" if avatar else "text"
        return _Plan(
            raw,
            None,
            (op, found.system, found.normalized, key),
            lambda now: self._record_steps(template, now, avatar),
            lambda detail: replace(template, error=NameErrorCode.RPC_UNAVAILABLE, detail=detail),
        )

    def _record_steps(self, template: NameRecord, now: float, avatar: bool) -> Step[NameRecord]:
        system = template.system or ""
        name = template.normalized or ""
        stored: Optional[str] = None
        try:
            if system in ENS_FAMILY:
                value = yield from _ens.text(name, system, now, template.key)
            elif system == NameSystem.UNSTOPPABLE:
                value = yield from _uns.text(name, template.key)
            else:
                value = yield from _avvy.text(name, now, template.key)
            stored = value
            detail: Optional[str] = None
            if avatar and value is not None:
                value, detail = yield from self._avatar_url(name, system, now, value)
        except Outcome as outcome:
            return replace(
                template,
                error=outcome.code,
                detail=outcome.detail,
                raw_value=stored,
                verified_onchain=outcome.code != NameErrorCode.UNSUPPORTED_SYSTEM,
            )
        except Unavailable as failure:
            return replace(
                template, error=NameErrorCode.RPC_UNAVAILABLE, detail=str(failure), raw_value=stored
            )
        except Reverted as rev:
            return replace(
                template,
                error=NameErrorCode.NOT_FOUND,
                detail=str(rev),
                raw_value=stored,
                verified_onchain=True,
            )
        return replace(
            template, value=value, detail=detail, raw_value=stored, verified_onchain=True
        )

    def _avatar_url(
        self, name: str, system: str, now: float, record: str
    ) -> Step[Tuple[Optional[str], Optional[str]]]:
        url = plain_url(record, self._ipfs_gateway)
        if url is not None:
            return url, None
        if nft_reference(record) is None:
            return None, "the avatar record is not an ENSIP-12 URI"
        try:
            owner = yield from _ens.forward(name, system, now)
        except Outcome:
            return None, "the name has no address, so NFT ownership cannot be checked"
        try:
            image = yield from nft_image(record, owner, self._ipfs_gateway)
        except NoAvatar as why:
            return None, str(why)
        return image, None
