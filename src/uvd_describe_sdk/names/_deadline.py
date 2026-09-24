"""The sync deadline, enforced where it can be: on every socket operation.

WHY THIS FILE EXISTS (rounds 2 and 3 of PR #6)
----------------------------------------------
Round 2 made the sync engine read every body in chunks and check the clock
after each chunk. The round-3 verifier measured, against a LOCAL server and a
real httpx transport, what that still left open (budget 1.0 s):

  (C) a server that drips its status line and headers, 1 byte every 0.5 s:
      15.2 s — `client.stream()` waits for the headers, and httpcore sets the
      read timeout once per request, so no chunk ever reaches the check;
  (D) `Content-Encoding: gzip` with FLG=FCOMMENT and the comment dripping:
      10.15 s — zlib produces no decoded byte, so neither the clock check nor
      the size cap (which counted DECODED bytes) ever ran.

Re-measured here before writing this, on `cbc93a0`: 11.43 s for (C) with a
0.3 s drip, and 10.15 s for (D) — returning "no error" and an empty body.

THE FIX
-------
`DeadlineBackend` wraps httpcore's `SyncBackend`: every `connect`, `read`,
`write` and TLS handshake gets `min(its own timeout, what is left of the
budget)`, and raises the matching httpcore timeout once nothing is left. The
budget travels in a `ContextVar` that `_proto.run_sync` sets for the whole
call. So a drip in the headers, in a gzip header or anywhere else is cut at
the socket, whatever the bytes mean.

`deadline_transport()` builds the `httpx` transport the resolver uses by
default, on PUBLIC httpcore API (`ConnectionPool(network_backend=...)`): httpx
does not expose a network backend, and rebuilding its private `_pool` would be
the kind of shortcut that breaks silently on an upgrade.

WHAT IS STILL NOT BOUNDED — written down, not implied
-----------------------------------------------------
* DNS. `socket.create_connection` resolves the host with `getaddrinfo`, which
  takes no timeout: a slow resolver for a gateway's host is not cut by the
  budget in sync. (The async engine's `asyncio.wait_for` does cut it.)
* A `transport=` the consumer passes: that transport has its own sockets. Only
  the chunk-level check of `_proto._read_capped` applies there.
* Environment proxies: this transport does not read `HTTP(S)_PROXY` — the
  house rule of this SDK is that nothing is read from the environment. A
  consumer behind a proxy passes its own `transport=` (and the point above
  applies).
"""

from __future__ import annotations

import contextlib
import time
import typing
from contextvars import ContextVar
from typing import Iterator, Optional, Type

import httpcore
import httpx

#: The monotonic instant the current sync call must end by, or `None`.
_DEADLINE: ContextVar[Optional[float]] = ContextVar("uvd_names_deadline", default=None)


@contextlib.contextmanager
def deadline_scope(deadline: float) -> Iterator[None]:
    """Every socket operation inside this block is capped to `deadline`."""
    token = _DEADLINE.set(deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _cap(timeout: Optional[float], expired: Type[Exception]) -> Optional[float]:
    deadline = _DEADLINE.get()
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise expired("the budget of this call ran out")
    return left if timeout is None else min(timeout, left)


class _DeadlineStream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return self._inner.read(max_bytes, _cap(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._inner.write(buffer, _cap(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: typing.Any,
        server_hostname: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> httpcore.NetworkStream:
        inner = self._inner.start_tls(
            ssl_context, server_hostname, _cap(timeout, httpcore.ConnectTimeout)
        )
        return _DeadlineStream(inner)

    def get_extra_info(self, info: str) -> typing.Any:
        return self._inner.get_extra_info(info)


class DeadlineBackend(httpcore.NetworkBackend):
    """httpcore's `SyncBackend`, with every operation capped to the deadline."""

    def __init__(self, inner: Optional[httpcore.NetworkBackend] = None) -> None:
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: typing.Any = None,
    ) -> httpcore.NetworkStream:
        stream = self._inner.connect_tcp(
            host, port, _cap(timeout, httpcore.ConnectTimeout), local_address, socket_options
        )
        return _DeadlineStream(stream)

    def connect_unix_socket(
        self, path: str, timeout: Optional[float] = None, socket_options: typing.Any = None
    ) -> httpcore.NetworkStream:  # pragma: no cover - the resolver never dials a unix socket
        stream = self._inner.connect_unix_socket(
            path, _cap(timeout, httpcore.ConnectTimeout), socket_options
        )
        return _DeadlineStream(stream)

    def sleep(self, seconds: float) -> None:  # pragma: no cover - used by httpcore retries only
        self._inner.sleep(seconds)


#: httpcore exception → httpx exception, most specific first. The engine only
#: branches on `httpx.TimeoutException` and `httpx.HTTPError`.
_MAP = (
    (httpcore.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore.ReadTimeout, httpx.ReadTimeout),
    (httpcore.WriteTimeout, httpx.WriteTimeout),
    (httpcore.PoolTimeout, httpx.PoolTimeout),
    (httpcore.TimeoutException, httpx.TimeoutException),
    (httpcore.ConnectError, httpx.ConnectError),
    (httpcore.ReadError, httpx.ReadError),
    (httpcore.WriteError, httpx.WriteError),
    (httpcore.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore.UnsupportedProtocol, httpx.UnsupportedProtocol),
    (httpcore.ProxyError, httpx.ProxyError),
    (httpcore.ProtocolError, httpx.ProtocolError),
    (httpcore.NetworkError, httpx.NetworkError),
    (httpcore.ConnectionNotAvailable, httpx.TransportError),
)
_HTTPCORE_ERRORS = tuple(source for source, _ in _MAP)


def _as_httpx(exc: Exception) -> Exception:
    for source, target in _MAP:
        if isinstance(exc, source):
            return target(str(exc))
    return httpx.TransportError(str(exc))  # pragma: no cover - _MAP covers _HTTPCORE_ERRORS


class _Body(httpx.SyncByteStream):
    def __init__(self, inner: typing.Iterable[bytes]) -> None:
        self._inner = inner

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._inner
        except _HTTPCORE_ERRORS as exc:
            raise _as_httpx(exc) from exc

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


class DeadlineTransport(httpx.BaseTransport):
    """An `httpx` transport over an httpcore pool with `DeadlineBackend`."""

    def __init__(self) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=DeadlineBackend(),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self._pool.handle_request(core_request)
        except _HTTPCORE_ERRORS as exc:
            raise _as_httpx(exc) from exc
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_Body(response.stream),  # type: ignore[arg-type]
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._pool.close()
