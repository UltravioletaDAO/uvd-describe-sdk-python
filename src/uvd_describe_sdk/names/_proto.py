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
The async engine enforces it with `asyncio.wait_for`, which is truly hard. The
sync engine passes the remainder as httpx's timeout, which bounds connect and
each read — a server that drips one byte at a time could stretch a single read;
that limit is stated here instead of being papered over.

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
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Mapping, Optional, TypeVar, Union
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
_JSON = {"Content-Type": "application/json", "Accept": "application/json"}


def _redirect_target(response: httpx.Response, url: str) -> Optional[str]:
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if location:
            return str(urljoin(url, location))
    return None


def run_sync(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.Client,
    timeout: float,
) -> T:
    """Drive a resolution generator to its end over a blocking client."""
    deadline = time.monotonic() + timeout
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
            value = _do_sync(request, rpc, client, remaining)
        except (Unavailable, Reverted) as failure:
            exc = failure


def _do_sync(
    request: Request, rpc: Mapping[str, str], client: httpx.Client, remaining: float
) -> Any:
    limit = httpx.Timeout(remaining)
    if isinstance(request, Call):
        url = rpc.get(request.chain)
        if not url:
            raise Unavailable(f"no RPC configured for {request.chain}")
        try:
            response = client.post(url, json=rpc_body(request), timeout=limit)
            payload = response.json()
        except httpx.TimeoutException:
            raise Unavailable(f"the {request.chain} RPC timed out") from None
        except httpx.HTTPError as err:
            raise Unavailable(
                f"the {request.chain} RPC is unreachable ({type(err).__name__})"
            ) from None
        except ValueError:
            raise Unavailable(
                f"the {request.chain} RPC answered something that is not JSON"
            ) from None
        return rpc_result(response.status_code, payload, request.chain)
    url = request.url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        method = "GET" if request.body is None else "POST"
        try:
            with client.stream(
                method, url, content=request.body, headers=_JSON, timeout=limit
            ) as response:
                target = _redirect_target(response, url)
                if target is not None:
                    url = target
                    continue
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
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


async def run_async(
    gen: Step[T],
    *,
    rpc: Mapping[str, str],
    client: httpx.AsyncClient,
    timeout: float,
) -> T:
    """Drive a resolution generator to its end over an async client.

    Each request runs under `asyncio.wait_for(…, remaining)`: the deadline is
    hard here, drips included.
    """
    deadline = time.monotonic() + timeout
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
            value = await asyncio.wait_for(_do_async(request, rpc, client, remaining), remaining)
        except asyncio.TimeoutError:
            exc = Unavailable(f"the {timeout:g}s budget of this call ran out")
        except (Unavailable, Reverted) as failure:
            exc = failure


async def _do_async(
    request: Request, rpc: Mapping[str, str], client: httpx.AsyncClient, remaining: float
) -> Any:
    limit = httpx.Timeout(remaining)
    if isinstance(request, Call):
        url = rpc.get(request.chain)
        if not url:
            raise Unavailable(f"no RPC configured for {request.chain}")
        try:
            response = await client.post(url, json=rpc_body(request), timeout=limit)
            payload = response.json()
        except httpx.TimeoutException:
            raise Unavailable(f"the {request.chain} RPC timed out") from None
        except httpx.HTTPError as err:
            raise Unavailable(
                f"the {request.chain} RPC is unreachable ({type(err).__name__})"
            ) from None
        except ValueError:
            raise Unavailable(
                f"the {request.chain} RPC answered something that is not JSON"
            ) from None
        return rpc_result(response.status_code, payload, request.chain)
    url = request.url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        method = "GET" if request.body is None else "POST"
        try:
            async with client.stream(
                method, url, content=request.body, headers=_JSON, timeout=limit
            ) as response:
                target = _redirect_target(response, url)
                if target is not None:
                    url = target
                    continue
                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
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
