"""The I/O seam: resolution logic as generators, and the two engines that run it.

WHY GENERATORS (sans-IO)
------------------------
The contract asks for `resolve()` AND `resolve_sync()`. Execution Market is
async (`asyncio.to_thread` around web3), describe-net and KarmaKadabra are
sync, karma-hello is async. Writing the policy twice — once with `await`, once
without — is the shortest path to two resolvers that disagree (this repo's own
CLAUDE.md names the risk: "sin duplicar una línea de política"). So every
resolution step is a generator that YIELDS what it needs (`Call`: an `eth_call`
on a chain; `Fetch`: an HTTPS request to a CCIP gateway or NFT metadata) and
receives the answer. The generator never touches the network. Two small engines
—`run_sync` and `run_async`— perform the requests. One policy, two transports.

THE HARD TIMEOUT
----------------
`timeout` is a budget for the WHOLE public call, not per request: a reverse
that walks four systems and two CCIP hops gets one deadline. Before each request
the engine computes what is left and gives the request exactly that; past the
deadline the next request fails at once. karma-hello measured why this matters
(`domain_resolver.py:225-232`): web3's ENS call blocks without an upper bound.
The async engine enforces it with `asyncio.wait_for`, which is truly hard.

The sync engine enforces it at the SOCKET, through the transport the resolver
uses by default (`_deadline.py`): every connect, read, write and TLS handshake
gets `min(its timeout, what is left)` and fails once nothing is left — the status
line, the headers and a compressed body included. On top of that it reads every
body in chunks, checks the clock after each chunk and before each redirect, and
asks gateways for `Accept-Encoding: identity` (a gzip body is refused, and the
size cap counts wire bytes). Measured on a local server with a 1.0 s budget:
headers dripping 1 byte every 0.3 s, 11.43 s before → 1.00 s after.

What is NOT bounded in sync, stated: DNS resolution (`getaddrinfo` takes no
timeout), and a `transport=` passed by the consumer — its sockets are its own,
and only the chunk and redirect checks apply to it.

⚠️ Corrected twice on 2026-09-24, both left written. Round 2 of PR #6: this
paragraph said the sync engine only passed the remainder as httpx's timeout and
that "a server that drips one byte at a time could stretch a single read"
(measured by the refuter: 1.0 s → 6.36 s). Round 2 then said the chunk checks
made the sync timeout hard, "the worst overshoot is ONE read". Round 3 measured
that this was still false: httpx waits for the complete headers before any
chunk exists, and httpcore sets the read timeout once per request — headers
dripping 1 byte every 0.5 s took 15.2 s, and a dripping gzip header (FCOMMENT)
10.15 s, with no decoded byte ever reaching the clock check or the size cap.
Twice a text claimed a bound the code did not have; the bound now lives in the
socket, and the tests that pin it use a real local server.

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
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
import typing
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    Generator,
    Iterable,
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
from ._deadline import deadline_scope
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
            except (ValueError, KeyError, TypeError):
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
    """Refuse a URL this process must not fetch. Raises `Unavailable`."""
    parts = urlsplit(url)
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


def _raw_chunks(response: httpx.Response) -> Iterable[bytes]:
    """The body as it came off the wire. A body an in-memory transport handed over
    whole (`is_stream_consumed`) is used as is: with identity enforced, raw and
    decoded are the same bytes."""
    return [response.content] if response.is_stream_consumed else response.iter_raw()


async def _araw_chunks(response: httpx.Response) -> typing.AsyncIterator[bytes]:
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
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if location:
            return str(urljoin(url, location))
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


def _read_capped(chunks: Iterable[bytes], deadline: float, what: str) -> bytes:
    """A body, read chunk by chunk: capped in size and cut at the deadline."""
    parts: List[bytes] = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise Unavailable(f"{what} sent more than {MAX_BODY_BYTES} bytes")
        parts.append(chunk)
        if time.monotonic() > deadline:
            raise Unavailable(f"{what} was still sending when the budget of this call ran out")
    return b"".join(parts)


def _left(deadline: float) -> httpx.Timeout:
    return httpx.Timeout(max(deadline - time.monotonic(), 0.001))


def run_sync(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.Client,
    timeout: float,
    verified_chains: Optional[Set[str]] = None,
) -> T:
    """Drive a resolution generator to its end over a blocking client.

    `verified_chains` is the set of chains whose RPC already answered the right
    `eth_chainId`; a resolver passes its own so the check runs once per chain.
    """
    deadline = time.monotonic() + timeout
    verified = verified_chains if verified_chains is not None else set()
    value: Any = None
    exc: Optional[BaseException] = None
    # Every socket operation of the default transport is capped to `deadline`
    # (`_deadline.py`). The checks below stay: they are what a consumer's own
    # `transport=` gets.
    with deadline_scope(deadline):
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
                value = _do_sync(request, rpc, client, deadline, verified)
            except (Unavailable, Reverted) as failure:
                exc = failure


def _post_rpc_sync(
    client: httpx.Client, url: str, body: Dict[str, Any], chain: str, deadline: float
) -> Tuple[int, Any]:
    try:
        with client.stream("POST", url, json=body, timeout=_left(deadline)) as response:
            raw = _read_capped(response.iter_bytes(), deadline, f"the {chain} RPC")
            status = response.status_code
    except httpx.TimeoutException:
        raise Unavailable(f"the {chain} RPC timed out") from None
    except httpx.HTTPError as err:
        raise Unavailable(f"the {chain} RPC is unreachable ({type(err).__name__})") from None
    try:
        return status, json.loads(raw)
    except ValueError:
        raise Unavailable(f"the {chain} RPC answered something that is not JSON") from None


def _do_sync(
    request: Request,
    rpc: Mapping[str, str],
    client: httpx.Client,
    deadline: float,
    verified: Set[str],
) -> Any:
    if isinstance(request, Call):
        url = rpc.get(request.chain)
        if not url:
            raise Unavailable(f"no RPC configured for {request.chain}")
        if request.chain not in verified:
            status, payload = _post_rpc_sync(client, url, _CHAIN_ID_BODY, request.chain, deadline)
            check_served_chain(request.chain, status, payload)
            verified.add(request.chain)
        status, payload = _post_rpc_sync(client, url, rpc_body(request), request.chain, deadline)
        return rpc_result(status, payload, request.chain)
    url = request.url
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() >= deadline:
            raise Unavailable("the budget of this call ran out before a redirect")
        check_url(url)
        method = "GET" if request.body is None else "POST"
        host = urlsplit(url).hostname
        try:
            with client.stream(
                method, url, content=request.body, headers=_FETCH, timeout=_left(deadline)
            ) as response:
                target = _redirect_target(response, url)
                if target is not None:
                    url = target
                    continue
                _refuse_encoded(response, host)
                body = _read_capped(_raw_chunks(response), deadline, str(host))
                return FetchResponse(response.status_code, body)
        except httpx.TimeoutException:
            raise Unavailable(f"{host} timed out") from None
        except httpx.HTTPError as err:
            raise Unavailable(f"{host} is unreachable ({type(err).__name__})") from None
    raise Unavailable(f"more than {MAX_REDIRECTS} redirects")


async def run_async(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float,
    verified_chains: Optional[Set[str]] = None,
) -> T:
    """Drive a resolution generator to its end over an async client.

    Each request runs under `asyncio.wait_for(…, remaining)`: the deadline is
    hard here, drips included. `verified_chains` as in `run_sync`.
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
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            exc = Unavailable(f"the {timeout:g}s budget of this call ran out")
            continue
        try:
            value = await asyncio.wait_for(
                _do_async(request, rpc, client, remaining, verified), remaining
            )
        except asyncio.TimeoutError:
            exc = Unavailable(f"the {timeout:g}s budget of this call ran out")
        except (Unavailable, Reverted) as failure:
            exc = failure


async def _post_rpc_async(
    client: httpx.AsyncClient, url: str, body: Dict[str, Any], chain: str, limit: httpx.Timeout
) -> Tuple[int, Any]:
    try:
        response = await client.post(url, json=body, timeout=limit)
        return response.status_code, response.json()
    except httpx.TimeoutException:
        raise Unavailable(f"the {chain} RPC timed out") from None
    except httpx.HTTPError as err:
        raise Unavailable(f"the {chain} RPC is unreachable ({type(err).__name__})") from None
    except ValueError:
        raise Unavailable(f"the {chain} RPC answered something that is not JSON") from None


async def _do_async(
    request: Request,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    remaining: float,
    verified: Set[str],
) -> Any:
    limit = httpx.Timeout(remaining)
    if isinstance(request, Call):
        url = rpc.get(request.chain)
        if not url:
            raise Unavailable(f"no RPC configured for {request.chain}")
        if request.chain not in verified:
            status, payload = await _post_rpc_async(
                client, url, _CHAIN_ID_BODY, request.chain, limit
            )
            check_served_chain(request.chain, status, payload)
            verified.add(request.chain)
        status, payload = await _post_rpc_async(
            client, url, rpc_body(request), request.chain, limit
        )
        return rpc_result(status, payload, request.chain)
    url = request.url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        method = "GET" if request.body is None else "POST"
        try:
            async with client.stream(
                method, url, content=request.body, headers=_FETCH, timeout=limit
            ) as response:
                target = _redirect_target(response, url)
                if target is not None:
                    url = target
                    continue
                _refuse_encoded(response, urlsplit(url).hostname)
                chunks = []
                total = 0
                async for chunk in _araw_chunks(response):
                    total += len(chunk)
                    if total > MAX_BODY_BYTES:
                        raise Unavailable(
                            f"{urlsplit(url).hostname} sent more than {MAX_BODY_BYTES} bytes"
                        )
                    chunks.append(chunk)
                return FetchResponse(response.status_code, b"".join(chunks))
        except httpx.TimeoutException:
            raise Unavailable(f"{urlsplit(url).hostname} timed out") from None
        except httpx.HTTPError as err:
            raise Unavailable(
                f"{urlsplit(url).hostname} is unreachable ({type(err).__name__})"
            ) from None
    raise Unavailable(f"more than {MAX_REDIRECTS} redirects")
