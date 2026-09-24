"""Rondas 3 y 4 del PR #6: el timeout sync, duro también donde el cuerpo no llega.

El verificador de la ronda 2 midió, con un servidor LOCAL y el transporte httpx
real (presupuesto 1,0 s): headers goteando de a 1 byte cada 0,5 s → 15,2 s; un
gzip con FLG=FCOMMENT y el comentario goteando → 10,15 s. Re-medido antes de
arreglarlo (`cbc93a0`): 11,43 s y 10,15 s — el segundo, encima, «sin error» y con
el cuerpo vacío.

⚠️ Ronda 4: el arreglo de la ronda 3 (un backend de socket para el motor sync)
se BORRÓ. El modo sync es ahora el motor async bajo un deadline duro (decisión de
c0der), y estos mismos tests —los casos A, B, C y D del verificador— son los que
lo prueban: ahora pasan por ese único motor. Se sacó el test del backend borrado;
el resto conserva sus aserciones.

Usan un servidor de verdad en 127.0.0.1 (`names_servidor.py`), porque lo que se
prueba es el reloj contra un socket que tarda: un doble en memoria no tarda.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, List

import httpx
import pytest

from uvd_describe_sdk.names import InvalidNameError, NameResolver, _proto
from uvd_describe_sdk.names._proto import Fetch, Unavailable, run_bounded

from .names_replay import drive
from .names_servidor import (
    Responder,
    ServidorLocal,
    cronometrar,
    cuerpo_goteando,
    gzip_goteando,
    headers_goteando,
)

PRESUPUESTO = 0.6
MARGEN = 0.6


# ---------------------------------------------------------------------------
# El camino del RPC, por el resolver entero y su transporte por defecto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "responder",
    [cuerpo_goteando, headers_goteando, gzip_goteando],
    ids=["A-cuerpo", "C-headers", "D-gzip"],
)
def test_sync_un_RPC_que_gotea_se_corta_en_el_presupuesto(
    servidor: Callable[[Responder], ServidorLocal],
    responder: Responder,
) -> None:
    rpc = servidor(responder)
    resultado: List[Any] = []
    with NameResolver(rpc={"eip155:1": rpc.url}, cache=False, timeout=PRESUPUESTO) as resolver:
        duro = cronometrar(lambda: resultado.append(resolver.resolve_sync("ultravioletadao.eth")))
    assert resultado[0].error == "rpc_unavailable"
    assert duro < PRESUPUESTO + MARGEN, f"presupuesto {PRESUPUESTO} s, tardó {duro:.2f} s"
    assert "127.0.0.1" not in repr(resultado[0]), "el detalle nombra la cadena, nunca la URL"


# ---------------------------------------------------------------------------
# El camino de un gateway (Fetch), con `check_url` parcheado SÓLO acá
# ---------------------------------------------------------------------------


def _pasos(url: str) -> _proto.Step[object]:
    respuesta = yield Fetch(url)
    return respuesta


def _fetch(url: str, timeout: float) -> object:
    return drive(_pasos(url), httpx.AsyncHTTPTransport(), rpc={}, timeout=timeout)


async def _fetch_async(url: str, timeout: float) -> object:
    async with httpx.AsyncClient() as client:
        return await run_bounded(_pasos(url), rpc={}, client=client, timeout=timeout)


@pytest.mark.parametrize(
    "responder", [cuerpo_goteando, headers_goteando], ids=["B-cuerpo", "C-headers"]
)
def test_sync_un_gateway_que_gotea_se_corta_en_el_presupuesto(
    servidor: Callable[[Responder], ServidorLocal],
    responder: Responder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_proto, "check_url", lambda url: None)
    gateway = servidor(responder)
    inicio = time.monotonic()
    with pytest.raises(Unavailable):
        _fetch(gateway.url, PRESUPUESTO)
    duro = time.monotonic() - inicio
    assert duro < PRESUPUESTO + MARGEN, f"presupuesto {PRESUPUESTO} s, tardó {duro:.2f} s"


@pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])
def test_un_gateway_pide_identity_y_un_cuerpo_gzip_no_se_lee(
    servidor: Callable[[Responder], ServidorLocal],
    monkeypatch: pytest.MonkeyPatch,
    asincrono: bool,
) -> None:
    """Parametrizado en la ronda 4 (P3-b): el rechazo vive en el único motor, y
    el verificador pidió verlo por las dos puertas."""
    monkeypatch.setattr(_proto, "check_url", lambda url: None)
    gateway = servidor(gzip_goteando)
    inicio = time.monotonic()
    with pytest.raises(Unavailable, match="gzip-encoded"):
        if asincrono:
            asyncio.run(_fetch_async(gateway.url, 5.0))
        else:
            _fetch(gateway.url, 5.0)
    assert time.monotonic() - inicio < 1.0, "se rechaza en los headers, sin esperar el cuerpo"
    assert b"accept-encoding: identity" in gateway.pedidos[0].lower()


# ---------------------------------------------------------------------------
# Un redirect no se sigue pasado el plazo (P3-1)
# ---------------------------------------------------------------------------


def test_un_redirect_no_se_sigue_pasado_el_presupuesto() -> None:
    """El doble BLOQUEA el loop 0,3 s (un `time.sleep` en el handler), así que la
    cancelación del `wait_for` no llega a tiempo: lo que corta es el chequeo del
    reloj antes de cada redirect. Sin él, el segundo host se pide."""
    pedidas: List[str] = []

    def lento_y_redirige(request: httpx.Request) -> httpx.Response:
        pedidas.append(str(request.url))
        if request.url.host == "uno.example":
            time.sleep(0.3)
            return httpx.Response(302, headers={"location": "https://dos.example/x"})
        return httpx.Response(200, json={"data": "0x"})

    with pytest.raises(Unavailable, match="before a redirect"):
        drive(
            _pasos("https://uno.example/x"),
            httpx.MockTransport(lento_y_redirige),
            rpc={},
            timeout=0.2,
        )
    assert pedidas == ["https://uno.example/x"]


# ---------------------------------------------------------------------------
# La forma se valida ANTES de la colisión (P3-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("malo", ["a b.travel", "x..travel", "a_b.guide"])
def test_un_nombre_mal_formado_bajo_una_colision_es_invalid_name(malo: str) -> None:
    resolver = NameResolver(rpc={})
    with pytest.raises(InvalidNameError):
        resolver.normalize(malo)
    assert resolver.resolve_sync(malo).error == "invalid_name"


def test_un_nombre_en_ancho_completo_bajo_una_colision_se_normaliza_y_sigue_ambiguo() -> None:
    ancho = chr(0xFF58) + ".travel"  # «ｘ.travel»
    resolver = NameResolver(rpc={})
    assert resolver.normalize(ancho) == "x.travel", "nunca el texto crudo en ancho completo"
    assert resolver.resolve_sync(ancho).error == "unsupported_system"
