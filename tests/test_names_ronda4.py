"""Ronda 4 del PR #6: el modo sync ES el motor async bajo un deadline duro.

Decisión de c0der (manda): tercera ronda seguida con un P1 nuevo de la misma
clase en el motor sync —cuerpo, headers y gzip, y ahora un connect sobre N
direcciones de DNS (5,00 s con N=5 y 10,00 s con N=10, presupuesto 1,0 s)—, así
que no se parcha el síntoma: se achica lo refutable. `resolve_sync()` corre el
mismo motor que `resolve()`, bajo UN `asyncio.wait_for`.

Lo que fija este archivo:

* N direcciones que no contestan el SYN (un agujero negro local: un puerto de
  127.0.0.1 con el backlog lleno — medido en Windows, cada SYN queda ~2 s antes
  del rechazo) no estiran la llamada sync: ~ el presupuesto.
* El DNS tampoco: `run_blocking` no usa `asyncio.run`, que espera al executor
  donde corre `getaddrinfo` (medido: 0,5 s de presupuesto → 3,01 s).
* Un `_sync` llamado desde código async corre en un hilo propio con su loop.
* Un transporte sólo-sync se rechaza al construir, no en la primera request.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, Callable, Iterator, List, Tuple

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver

from .names_replay import Replay, load, resolver_for
from .names_servidor import Responder, ServidorLocal

PRESUPUESTO = 1.0
MARGEN = 0.6


@pytest.fixture
def agujero_negro() -> Iterator[int]:
    """Un puerto en 127.0.0.1 con el backlog LLENO: un SYN más no entra.

    ⚠️ Corregido en la ronda 5 del PR #6, y se deja escrito: la receta vieja
    (`listen(0)` + 8 connects no bloqueantes, sin mirar) en macOS NO llenaba
    nada —medido por el verificador de la ronda 4: el connect entraba en 0,00 s,
    1 connect y no N— y los dos tests
    pasaban igual, porque un connect que entra y un 500 también caen dentro del
    presupuesto. Ahora se llena hasta que un connect de PRUEBA, con 0,3 s, vence:
    eso es lo que el test necesita que pase, medido en el SO donde corre
    (Windows, 2026-09-24: 1 relleno entra, la prueba 2 vence, y un connect más
    queda 2,00 s antes del rechazo). Y el test cuenta los connects.
    """
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(0)
    puerto = servidor.getsockname()[1]
    rellenos: List[socket.socket] = []
    for _ in range(512):
        prueba = socket.socket()
        prueba.settimeout(0.3)
        try:
            prueba.connect(("127.0.0.1", puerto))
        except socket.timeout:
            prueba.close()
            break
        rellenos.append(prueba)
    else:
        pytest.fail("512 connects entraron: este SO no deja un backlog lleno en loopback")
    yield puerto
    for relleno in rellenos:
        relleno.close()
    servidor.close()


def _contar_connects(monkeypatch: pytest.MonkeyPatch, puerto: int) -> List[float]:
    """Cada connect que el motor intenta a `puerto` (anyio → `create_connection`)."""
    intentos: List[float] = []
    real = asyncio.base_events.BaseEventLoop.create_connection

    async def contar(self: Any, factory: Any, host: Any = None, port: Any = None, **kw: Any) -> Any:
        if port == puerto:
            intentos.append(time.monotonic())
        return await real(self, factory, host, port, **kw)

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", contar)
    return intentos


def _dns(monkeypatch: pytest.MonkeyPatch, host: str, respuesta: Any) -> None:
    """🔴 SINTÉTICO: el DNS de `host` contesta lo que diga `respuesta(port)`."""
    real = socket.getaddrinfo

    def falso(consulta: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        # anyio pasa el host como BYTES (IDNA): comparado con un `str`, la
        # primera versión de este doble no matcheaba y la consulta se iba al DNS
        # real — lo que `conftest.py` ahora atrapa y hace fallar.
        nombre = consulta.decode("ascii") if isinstance(consulta, bytes) else consulta
        if nombre == host:
            return respuesta(port)
        return real(consulta, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", falso)


@pytest.mark.parametrize("n", [3, 5])
def test_sync_un_connect_sobre_n_direcciones_que_no_contestan_se_corta(
    monkeypatch: pytest.MonkeyPatch,
    agujero_negro: int,
    sin_proxies: None,
    n: int,
) -> None:
    """Medido por el verificador sobre `3f556d9` (motor sync propio): N=5 →
    5,00 s, N=10 → 10,00 s. N lo elige quien controla el DNS del gateway."""
    direccion: Tuple[str, int] = ("127.0.0.1", agujero_negro)
    _dns(
        monkeypatch,
        "agujero.test",
        lambda port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", direccion)] * n,
    )
    url = f"http://agujero.test:{agujero_negro}/"
    intentos = _contar_connects(monkeypatch, agujero_negro)
    with NameResolver(rpc={"eip155:1": url}, cache=False, timeout=PRESUPUESTO) as resolver:
        inicio = time.monotonic()
        result = resolver.resolve_sync("ultravioletadao.eth")
        duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert duro < PRESUPUESTO + MARGEN, f"N={n}: presupuesto {PRESUPUESTO} s, tardó {duro:.2f} s"
    # La premisa: los SYN quedaron retenidos y el motor pasó a la dirección
    # siguiente (anyio escalona cada 0,25 s). Con UN connect el agujero no
    # existió y el tiempo de arriba no prueba nada.
    assert len(intentos) >= 2, f"N={n}: {len(intentos)} connect(s) — el agujero no retuvo el SYN"


def test_sync_un_DNS_lento_no_retiene_la_llamada(
    monkeypatch: pytest.MonkeyPatch,
    sin_proxies: None,
) -> None:
    """`asyncio.run` esperaría al `getaddrinfo` colgado en el executor (medido:
    3,01 s con 0,5 s de presupuesto); el loop propio de `run_blocking` no."""

    def lento(port: Any) -> Any:
        time.sleep(2.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))]

    _dns(monkeypatch, "lento.test", lento)
    with NameResolver(
        rpc={"eip155:1": "http://lento.test:9/"}, cache=False, timeout=0.5
    ) as resolver:
        inicio = time.monotonic()
        result = resolver.resolve_sync("ultravioletadao.eth")
        duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert duro < 1.2, f"presupuesto 0.5 s, tardó {duro:.2f} s"


def test_un_sync_llamado_desde_codigo_async_da_lo_mismo() -> None:
    """Con un loop corriendo en el hilo, `run_blocking` usa un hilo propio con
    su loop (los loops no se anidan). El resultado es el de la grabación."""
    fixture = load("resolve_ultravioletadao_eth")
    replay = Replay(fixture["exchanges"])
    resolver = resolver_for(fixture, replay)

    async def codigo_async() -> Any:
        asyncio.get_running_loop()  # hay loop corriendo acá
        return resolver.resolve_sync("ultravioletadao.eth")

    result = asyncio.run(codigo_async())
    replay.assert_consumed()
    assert result.to_dict() == fixture["result"]


def test_un_transporte_solo_sync_se_rechaza_al_construir() -> None:
    with pytest.raises(ValueError, match="AsyncBaseTransport"):
        NameResolver(rpc={}, transport=httpx.HTTPTransport())  # type: ignore[arg-type]
    # Un MockTransport es las dos cosas: se acepta.
    NameResolver(rpc={}, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    # Y un transporte async explícito, también.
    NameResolver(rpc={}, async_transport=httpx.AsyncHTTPTransport())


def _500(conn: socket.socket, _: bytes) -> None:
    conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")


def test_un_solo_contexto_TLS_por_resolver(
    servidor: Callable[[Responder], ServidorLocal], monkeypatch: pytest.MonkeyPatch
) -> None:
    """El modo sync abre un cliente por llamada, y `AsyncClient()` arma un
    contexto TLS cada vez: medido 0,34 s (cargar el bundle de CAs), fuera de
    cualquier presupuesto. El resolver arma UNO y lo reusa (0,3 ms por cliente)."""
    from uvd_describe_sdk.names import _resolver

    creados: List[int] = []
    real = _resolver.httpx.create_ssl_context

    def contar(*args: Any, **kwargs: Any) -> Any:
        creados.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(_resolver.httpx, "create_ssl_context", contar)
    rpc = servidor(_500)
    with NameResolver(rpc={"eip155:1": rpc.url}, cache=False) as resolver:
        for _ in range(2):
            assert resolver.resolve_sync("ultravioletadao.eth").error == "rpc_unavailable"
    assert len(creados) == 1
