"""Ronda 5 del PR #6: la arquitectura convergió; esto fija lo que quedaba suelto.

* P0: `await resolve()` con el transporte POR DEFECTO se colgaba para siempre.
  `_async_client()` tomaba `self._lock` (un `threading.Lock`) y armaba el
  cliente, que para el contexto TLS volvía a tomar el MISMO lock. Ningún test
  corría la variante async sin `transport=`: todos pasaban un doble.
* Al vencer el presupuesto se CANCELA lo pendiente, y se ve del lado del
  servidor: la conexión se cierra.
* `transport=` y `async_transport=` distintos se rechazan (antes uno se
  ignoraba en silencio).
* `resolve_sync()` dentro de `trio.run` no revienta.
* El presupuesto cuenta desde que entra la llamada, armado del cliente incluido.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any, Awaitable, Callable, Coroutine, Dict

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver

from .names_replay import FAKE_RPC
from .names_servidor import Responder, ServidorLocal, cuerpo_goteando

#: Ninguna de estas llamadas debería tardar más de ~0,1 s contra un 500 local.
LIMITE_S = 5.0


def _500(conn: socket.socket, _: bytes) -> None:
    conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")


def _en_un_hilo(hacer: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Corre `hacer()` con `asyncio.run` en un hilo aparte y espera `LIMITE_S`.

    No con un `asyncio.wait_for` externo, que era la receta del informe, y es
    medido: el deadlock es de un `threading.Lock` tomado DENTRO de una corrutina,
    así que bloquea el hilo del loop entero y ningún timeout de asyncio llega a
    dispararse — con el lock viejo, `asyncio.run(asyncio.wait_for(resolve(), 5))`
    seguía colgado a los 20 s (py3.13, 2026-09-24). Un hilo que no vuelve sí se
    puede abandonar: es `daemon` y el proceso sale igual.
    """
    caja: Dict[str, Any] = {}

    def correr() -> None:
        try:
            caja["resultado"] = asyncio.run(hacer())
        except BaseException as exc:  # noqa: BLE001 - se re-levanta abajo
            caja["error"] = exc

    hilo = threading.Thread(target=correr, name="test-ronda5", daemon=True)
    hilo.start()
    hilo.join(LIMITE_S)
    assert not hilo.is_alive(), f"colgado: no volvió en {LIMITE_S:g} s"
    if "error" in caja:
        raise caja["error"]
    return caja["resultado"]


@pytest.mark.parametrize(
    "operacion",
    [
        lambda r: r.resolve("ultravioletadao.eth"),
        lambda r: r.reverse("0xe4dc963c56979E0260fc146b87eE24F18220e545"),
        lambda r: r.text("ultravioletadao.eth", "url"),
        lambda r: r.avatar("ultravioletadao.eth"),
    ],
    ids=["resolve", "reverse", "text", "avatar"],
)
def test_P0_la_variante_async_con_el_transporte_por_defecto_no_se_cuelga(
    servidor: Callable[[Responder], ServidorLocal],
    operacion: Callable[[NameResolver], Awaitable[Any]],
) -> None:
    """SIN `transport=`: el camino que usa quien adopta el SDK. Medido por el
    verificador sobre `8326ef2`: colgado en py3.9/3.12/3.13; `3f556d9` volvía en
    0,01-0,08 s. Mutación CA (volver al `threading.Lock`): estos cuatro, rojos."""
    rpc = servidor(_500)

    async def hacer() -> Any:
        async with NameResolver(rpc={"eip155:1": rpc.url}, cache=False) as resolver:
            return await operacion(resolver)

    result = _en_un_hilo(hacer)
    assert result.error == "rpc_unavailable"
    assert rpc.pedidos, "la llamada no llegó al servidor: no probó el transporte por defecto"


def test_al_vencer_se_CANCELA_y_el_servidor_ve_cerrarse_la_conexion(
    servidor: Callable[[Responder], ServidorLocal],
) -> None:
    """Volver a tiempo no alcanza: lo pendiente tiene que morir. Con un
    `asyncio.wait` sin cancelar (mutación CB) la llamada vuelve igual en el
    presupuesto —los tests de tiempo quedan verdes— y la request sigue leyendo
    el goteo en el loop de quien llamó: medido por el verificador, la conexión
    seguía abierta 4 s después. El cliente es el persistente de la variante
    async, así que la conexión sólo se cierra si alguien la cancela."""
    rpc = servidor(cuerpo_goteando)

    async def hacer() -> Any:
        resolver = NameResolver(rpc={"eip155:1": rpc.url}, cache=False, timeout=0.6)
        try:
            result = await resolver.resolve("ultravioletadao.eth")
            volvio = time.monotonic()
            await asyncio.sleep(0.5)  # un huérfano seguiría corriendo en ESTE loop
            return result, volvio, list(rpc.cerradas)
        finally:
            await resolver.aclose()

    result, volvio, cerradas = _en_un_hilo(hacer)
    assert result.error == "rpc_unavailable"
    assert len(rpc.pedidos) == 1
    assert cerradas, "0,5 s después de volver, la conexión seguía abierta: no se canceló"
    assert cerradas[0] - volvio < 0.5


def test_dos_transportes_distintos_se_rechazan_y_el_mismo_se_acepta() -> None:
    """Antes, con los dos, `transport=` se ignoraba en silencio (en las DOS
    variantes: hay un solo motor). Mutación CC: el primer `raises`, rojo."""
    uno = httpx.MockTransport(lambda r: httpx.Response(500))
    otro = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(ValueError, match="ONE transport"):
        NameResolver(rpc=FAKE_RPC, transport=uno, async_transport=otro)
    # Uno sólo-sync al lado de uno async sigue siendo el error de siempre.
    with pytest.raises(ValueError, match="AsyncBaseTransport"):
        NameResolver(
            rpc=FAKE_RPC,
            transport=httpx.HTTPTransport(),  # type: ignore[arg-type]
            async_transport=otro,
        )
    # El mismo objeto en los dos lugares es lo que hacen los tests: se acepta.
    NameResolver(rpc=FAKE_RPC, transport=uno, async_transport=uno)


def test_un_sync_dentro_de_trio_no_revienta(
    servidor: Callable[[Responder], ServidorLocal],
) -> None:
    """Medido por el verificador sobre `8326ef2`: `RuntimeError: Task got bad
    yield` (en `3f556d9`, `rpc_unavailable`). `run_blocking` sólo le preguntaba a
    asyncio si había código async corriendo, y trio no tiene loop de asyncio:
    armaba su loop privado en el hilo de trio y httpx, preguntándole a sniffio,
    usaba primitivas de trio adentro de asyncio. Mutación CD: rojo.

    `trio` está en el extra `dev` a propósito: sin él esto se saltea, y un
    salteo no prueba nada."""
    trio = pytest.importorskip("trio")
    rpc = servidor(_500)
    resolver = NameResolver(rpc={"eip155:1": rpc.url}, cache=False)

    async def en_trio() -> Any:
        return resolver.resolve_sync("ultravioletadao.eth")

    result = trio.run(en_trio)
    assert result.error == "rpc_unavailable"
    assert rpc.pedidos, "la llamada no llegó al servidor"


@pytest.mark.parametrize("variante", ["sync", "async"])
def test_el_presupuesto_cuenta_desde_que_entra_la_llamada(
    monkeypatch: pytest.MonkeyPatch, variante: str
) -> None:
    """Armar el cliente cuesta (medido en la ronda 4: 0,34 s el contexto TLS), y
    ese tiempo es de la llamada. Acá armarlo tarda 0,8 s de un presupuesto de
    1,0 s: la llamada vuelve en ~1,0 s, no en 0,8 + 1,0. Mutación CE
    (`left = self._timeout`): las dos variantes tardan ~1,8 s, rojo."""

    async def colgado(request: httpx.Request) -> httpx.Response:
        # 5 s y no «para siempre»: sin el deadline (mutación BA) este test tiene
        # que FALLAR, no colgar la suite — con 3600 s la dejó colgada 20 min,
        # hasta que se mató el proceso (2026-09-24).
        await asyncio.sleep(5)
        raise AssertionError("el deadline no cortó la request")

    resolver = NameResolver(
        rpc=FAKE_RPC, cache=False, timeout=1.0, async_transport=httpx.MockTransport(colgado)
    )
    real = resolver._new_client

    def lento() -> httpx.AsyncClient:
        time.sleep(0.8)
        return real()

    monkeypatch.setattr(resolver, "_new_client", lento)

    async def en_async() -> Any:
        try:
            return await resolver.resolve("ultravioletadao.eth")
        finally:
            await resolver.aclose()

    inicio = time.monotonic()
    if variante == "sync":
        result = resolver.resolve_sync("ultravioletadao.eth")
    else:
        result = asyncio.run(en_async())
    duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert duro < 1.4, f"presupuesto 1,0 s contado desde la entrada, tardó {duro:.2f} s"
