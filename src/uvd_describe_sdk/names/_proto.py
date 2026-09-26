"""The I/O seam: resolution logic as generators, and the ONE engine that runs it.

WHY GENERATORS (sans-IO)
------------------------
The contract asks for `resolve()` AND `resolve_sync()`. Execution Market is
async (`asyncio.to_thread` around web3), describe-net and KarmaKadabra are
sync, karma-hello is async. Writing the policy twice — once with `await`, once
without — is the shortest path to two resolvers that disagree (this repo's own
CLAUDE.md names the risk: "sin duplicar una línea de política"). So every
resolution step is a generator that YIELDS what it needs (`Call`: an `eth_call`
on a chain; `Fetch`: an HTTPS request to a CCIP gateway or NFT metadata) and
receives the answer. The generator never touches the network; `run_async`
performs the requests.

ONE ENGINE, ONE HARD DEADLINE — AND WHY THERE IS NO SYNC ENGINE ANY MORE
------------------------------------------------------------------------
`timeout` is a budget for the WHOLE public call, not per request: a reverse
that walks four systems and two CCIP hops gets one deadline. karma-hello
measured why this matters (`domain_resolver.py:225-232`): web3's ENS call
blocks without an upper bound.

`run_bounded` runs the async engine under ONE `asyncio.wait_for(…, timeout)`,
which cancels whatever is pending when the budget runs out — a dripping body,
dripping headers, a gzip header that never ends, a connect trying N addresses,
a DNS lookup. Both flavours use it: `resolve()` awaits it, and `resolve_sync()`
runs it through `run_blocking` on a private event loop (in a thread of its own
when the calling thread already runs a loop, since loops do not nest).

`run_blocking` does not use `asyncio.run`, and that is measured, not taste:
`asyncio.run` waits for the default executor on exit, and a DNS lookup runs
there — with a 0.5 s budget and a 3 s `getaddrinfo`, `asyncio.run` returned at
3.01 s (py3.13 and py3.9, 2026-09-24); closing a private loop without waiting
returns at 0.5 s. The lookup finishes in its thread; the caller does not wait.

Between requests the engine also checks the clock, and gives each request the
remainder as httpx's timeout; those are what a test double that BLOCKS (instead
of awaiting) runs into, since a blocked loop cannot deliver a cancellation.

⚠️ History, left written — three rounds of PR #6 found the SAME class of hole
in a separate sync engine, each after the previous fix was declared hard:
round 2, a dripping body (1.0 s budget → 6.36 s); round 3, dripping headers and a
dripping gzip header (15.2 s and 10.15 s, measured with a real local server);
round 4, a connect over N addresses from DNS (5.00 s and 10.00 s for N=5 and
N=10). The async engine cut all of them at 1.00 s. c0der's decision for round 4:
stop patching the symptom and shrink what can be refuted — the sync flavour is
now the async engine under the deadline, and `_deadline.py` (the socket-level
cap written in round 3) was deleted.

THE RPC IS CHECKED AGAINST ITS CHAIN
------------------------------------
`rpc={"eip155:1": url}` is a claim by the consumer. Before the first `eth_call`
on a chain, each resolver asks that RPC `eth_chainId` once; if it serves another
chain the answer is `rpc_unavailable`, naming the chain it serves (never the
URL). Measured by the refuter: Sepolia's public RPC under the key `eip155:1`
resolved `vitalik.eth` with `verified_onchain=True`.

WHAT THE ENGINES REFUSE TO FETCH
--------------------------------
CCIP-Read gateway URLs and NFT metadata URLs come from the chain, i.e. from
whoever controls a resolver or an NFT contract. Inside a Lambda that is a
server-side request forgery surface. `check_url` refuses anything that is not
`https://`, carries userinfo, names `localhost`, or is an IP literal outside the
global unicast space (or a bare number that `getaddrinfo` would read as one).
Redirects are followed by hand, at most three, each re-checked. Bodies are
capped. The residual risk, stated: a public hostname whose DNS answers with a
private address is not detected (checking it would race the connect anyway).

A URL that does not PARSE is refused the same way, as `Unavailable` — for
`urlsplit` (`check_url`, `urljoin` on a redirect) or for httpx (`InvalidURL`) —
and so is a gateway body `json.loads` cannot read (`RecursionError` included).
⚠️ Until 0.7.0 those escaped the resolver as `ValueError`, `httpx.InvalidURL` or
`RecursionError`, i.e. whoever controls a resolver could make a consumer's
request fail with a 500 (SDK-5, describe-net's review of its PR 62). Each is
caught by its concrete class, never `except Exception`: a bug of this SDK must
still raise.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import threading
import time
import typing
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)
from urllib.parse import urljoin, urlsplit

import httpx

from . import _abi
from ._hash import selector

#: CAIP-2 ids of the chains the naming systems live on. Protocol facts of each
#: system, not a network table: ENS lives on 1, Basenames registers on 8453, UNS
#: on 137 and 1, Avvy on 43114.
ETHEREUM = "eip155:1"
BASE = "eip155:8453"
POLYGON = "eip155:137"
AVALANCHE = "eip155:43114"


@dataclass(frozen=True)
class Call:
    """An `eth_call` at `latest` on `chain` (a CAIP-2 id)."""

    chain: str
    to: str
    data: bytes


@dataclass(frozen=True)
class Fetch:
    """An HTTPS request. `body is None` → GET; otherwise POST of that JSON."""

    url: str
    body: Optional[bytes] = None


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: bytes


class Unavailable(Exception):
    """Could not ask. Becomes `rpc_unavailable`, the one result never cached.

    🔴 The message never carries an RPC URL: a provider URL often has its API
    key in the path (the incident `describenet/chain/rpc.py::_redact` exists
    for). Messages name the chain, never the endpoint.
    """


class Reverted(Exception):
    """The `eth_call` reverted. `data` is the revert payload (maybe empty)."""

    def __init__(self, data: bytes) -> None:
        super().__init__(f"reverted with {data[:4].hex() or 'no data'}")
        self.data = data


class Outcome(Exception):
    """A terminal answer that is not an address: `not_found`, `expired`…"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


Request = Union[Call, Fetch]
T = TypeVar("T")
Step = Generator[Request, Any, T]

# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------


def rpc_body(call: Call) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": call.to, "data": "0x" + call.data.hex()}, "latest"],
    }


_HEX = re.compile(r"0x([0-9a-fA-F]{2})*")


def rpc_result(status: int, payload: Any, chain: str) -> bytes:
    """The bytes an `eth_call` returned, or the typed reason it did not."""
    if status != 200:
        raise Unavailable(f"the {chain} RPC answered HTTP {status}")
    if not isinstance(payload, dict):
        raise Unavailable(f"the {chain} RPC answered something that is not JSON-RPC")
    if "error" in payload:
        error = payload["error"] if isinstance(payload["error"], dict) else {}
        data = error.get("data")
        if isinstance(data, dict):
            data = data.get("data")
        message = str(error.get("message", ""))
        if isinstance(data, str) and _HEX.fullmatch(data):
            raise Reverted(bytes.fromhex(data[2:]))
        if "revert" in message.lower():
            raise Reverted(b"")
        raise Unavailable(f"the {chain} RPC answered error {error.get('code')}")
    result = payload.get("result")
    if not isinstance(result, str) or not _HEX.fullmatch(result):
        raise Unavailable(f"the {chain} RPC answered a result that is not hex")
    return bytes.fromhex(result[2:])


# ---------------------------------------------------------------------------
# CCIP-Read (EIP-3668)
# ---------------------------------------------------------------------------

OFFCHAIN_LOOKUP = selector("OffchainLookup(address,string[],bytes,bytes4,bytes)")

#: EIP-3668 recommends a limit and leaves the number to the client; ethers v6
#: and viem both stop at 4 nested lookups.
MAX_LOOKUPS = 4


def ccip_call(chain: str, to: str, data: bytes) -> Step[bytes]:
    """An `eth_call` that follows `OffchainLookup` reverts (EIP-3668).

    The gateway's answer is not trusted as data: it goes back ON-CHAIN through
    the resolver's callback, which verifies it (a signature for Basenames, a
    DNSSEC proof for imported DNS names). Only what the callback returns counts.
    """
    for _ in range(MAX_LOOKUPS + 1):
        try:
            result: bytes = yield Call(chain, to, data)
            return result
        except Reverted as rev:
            if rev.data[:4] != OFFCHAIN_LOOKUP:
                raise
            try:
                sender, urls, call_data, callback, extra = _abi.decode(
                    ["address", "string[]", "bytes", "bytes4", "bytes"], rev.data[4:]
                )
            except _abi.AbiError:
                raise Outcome("not_found", "the resolver sent a malformed OffchainLookup") from None
            if sender.lower() != to.lower():
                # EIP-3668: a lookup whose sender is not the contract called
                # MUST NOT be followed.
                raise Outcome("not_found", "the OffchainLookup sender is not the resolver")
            response = yield from _ccip_fetch(sender, urls, call_data)
            data = callback + _abi.encode(["bytes", "bytes"], [response, extra])
    raise Unavailable(f"CCIP-Read did not settle within {MAX_LOOKUPS} lookups")


def _ccip_fetch(sender: str, urls: List[Any], call_data: bytes) -> Step[bytes]:
    last = "the resolver gave no gateway URL"
    hex_data = "0x" + call_data.hex()
    for template in urls:
        if not isinstance(template, str):
            continue
        if "{data}" in template:
            url = template.replace("{sender}", sender.lower()).replace("{data}", hex_data)
            body = None
        else:
            url = template.replace("{sender}", sender.lower())
            body = json.dumps({"data": hex_data, "sender": sender.lower()}).encode()
        try:
            check_url(url)
            response: FetchResponse = yield Fetch(url, body)
        except Unavailable as exc:
            last = str(exc)
            continue
        host = urlsplit(url).hostname
        if 200 <= response.status < 300:
            try:
                parsed = json.loads(response.body)
                hex_answer = parsed["data"]
                if not isinstance(hex_answer, str) or not _HEX.fullmatch(hex_answer):
                    raise ValueError
                return bytes.fromhex(hex_answer[2:])
            except (ValueError, KeyError, TypeError, RecursionError):
                # `RecursionError` is not a `ValueError`: a body of 1,000+ nested
                # `[` (well under MAX_BODY_BYTES) made `json.loads` raise it and
                # it escaped the resolver (SDK-5, measured py3.9/3.12/3.13).
                # Mutation DD.
                last = f"the gateway {host} answered a body without hex `data`"
                continue
        if response.status == 404:
            # EIP-3668: a 4xx ends the lookup. 404 is the gateway saying it has
            # no such name; the other 4xx (429, 403…) say it would not answer us.
            raise Outcome("not_found", f"the gateway {host} answered HTTP 404")
        if 400 <= response.status < 500:
            raise Unavailable(f"the gateway {host} answered HTTP {response.status}")
        last = f"the gateway {host} answered HTTP {response.status}"
    raise Unavailable(f"no CCIP gateway answered: {last}")


# ---------------------------------------------------------------------------
# The URL guard
# ---------------------------------------------------------------------------

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


def check_url(url: str) -> None:
    """Refuse a URL this process must not fetch. Raises `Unavailable`, and only that.

    A URL that does not parse is refused like one that parses to somewhere
    forbidden (SDK-5, found by describe-net's review of PR 62, 2026-09-25):
    `urlsplit` raises `ValueError` on `https://[x/…` (an unclosed bracket) and on
    `https://[zzz]/…` (a bracketed host that is not an IP — py3.9.24, 3.12, 3.13),
    and whoever controls the resolver or the NFT contract picks that string. It
    used to escape the resolver, and describe-net's route answered 500.
    Mutation DA.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise Unavailable("refused a URL that does not parse") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise Unavailable(f"refused a non-https URL ({parts.scheme or 'no scheme'})")
    if not host or parts.username is not None or parts.password is not None:
        raise Unavailable("refused a URL without a host or with credentials")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise Unavailable("refused a local host name")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise Unavailable("refused an IP literal outside the global address space")
        return
    if not re.search(r"[a-z]", host.rsplit(".", 1)[-1]):
        # `https://2130706433/` and `https://127.1/` are 127.0.0.1 to
        # getaddrinfo on most platforms. A real top-level domain has letters.
        raise Unavailable("refused a host that is a number in disguise")


# ---------------------------------------------------------------------------
# The engines
# ---------------------------------------------------------------------------

#: Largest body accepted from a gateway or a metadata URL. DNSSEC proofs and
#: storage proofs are kilobytes; this leaves two orders of magnitude of room.
MAX_BODY_BYTES = 2_000_000
MAX_REDIRECTS = 3
#: Headers of a gateway / metadata request. `Accept-Encoding: identity` because a
#: compressed body is read in DECODED bytes: the round-3 verifier dripped a gzip
#: header with FLG=FCOMMENT and zlib produced nothing — no size cap, no clock
#: check, 10.15 s on a 1.0 s budget. Those bodies are JSON of a few KB.
_FETCH = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}


async def _raw_chunks(response: httpx.Response) -> typing.AsyncIterator[bytes]:
    """The body as it came off the wire. A body an in-memory transport handed over
    whole (`is_stream_consumed`) is used as is: with identity enforced, raw and
    decoded are the same bytes."""
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


def _refuse_encoded(response: httpx.Response, host: Optional[str]) -> None:
    """A gateway that ignores `Accept-Encoding: identity` is not read at all."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise Unavailable(f"{host} sent a {encoding}-encoded body; only identity is read")


def _redirect_target(response: httpx.Response, url: str) -> Optional[str]:
    """The next URL of a redirect, or `None`. Raises `Unavailable`, and only that.

    A `Location` that httpx cannot parse never gets here: httpx builds the next
    request itself and raises `RemoteProtocolError`, an `httpx.HTTPError`
    (measured: `//[zzz]/a`, `https://gw.example/\\x01`). One httpx accepts and
    `urljoin` does not — `https://[x/`, an unclosed bracket — raised `ValueError`
    out of the resolver (SDK-5). Mutation DC.
    """
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if location:
            try:
                return str(urljoin(url, location))
            except ValueError:
                raise Unavailable(
                    f"{urlsplit(url).hostname} redirected to a URL that does not parse"
                ) from None
    return None


_CHAIN_ID_BODY: Dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}


def chain_number(chain: str) -> Optional[int]:
    """The EIP-155 chain id a CAIP-2 key promises, or `None` if it is not `eip155`."""
    namespace, _, reference = chain.partition(":")
    return int(reference) if namespace == "eip155" and reference.isdigit() else None


def check_served_chain(chain: str, status: int, payload: Any) -> None:
    """`Unavailable` unless the RPC's `eth_chainId` answer is the chain of its key."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(result, str):
        raise Unavailable(f"the {chain} RPC did not answer eth_chainId")
    try:
        served = int(result, 16)
    except ValueError:
        raise Unavailable(f"the {chain} RPC answered an eth_chainId that is not hex") from None
    if served != chain_number(chain):
        # The chain it serves, never its URL: the URL may carry a key.
        raise Unavailable(f"the RPC configured for {chain} serves eip155:{served}")


async def run_bounded(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float,
    verified_chains: Optional[Set[str]] = None,
) -> T:
    """`run_async` under THE deadline of the call: one `asyncio.wait_for`.

    Both `resolve()` and `resolve_sync()` go through here. When the budget runs
    out whatever is pending is cancelled and `Unavailable` is raised; the
    resolver turns it into `rpc_unavailable`.
    """
    try:
        return await asyncio.wait_for(
            run_async(
                gen, rpc=rpc, client=client, timeout=timeout, verified_chains=verified_chains
            ),
            timeout,
        )
    except asyncio.TimeoutError:
        raise Unavailable(f"the {timeout:g}s budget of this call ran out") from None


async def run_async(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float,
    verified_chains: Optional[Set[str]] = None,
) -> T:
    """Drive a resolution generator to its end over an async client.

    Not bounded by itself: `run_bounded` is. The clock is still checked between
    requests and each request gets the remainder as httpx's timeout — that is
    what stops a test double that BLOCKS the loop, which no cancellation reaches.
    `verified_chains` is the set of chains whose RPC already answered the right
    `eth_chainId`; a resolver passes its own so the check runs once per chain.
    """
    deadline = time.monotonic() + timeout
    verified = verified_chains if verified_chains is not None else set()
    value: Any = None
    exc: Optional[BaseException] = None
    while True:
        try:
            request = gen.throw(exc) if exc is not None else gen.send(value)
        except StopIteration as stop:
            result: T = stop.value
            return result
        value, exc = None, None
        if time.monotonic() >= deadline:
            exc = Unavailable(f"the {timeout:g}s budget of this call ran out")
            continue
        try:
            value = await _do_async(request, rpc, client, deadline, verified)
        except (Unavailable, Reverted) as failure:
            exc = failure


def _left(deadline: float) -> httpx.Timeout:
    return httpx.Timeout(max(deadline - time.monotonic(), 0.001))


async def _post_rpc(
    client: httpx.AsyncClient, url: str, body: Dict[str, Any], chain: str, deadline: float
) -> Tuple[int, Any]:
    """One JSON-RPC exchange, its body capped (an RPC may gzip: decoded bytes)."""
    try:
        async with client.stream("POST", url, json=body, timeout=_left(deadline)) as response:
            parts: List[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise Unavailable(f"the {chain} RPC sent more than {MAX_BODY_BYTES} bytes")
                parts.append(chunk)
            status = response.status_code
    except httpx.TimeoutException:
        raise Unavailable(f"the {chain} RPC timed out") from None
    except httpx.HTTPError as err:
        raise Unavailable(f"the {chain} RPC is unreachable ({type(err).__name__})") from None
    try:
        return status, json.loads(b"".join(parts))
    except ValueError:
        raise Unavailable(f"the {chain} RPC answered something that is not JSON") from None


async def _do_async(
    request: Request,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    deadline: float,
    verified: Set[str],
) -> Any:
    if isinstance(request, Call):
        url = rpc.get(request.chain)
        if not url:
            raise Unavailable(f"no RPC configured for {request.chain}")
        if request.chain not in verified:
            status, payload = await _post_rpc(client, url, _CHAIN_ID_BODY, request.chain, deadline)
            check_served_chain(request.chain, status, payload)
            verified.add(request.chain)
        status, payload = await _post_rpc(client, url, rpc_body(request), request.chain, deadline)
        return rpc_result(status, payload, request.chain)
    url = request.url
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() >= deadline:
            raise Unavailable("the budget of this call ran out before a redirect")
        check_url(url)
        method = "GET" if request.body is None else "POST"
        host = urlsplit(url).hostname
        try:
            async with client.stream(
                method, url, content=request.body, headers=_FETCH, timeout=_left(deadline)
            ) as response:
                target = _redirect_target(response, url)
                if target is not None:
                    url = target
                    continue
                _refuse_encoded(response, host)
                parts: List[bytes] = []
                total = 0
                async for chunk in _raw_chunks(response):
                    total += len(chunk)
                    if total > MAX_BODY_BYTES:
                        raise Unavailable(f"{host} sent more than {MAX_BODY_BYTES} bytes")
                    parts.append(chunk)
                return FetchResponse(response.status_code, b"".join(parts))
        except httpx.InvalidURL:
            # Not an `httpx.HTTPError`: httpx refuses to build the request at all.
            # A URL `urlsplit` accepts and httpx does not (a control character,
            # `https://gw.example/\x01…`) escaped here as `InvalidURL` (SDK-5).
            # The detail does not repeat the URL: it is the part that is broken.
            # Mutation DB.
            raise Unavailable("refused a URL that httpx cannot request (InvalidURL)") from None
        except httpx.TimeoutException:
            raise Unavailable(f"{host} timed out") from None
        except httpx.HTTPError as err:
            raise Unavailable(f"{host} is unreachable ({type(err).__name__})") from None
    raise Unavailable(f"more than {MAX_REDIRECTS} redirects")


# ---------------------------------------------------------------------------
# The sync flavour: the async engine, on a private loop
# ---------------------------------------------------------------------------


def _run_on_private_loop(coro: typing.Coroutine[Any, Any, T]) -> T:
    """`asyncio.run` minus the wait for the default executor (see the header)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            # `close()` shuts the default executor down WITHOUT waiting: a DNS
            # lookup still running there finishes on its own, off the caller's time.
            loop.close()


def _inside_async_code() -> bool:
    """Is this thread running async code, of ANY library?

    asyncio answers `get_running_loop()`; trio (and curio) do not have an
    asyncio loop, and only `sniffio` knows about them. Asking asyncio alone was
    a bug (round 5 of PR #6): `resolve_sync()` inside `trio.run` built its
    private asyncio loop in trio's own thread, httpx's transport (httpcore) asked
    sniffio which library it was on, heard «trio», and the call died with
    `RuntimeError: Task got bad yield` (measured 2026-09-24, trio 0.34, py3.13).
    `sniffio` is not a dependency: when it is not installed, no
    library that needs it is running either. Mutation CD.
    """
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        pass
    try:
        import sniffio
    except ImportError:
        return False
    try:
        sniffio.current_async_library()
    except sniffio.AsyncLibraryNotFoundError:
        return False
    return True


def run_blocking(make: Callable[[], typing.Coroutine[Any, Any, T]]) -> T:
    """Run the coroutine `make()` returns, from sync code, and return its result.

    With no async code running in this thread, on a private loop right here.
    With some running (a `_sync` call from asyncio, trio or anything sniffio
    knows), on a private loop in a thread of its own — loops do not nest, and
    two libraries do not share a thread — and this thread waits for it: calling
    a blocking function from async code blocks it, as it would anyway.

    What the deadline does NOT reach: a `getaddrinfo` it abandons. The call
    returns on time, but the lookup keeps running in its executor thread until
    the OS answers (`close()` does not wait for it, and cannot kill it: a
    thread in a C call is not cancellable). It costs a thread for that long,
    not the caller's time.
    """
    if not _inside_async_code():
        return _run_on_private_loop(make())
    box: Dict[str, Any] = {}

    def worker() -> None:
        try:
            box["result"] = _run_on_private_loop(make())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=worker, name="uvd-names-sync", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    result: T = box["result"]
    return result
