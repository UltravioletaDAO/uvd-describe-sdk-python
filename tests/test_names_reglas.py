"""Las reglas del resolver de nombres, una por test, contra datos REALES.

Cada regla se ata a una grabación (o a una composición de grabaciones) y no a una
respuesta escrita a mano: una respuesta inventada prueba la idea de quien la
escribió. Las que no necesitan red (normalización, sistemas no soportados) usan
un transporte que REVIENTA si alguien lo toca — así «no hubo red» es un hecho
comprobado y no una suposición.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List

import httpx
import pytest

import uvd_describe_sdk as sdk
import uvd_describe_sdk.names as names
from uvd_describe_sdk import (
    DescribeClient,
    DescribeHTTPError,
    NameNotVerifiedError,
    NameResolution,
    parse_name_resolution,
    require_onchain_address,
)
from uvd_describe_sdk.names import InvalidNameError, NameResolver, _abi, _ens
from uvd_describe_sdk.names._proto import Outcome

from .names_replay import FAKE_RPC, Replay, drive, failing_transport, load, resolver_for

JESSE = "0x2211d1D0020DAEA8039E46Cf1367962070d77DA9"
ULTRAVIOLETA = "0xe4dc963c56979E0260fc146b87eE24F18220e545"


def _run(name: str, **overrides: Any) -> NameResolution:
    fixture = load(name)
    replay = Replay(fixture["exchanges"])
    with resolver_for(fixture, replay, **overrides) as resolver:
        result = getattr(resolver, f"{fixture['op']}_sync")(*fixture["args"])
    replay.assert_consumed()
    return result  # type: ignore[no-any-return]


def _offline(**kwargs: Any) -> NameResolver:
    return NameResolver(rpc=FAKE_RPC, transport=failing_transport(), **kwargs)


# ---------------------------------------------------------------------------
# 1. Nunca la dirección cero
# ---------------------------------------------------------------------------


def test_un_resolver_real_que_contesta_la_direccion_cero_da_not_found() -> None:
    """`default.reverse` lo atiende el DefaultReverseResolver de ENSIP-19, y su
    `addr()` devuelve `address(0)` (medido: `AbstractReverseResolver.resolve`).
    Es una dirección cero REAL, de un contrato real — y un pago ahí se quema."""
    fixture = load("resolve_default_reverse_direccion_cero")
    grabada = bytes.fromhex(fixture["exchanges"][-1]["response"]["result"][2:])
    # `resolve()` de ENSIP-10 envuelve la respuesta en un `bytes`; adentro, la
    # palabra de `addr()`.
    (interna,) = _abi.decode(["bytes"], grabada)
    assert interna == b"\x00" * 32, "la grabación tiene que ser la dirección cero de verdad"

    result = _run("resolve_default_reverse_direccion_cero")
    assert result.address is None
    assert result.error == "not_found"
    assert "zero address" in (result.detail or "")


def test_la_capa_http_tampoco_deja_pasar_la_direccion_cero() -> None:
    body = load("resolve_ultravioletadao_eth")["result"]
    body = {**body, "address": "0x" + "0" * 40}
    assert parse_name_resolution(body).address is None


# ---------------------------------------------------------------------------
# 2. Un reverse falso es reverse_mismatch, y el nombre NO se devuelve
# ---------------------------------------------------------------------------


def test_un_reverse_no_normalizado_es_reverse_mismatch() -> None:
    """Grabado: `0xd02a…a513` reclama `0x5e405F9e…hooks.cow.eth`, con la dirección
    en mayúsculas. No está en forma normal ENSIP-15, y así es como entran los
    parecidos (`vitaIik.eth` con I mayúscula). No se muestra."""
    result = _run("reverse_cow_hook_no_normalizado")
    assert result.error == "reverse_mismatch"
    assert result.normalized is None, "el nombre reclamado NO puede salir en el resultado"
    assert "hooks.cow.eth" not in repr(result)


def test_un_nombre_que_apunta_a_OTRA_direccion_es_reverse_mismatch() -> None:
    """La rama de la comparación, con datos reales: el forward GRABADO de
    `0xultravioleta.eth` (apunta a 0xe4dc…545) confrontado con la dirección de
    Jesse. Nada inventado: la respuesta es la de la cadena, y lo que se prueba es
    que el SDK la compare con la dirección que se le preguntó."""
    fixture = load("resolve_0xultravioleta_eth")
    replay = Replay(fixture["exchanges"])
    with pytest.raises(Outcome) as exc:
        drive(
            _ens.confirm("0xultravioleta.eth", JESSE, float(fixture["now"]), _ens.COIN_TYPE_ETH),
            httpx.MockTransport(replay),
            timeout=10,
        )
    replay.assert_consumed()
    assert exc.value.code == "reverse_mismatch"
    assert "points elsewhere" in exc.value.detail


def test_y_con_SU_direccion_la_misma_grabacion_confirma() -> None:
    """El par del anterior: sin él, un `confirm` que siempre dijera mismatch
    pasaría el test de arriba en verde."""
    fixture = load("resolve_0xultravioleta_eth")
    replay = Replay(fixture["exchanges"])
    name = drive(
        _ens.confirm("0xultravioleta.eth", ULTRAVIOLETA, float(fixture["now"]), _ens.COIN_TYPE_ETH),
        httpx.MockTransport(replay),
        timeout=10,
    )
    assert name == "0xultravioleta.eth"


# ---------------------------------------------------------------------------
# 3. Lo que llega por una API HTTP no está verificado on-chain
# ---------------------------------------------------------------------------


def _api_client(body: Dict[str, Any], seen: List[httpx.Request]) -> DescribeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return DescribeClient(transport=httpx.MockTransport(handler), jitter=0)


def test_la_api_http_devuelve_verified_onchain_False_aunque_el_servidor_diga_True() -> None:
    """El cuerpo es el `to_dict()` del resultado ON-CHAIN grabado — lo que el
    servidor serviría, con `verified_onchain: true`. Este proceso no leyó la
    cadena: sale `False`, y lo que el servidor afirmó queda en `raw`."""
    servido = load("resolve_ultravioletadao_eth")["result"]
    assert servido["verified_onchain"] is True
    seen: List[httpx.Request] = []
    with _api_client(servido, seen) as describe:
        result = describe.names.resolve("ultravioletadao.eth")
    assert result is not None
    assert result.address == ULTRAVIOLETA
    assert result.verified_onchain is False
    assert result.raw is not None and result.raw["verified_onchain"] is True
    assert seen[0].url.path == "/v1/names/resolve"
    assert seen[0].url.params["name"] == "ultravioletadao.eth"


def test_un_destino_de_pago_exige_verified_onchain() -> None:
    servido = load("resolve_ultravioletadao_eth")["result"]
    with _api_client(servido, []) as describe:
        por_http = describe.names.resolve("ultravioletadao.eth")
    assert por_http is not None
    with pytest.raises(NameNotVerifiedError) as exc:
        require_onchain_address(por_http)
    assert exc.value.reason == "not_verified_onchain"

    on_chain = _run("resolve_ultravioletadao_eth")
    assert require_onchain_address(on_chain) == ULTRAVIOLETA


def test_el_rechazo_del_gate_no_es_un_DescribeError() -> None:
    """Mismo criterio que `CaveatsNotComputedError`: un `except DescribeError`
    tolerante a caídas no puede convertir «no pagues» en «siga»."""
    assert not issubclass(NameNotVerifiedError, sdk.DescribeError)


def test_un_nombre_inexistente_no_es_pagable_y_dice_por_que() -> None:
    result = _run("resolve_0xultravioletadao_eth_no_existe")
    with pytest.raises(NameNotVerifiedError) as exc:
        require_onchain_address(result)
    assert exc.value.reason == "not_found"


def test_la_capa_http_es_una_ruta_gratis_y_hace_fail_open_observado() -> None:
    """R5: la ruta no está desplegada todavía (la hace describe-net en otro
    encargo) → 404 → `None` OBSERVADO, nunca un `not_found` fabricado."""
    vistos: List[Exception] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    with DescribeClient(
        transport=httpx.MockTransport(handler), jitter=0, on_error=vistos.append
    ) as describe:
        assert describe.names.resolve("ultravioletadao.eth") is None
        assert describe.names.reverse(ULTRAVIOLETA) is None
    assert len(vistos) == 2 and all(isinstance(e, DescribeHTTPError) for e in vistos)

    with DescribeClient(transport=httpx.MockTransport(handler), jitter=0, fail_open=False) as d:
        with pytest.raises(DescribeHTTPError):
            d.names.resolve("ultravioletadao.eth")


def test_las_firmas_de_la_capa_http_son_nullables() -> None:
    for metodo in ("resolve", "reverse"):
        anotacion = str(inspect.signature(getattr(sdk.DescribeNames, metodo)).return_annotation)
        assert anotacion.startswith("Optional["), f"{metodo} -> {anotacion}"


# ---------------------------------------------------------------------------
# 4. Un nombre inexistente es not_found, no una excepción
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["resolve_0xultravioletadao_eth_no_existe", "resolve_uns_no_existe"],
)
def test_not_found_es_una_respuesta(name: str) -> None:
    result = _run(name)
    assert result.error == "not_found"
    assert result.address is None
    assert result.verified_onchain is True, "la ausencia también se leyó de la cadena"


def test_un_nombre_vencido_no_se_resuelve_aunque_sus_registros_sigan_ahi() -> None:
    """Grabado: `avvy.avax` venció el 2026-05-28. Sus registros siguen on-chain;
    por eso NO se consultan: el vencimiento se lee primero."""
    result = _run("resolve_avvy_avax_vencido")
    assert result.error == "expired"
    assert result.address is None
    assert "2026-05-28" in (result.detail or "")


# ---------------------------------------------------------------------------
# 5. Lo que se decide sin red
# ---------------------------------------------------------------------------


#: Nota: `-.eth` NO está en la lista a propósito — ENSIP-15 lo acepta (sólo
#: prohíbe `--` en las posiciones 3-4). Medido con `ens-normalize` 3.0.10.
@pytest.mark.parametrize(
    "raw",
    [
        "a..eth",
        "xn--ab.eth",
        "vitalik",
        "",
        "0xabc",
        "a b.eth",
        "a_b.eth",
        "ab" + chr(0x200D) + "cd.eth",
    ],
)
def test_un_nombre_invalido_es_invalid_name_y_no_toca_la_red(raw: str) -> None:
    with _offline() as resolver:
        result = resolver.resolve_sync(raw)
    assert result.error == "invalid_name"
    assert result.address is None and result.normalized is None
    with pytest.raises(InvalidNameError) as exc:
        _offline().normalize(raw)
    assert exc.value.code == "invalid_name"


def _ancho(texto: str) -> str:
    """El mismo texto en letras de ancho completo (U+FF21…), sin literales raros."""
    return "".join(chr(ord(c) - ord("A") + 0xFF21) if c.isupper() else c for c in texto)


def test_la_normalizacion_es_ENSIP15_y_no_lower() -> None:
    """Execution Market sólo bajaba a minúsculas (`client.py:147-158`). ENSIP-15
    además pliega las letras de ancho completo — y `str.lower()` no:
    `"ＥＴＨ".lower()` es `"ｅｔｈ"`, otro nombre y otro nodo."""
    resolver = _offline()
    assert resolver.normalize("Vitalik.ETH") == "vitalik.eth"
    ancho = "vitalik." + _ancho("ETH")
    assert ancho.lower() != "vitalik.eth"
    assert resolver.normalize(ancho) == "vitalik.eth"
    # Y el sistema se decide sobre el nombre YA normalizado: es `ens`, no DNS.
    assert resolver.detect(ancho) == "ens"


def test_un_punto_de_ancho_completo_NO_es_un_separador() -> None:
    """Medido con ens-normalize 3.0.10: U+FF0E y U+3002 son DISALLOWED."""
    for punto in (chr(0xFF0E), chr(0x3002)):
        with _offline() as resolver:
            assert resolver.resolve_sync("vitalik" + punto + "eth").error == "invalid_name"


def test_sol_es_unsupported_system_sin_red_y_dice_por_que() -> None:
    with _offline() as resolver:
        result = resolver.resolve_sync("bonfida.sol")
    assert result.error == "unsupported_system"
    assert result.system == "sns" and result.family == "solana"
    assert "SRS" in (result.detail or "")


def test_un_sistema_deshabilitado_es_unsupported_system() -> None:
    with _offline(systems=("ens",)) as resolver:
        assert resolver.resolve_sync("miniholder.avax").error == "unsupported_system"


def test_el_avatar_es_de_la_familia_ENS() -> None:
    with _offline() as resolver:
        assert resolver.avatar_sync("brad.crypto").error == "unsupported_system"


def test_sin_rpc_para_la_cadena_es_rpc_unavailable() -> None:
    with NameResolver(rpc={}, transport=failing_transport()) as resolver:
        result = resolver.resolve_sync("ultravioletadao.eth")
    assert result.error == "rpc_unavailable"
    assert "eip155:1" in (result.detail or "")


def test_ninguna_url_de_rpc_aparece_en_un_resultado() -> None:
    """🔴 Las URLs de RPC suelen llevar la llave en el path. Con un transporte que
    falla, el detalle nombra la CADENA, nunca el endpoint."""
    secreto = "https://rpc.test/eip155-1/LLAVE-DE-MENTIRA"
    boom = failing_transport(httpx.ConnectError("boom"))
    with NameResolver(rpc={"eip155:1": secreto}, transport=boom) as resolver:
        result = resolver.resolve_sync("ultravioletadao.eth")
    assert result.error == "rpc_unavailable"
    assert "LLAVE-DE-MENTIRA" not in repr(result) and "rpc.test" not in repr(result)


def test_reverse_con_un_sistema_de_arriba_caido_no_contesta_con_uno_de_abajo() -> None:
    """Orden estricto: si ENS en L1 no se pudo preguntar, el nombre principal
    podría estar ahí; contestar con el de Basenames sería contestar otra cosa.
    Sale `rpc_unavailable` (que no se cachea) y NO se pregunta a Base."""
    pedidas: List[str] = []

    def caido(request: httpx.Request) -> httpx.Response:
        pedidas.append(str(request.url))
        raise httpx.ConnectError("L1 caído")

    with NameResolver(
        rpc=FAKE_RPC, systems=("ens", "basenames"), transport=httpx.MockTransport(caido)
    ) as resolver:
        result = resolver.reverse_sync(JESSE)
    assert result.error == "rpc_unavailable"
    assert result.system == "ens" and result.tried == ("ens",)
    assert result.normalized is None
    assert all("eip155-8453" not in u for u in pedidas), "no se tenía que preguntar a Base"
    assert resolver.cache is not None and len(resolver.cache) == 0


def test_las_claves_de_rpc_son_CAIP2() -> None:
    with pytest.raises(ValueError, match="CAIP-2"):
        NameResolver(rpc={"ethereum": "https://x.example"})


def test_reverse_de_algo_que_no_es_una_direccion() -> None:
    with _offline() as resolver:
        assert resolver.reverse_sync("vitalik.eth").error == "invalid_name"
        cero = resolver.reverse_sync("0x" + "0" * 40)
    assert cero.error == "not_found" and cero.address is None


# ---------------------------------------------------------------------------
# 6. R2 también acá: ningún público devuelve un número pelado
# ---------------------------------------------------------------------------


def test_ninguna_superficie_de_names_devuelve_un_numero() -> None:
    numeros = {"int", "float", "Optional[int]", "Optional[float]"}
    violaciones = []
    for nombre in names.__all__:
        objeto = getattr(names, nombre)
        funciones = []
        if inspect.isclass(objeto):
            funciones = [
                f
                for n, f in vars(objeto).items()
                if inspect.isfunction(f) and not n.startswith("_")
            ]
        elif inspect.isfunction(objeto):
            funciones = [objeto]
        for fn in funciones:
            anotacion = str(inspect.signature(fn).return_annotation).replace(" ", "")
            if anotacion in numeros:
                violaciones.append(f"{nombre}.{fn.__name__} -> {anotacion}")
    assert not violaciones, violaciones


def test_las_variantes_async_y_sync_existen_para_las_cuatro_consultas() -> None:
    for op in ("resolve", "reverse", "text", "avatar"):
        assert asyncio.iscoroutinefunction(getattr(NameResolver, op)), op
        assert not asyncio.iscoroutinefunction(getattr(NameResolver, f"{op}_sync")), op
