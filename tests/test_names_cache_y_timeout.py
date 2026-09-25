"""La caché de nombres y el timeout duro — lo que las dos copias viejas hacían mal.

Execution Market guardaba en un `dict` sin tope y con el mismo TTL para los
negativos (`client.py:114-129`); karma-hello, sin tope y una hora
(`domain_resolver.py:167-193`). Y ninguna distinguía «no pude preguntar» de «no
existe» a la hora de guardar. Estos tests fijan las tres propiedades, y el
timeout que karma-hello sí había medido (`:225-232`).
"""

from __future__ import annotations

import asyncio
import time
from typing import List

import httpx
import pytest

from uvd_describe_sdk.names import NameCache, NameResolution, NameResolver

from .names_replay import FAKE_RPC, Replay, answer_chain_id, load, resolver_for


def _resultado(error: object = None) -> NameResolution:
    return NameResolution(
        input="x.eth",
        normalized="x.eth",
        address=None if error else "0x" + "1" * 40,
        family="evm",
        system="ens",
        verified_onchain=True,
        error=error,  # type: ignore[arg-type]
    )


class Reloj:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------------------
# NameCache
# ---------------------------------------------------------------------------


def test_la_cache_tiene_tope_y_expulsa_al_menos_usado() -> None:
    cache = NameCache(max_entries=2)
    cache.put("a", _resultado())
    cache.put("b", _resultado())
    assert cache.get("a") is not None  # «a» pasa a ser el más reciente
    cache.put("c", _resultado())
    assert len(cache) == 2
    assert cache.get("b") is None, "el menos usado tenía que salir"
    assert cache.get("a") is not None and cache.get("c") is not None


def test_positivos_y_negativos_tienen_vidas_distintas() -> None:
    reloj = Reloj()
    cache = NameCache(ttl=300, negative_ttl=60, clock=reloj)
    cache.put("si", _resultado())
    cache.put("no", _resultado("not_found"))
    reloj.t += 61
    assert cache.get("no") is None, "un «no existe» no puede durar lo que un sí"
    assert cache.get("si") is not None
    reloj.t += 240
    assert cache.get("si") is None


@pytest.mark.parametrize("error", ["not_found", "expired", "reverse_mismatch"])
def test_las_respuestas_negativas_se_guardan(error: str) -> None:
    cache = NameCache()
    cache.put("k", _resultado(error))
    assert cache.get("k") is not None


@pytest.mark.parametrize("error", ["rpc_unavailable", "invalid_name", "unsupported_system"])
def test_lo_que_no_es_una_respuesta_de_la_cadena_nunca_se_guarda(error: str) -> None:
    """🔴 Guardar «no pude preguntar» convierte un parpadeo de treinta segundos
    del RPC en minutos de respuestas falsas."""
    cache = NameCache()
    cache.put("k", _resultado(error))
    assert cache.get("k") is None and len(cache) == 0


def test_un_acierto_de_cache_no_toca_la_red_y_se_marca() -> None:
    fixture = load("resolve_ultravioletadao_eth")
    replay = Replay(fixture["exchanges"])
    with resolver_for(fixture, replay, cache=True) as resolver:
        primero = resolver.resolve_sync("ultravioletadao.eth")
        # La grabación ya se consumió entera: si el segundo pidiera algo, el
        # reproductor reventaría.
        segundo = resolver.resolve_sync("UltravioletaDAO.eth")
    replay.assert_consumed()
    assert primero.cached is False and segundo.cached is True
    assert segundo.address == primero.address
    assert segundo.input == "UltravioletaDAO.eth", "la entrada es la de ESTA llamada"


def test_un_fallo_de_transporte_no_se_cachea_y_la_siguiente_pregunta_de_nuevo() -> None:
    fixture = load("resolve_ultravioletadao_eth")
    replay = Replay(fixture["exchanges"])
    fallos: List[int] = [1]

    def handler(request: httpx.Request) -> httpx.Response:
        if fallos:
            fallos.pop()
            raise httpx.ConnectError("el RPC parpadeó")
        return replay(request)

    transport = httpx.MockTransport(handler)
    with NameResolver(
        rpc=FAKE_RPC, transport=transport, clock=lambda: float(fixture["now"])
    ) as resolver:
        caida = resolver.resolve_sync("ultravioletadao.eth")
        vuelta = resolver.resolve_sync("ultravioletadao.eth")
    replay.assert_consumed()
    assert caida.error == "rpc_unavailable" and caida.cached is False
    assert vuelta.error is None and vuelta.cached is False
    assert vuelta.address == "0xe4dc963c56979E0260fc146b87eE24F18220e545"


# ---------------------------------------------------------------------------
# El timeout duro: un presupuesto para la llamada ENTERA
# ---------------------------------------------------------------------------


def test_sync_pasado_el_presupuesto_no_sale_otra_request() -> None:
    """Cada request tarda 0,3 s y el presupuesto es 0,2 s: la primera sale (no
    hay forma de cortarla desde afuera en sync sin hilos), la segunda ya no.

    El doble es SINTÉTICO a propósito — no describe la cadena, describe un RPC
    lento: contesta a todo con un número enorme, que para `nameExpires` es «vence
    en el futuro» y obliga al resolver a pedir el paso siguiente."""
    salidas: List[str] = []

    def lento(request: httpx.Request) -> httpx.Response:
        # El `eth_chainId` del motor se contesta en el acto: el test mide el
        # presupuesto sobre los `eth_call`, no sobre el chequeo de cadena.
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            return chain_id
        salidas.append(str(request.url))
        time.sleep(0.3)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x" + "7f" * 32})

    with NameResolver(rpc=FAKE_RPC, transport=httpx.MockTransport(lento), timeout=0.2) as r:
        inicio = time.monotonic()
        result = r.resolve_sync("ultravioletadao.eth")
        duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert "budget" in (result.detail or "")
    assert len(salidas) == 1
    assert duro < 1.0


def test_async_el_timeout_es_duro_aunque_el_servidor_no_conteste() -> None:
    """En async el corte es real: `asyncio.wait_for` cancela la request colgada."""

    async def colgado(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    async def correr() -> NameResolution:
        async with NameResolver(
            rpc=FAKE_RPC, async_transport=httpx.MockTransport(colgado), timeout=0.2
        ) as r:
            return await r.resolve("ultravioletadao.eth")

    inicio = time.monotonic()
    result = asyncio.run(correr())
    assert time.monotonic() - inicio < 2.0
    assert result.error == "rpc_unavailable"
