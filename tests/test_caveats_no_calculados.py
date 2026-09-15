"""`caveats_not_computed` y `require_full_caveats()` — la puerta gratis DECLARA lo que no calcula.

De dónde sale: la fila del 2026-08-31 de `describe-net/docs/BACKLOG.md:221`
(hallazgo de karma-hello): un gate de calidad armado sobre la ruta gratis pasa
siempre en verde, porque los caveats de calidad de evidencia sólo los calcula la
descomposición paga. El servicio lo cerró el 2026-09-14 declarando, por respuesta,
la LISTA de codes que no evaluó; la fila upstream-first (`BACKLOG.md:19`) le pide
a este SDK tiparla y darle el gate que puede reprobar.

════════════════════════════════════════════════════════════════════════════
🔴 LO QUE HACE DISCRIMINANTE A ESTE ARCHIVO: LOS TRES ESTADOS, MONTADOS
════════════════════════════════════════════════════════════════════════════

    [codes]  declarado, no verificado  → el gate levanta, `not_computed` = codes
    []       declarado, nada afuera    → el gate devuelve el MISMO objeto
    None     NO declarado (API vieja)  → el gate levanta, `not_computed` is None

Un parser que colapse `None` en `[]` —`list(x or [])`, la línea obvia— deja verde
a todo test que sólo mire la captura viva, porque la viva SIEMPRE trae la lista.
Lo que lo pone rojo es montar la API vieja, que es justo la mitad que nadie
captura porque ya no se sirve. Y el `[]` no se sirve hoy en ninguna puerta gratis
(las dos capturas declaran siete): si no se monta a mano, el único estado que
PASA el gate quedaría sin test.

Las mutaciones que se vieron rojas están anotadas en la tabla de `CLAUDE.md`.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx
import pytest

import uvd_describe_sdk as sdk
from uvd_describe_sdk import (
    KNOWN_CAVEAT_CODES,
    CaveatCode,
    CaveatsNotComputedError,
    DescribeClient,
    DescribeError,
    WalletReputation,
    require_full_caveats,
)
from uvd_describe_sdk.models import parse_breakdown, parse_wallet_reputation

from .conftest import (
    BREAKDOWN,
    OPENAPI_ESQUEMAS,
    WALLET_CHAINS_VIVA,
    WALLET_CON_REPUTACION,
    WALLET_DESCONOCIDA_VIVA,
    json_response,
)


def _con(valor: Any) -> Dict[str, Any]:
    """La captura viva con `caveats_not_computed` reemplazado por `valor`."""
    body = dict(WALLET_CHAINS_VIVA)
    body["caveats_not_computed"] = valor
    return body


def _sin_el_campo() -> Dict[str, Any]:
    """La captura viva SIN la clave: una API anterior al 2026-09-14, y nada más
    distinto — así lo único que cambia entre los dos casos es la declaración."""
    body = dict(WALLET_CHAINS_VIVA)
    del body["caveats_not_computed"]
    return body


#: Las tres formas de «no declaró». La segunda es una captura REAL de antes del
#: campo (2026-08-30), no una construida.
NO_DECLARADAS = [
    pytest.param(_sin_el_campo(), id="viva-sin-la-clave"),
    pytest.param(WALLET_CON_REPUTACION, id="captura-2026-08-30"),
    pytest.param(_con(None), id="null-explicito"),
]


# ---------------------------------------------------------------------------
# 1. Lo que el servicio manda, tipado
# ---------------------------------------------------------------------------


def test_la_captura_viva_se_tipa_entera() -> None:
    declarados = WALLET_CHAINS_VIVA["caveats_not_computed"]
    assert declarados, "la captura tiene que declarar algo; si no, este test no prueba nada"
    rep = parse_wallet_reputation(WALLET_CHAINS_VIVA)
    assert rep.caveats_not_computed == declarados
    # El silencio que el campo desmiente: la MISMA respuesta no disparó ningún
    # caveat. Sin la declaración, esto se leía «sin observaciones».
    assert rep.caveats == []


@pytest.mark.parametrize(
    "captura",
    [WALLET_CHAINS_VIVA, WALLET_DESCONOCIDA_VIVA],
    ids=["con-reputacion", "desconocida"],
)
def test_cada_code_declarado_es_uno_que_el_sdk_conoce(captura: Dict[str, Any]) -> None:
    """Al recapturar, un code nuevo en la declaración se pone rojo ACÁ, que es
    donde hay que venir a leer qué corte nombra.

    Y `facilitator-authored` no puede aparecer: es scope agente, y declararlo
    «no calculado» sobre una wallet sugeriría que la paga de wallet lo calcula
    (`describenet/caveats.py:499-502` en `01f6c4a`).
    """
    declarados = set(captura["caveats_not_computed"])
    assert declarados <= KNOWN_CAVEAT_CODES, declarados - KNOWN_CAVEAT_CODES
    assert CaveatCode.FACILITATOR_AUTHORED not in declarados


def test_la_declaracion_no_depende_del_sujeto() -> None:
    """Nombres de cortes, nunca su resultado: la wallet con 648 reviews y la que
    el índice no conoce declaran lo mismo. Si dependiera del sujeto, la lista
    filtraría algo de lo que se vende en la ruta paga."""
    conocida = parse_wallet_reputation(WALLET_CHAINS_VIVA)
    desconocida = parse_wallet_reputation(WALLET_DESCONOCIDA_VIVA)
    assert conocida.identity_count > 0 and desconocida.identity_count == 0
    assert conocida.caveats_not_computed == desconocida.caveats_not_computed


def test_el_esquema_vivo_lo_declara_requerido_y_lista_de_strings() -> None:
    """Hoy el servicio lo manda SIEMPRE. El `None` del SDK no es para la API
    actual: es para una vieja, un respaldo o un valor ilegible."""
    esquema = OPENAPI_ESQUEMAS["WalletChains"]
    assert "caveats_not_computed" in esquema["required"]
    campo = esquema["properties"]["caveats_not_computed"]
    assert campo["type"] == "array"
    assert campo["items"] == {"type": "string"}


def test_viaja_por_wallet_de_punta_a_punta(make_client: Any) -> None:
    cliente = make_client(lambda req: json_response(WALLET_CHAINS_VIVA), jitter=0.0)
    rep = cliente.wallet(WALLET_CHAINS_VIVA["wallet"])
    assert rep is not None
    assert rep.caveats_not_computed == WALLET_CHAINS_VIVA["caveats_not_computed"]
    # Y lo que esta ruta sirve sin tipar sigue llegando por `raw`.
    assert "freshness" in rep.raw


# ---------------------------------------------------------------------------
# 2. 🔴 `None` NO es `[]`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", NO_DECLARADAS)
def test_una_API_que_no_declara_da_None_y_NUNCA_lista_vacia(body: Dict[str, Any]) -> None:
    """`[]` afirmaría «lo calculé todo» sobre una respuesta que no dijo nada — la
    misma frase con la que el servicio rechaza ese colapso en su tool MCP
    (`describenet/mcp_server.py:896-901`)."""
    assert parse_wallet_reputation(body).caveats_not_computed is None


def test_una_lista_vacia_declarada_es_lista_vacia_y_no_None() -> None:
    rep = parse_wallet_reputation(_con([]))
    assert rep.caveats_not_computed == []
    assert rep.caveats_not_computed is not None


@pytest.mark.parametrize(
    "valor",
    ["7", 7, {"codes": ["single-rater"]}, [None], [""], [42], ["single-rater", None]],
    ids=repr,
)
def test_una_declaracion_ilegible_es_None_y_no_una_lista_filtrada(valor: Any) -> None:
    """🔴 `[None]` filtrado da `[]`, y `[]` es el único valor que hace PASAR al
    gate. Por eso lo ilegible se lee «no declaró», nunca «declaró menos»."""
    rep = parse_wallet_reputation(_con(valor))
    assert rep.caveats_not_computed is None
    # Y la nota al pie no tumba la lectura.
    assert rep.global_score == WALLET_CHAINS_VIVA["global_score"]


# ---------------------------------------------------------------------------
# 3. El gate, en sus tres estados
# ---------------------------------------------------------------------------


def test_el_gate_levanta_con_la_respuesta_viva_y_trae_los_codes() -> None:
    rep = parse_wallet_reputation(WALLET_CHAINS_VIVA)
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed == WALLET_CHAINS_VIVA["caveats_not_computed"]
    assert info.value.wallet == rep.wallet


@pytest.mark.parametrize("body", NO_DECLARADAS)
def test_el_gate_NO_pasa_con_una_API_que_no_declaro(body: Dict[str, Any]) -> None:
    """La decisión del `None`, fijada. Si alguien lo deja pasar «porque no dijo
    que faltara nada», el gate de la fila del 2026-08-31 vuelve, en silencio."""
    rep = parse_wallet_reputation(body)
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed is None


def test_el_gate_deja_pasar_solo_la_lista_vacia_y_devuelve_el_mismo_objeto() -> None:
    rep = parse_wallet_reputation(_con([]))
    assert require_full_caveats(rep) is rep


def test_el_gate_con_una_declaracion_ilegible_no_pasa() -> None:
    rep = parse_wallet_reputation(_con([None]))
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed is None


@pytest.mark.parametrize("valor", [(), "", 0, False, {}, set()], ids=repr)
def test_el_gate_deja_pasar_la_lista_vacia_y_NINGUN_otro_vacio(valor: Any) -> None:
    """🔴 Pasa `[]`, no «cualquier cosa falsa». El parser nunca arma estas formas
    (lo que no es lista sale `None`), pero un `WalletReputation` construido a mano
    —un `fallback_reader`, un consumidor que lo rearma— sí puede traerlas, y un
    `if not declared` las dejaba pasar a las seis. El gemelo TypeScript las
    rechaza todas: lo que no es una lista no declaró nada, así que `not_computed`
    es `None`."""
    rep = WalletReputation(
        wallet=WALLET_CHAINS_VIVA["wallet"],
        identity_count=1,
        global_score=71.5,
        caveats_not_computed=valor,
    )
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed is None


def test_un_respaldo_no_declara_y_el_gate_no_lo_deja_pasar() -> None:
    """El caso que el `None` cubre y ninguna API vieja: el índice se cae, el
    `fallback_reader` del consumidor contesta, y ese objeto no sabe qué calculó
    describe — porque describe no contestó."""

    def respaldo(direccion: str) -> WalletReputation:
        return WalletReputation(wallet=direccion, identity_count=1, global_score=71.5)

    cliente = DescribeClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(503)),
        jitter=0.0,
        fallback_reader=respaldo,
    )
    rep = cliente.wallet(WALLET_CHAINS_VIVA["wallet"])
    assert rep is not None and rep.source == "fallback"
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed is None


def test_la_lista_del_error_es_una_copia() -> None:
    rep = parse_wallet_reputation(WALLET_CHAINS_VIVA)
    with pytest.raises(CaveatsNotComputedError) as info:
        require_full_caveats(rep)
    assert info.value.not_computed is not None
    info.value.not_computed.clear()
    assert rep.caveats_not_computed == WALLET_CHAINS_VIVA["caveats_not_computed"]


@pytest.mark.parametrize(
    "no_es",
    [None, parse_breakdown(BREAKDOWN), WALLET_CHAINS_VIVA],
    ids=["None-sin-respuesta", "Breakdown-pago", "dict-crudo"],
)
def test_el_gate_solo_acepta_un_WalletReputation(no_es: Any) -> None:
    """Un `TypeError` que nombra el error, y no un `AttributeError` de rebote: el
    `None` de `wallet()` es «no hubo respuesta» (R5), y confundirlo con «no
    declaró» es la confusión que R1 persigue."""
    with pytest.raises(TypeError, match="WalletReputation"):
        require_full_caveats(no_es)


# ---------------------------------------------------------------------------
# 4. El error: fuera de R4, y con una recuperación que no interpola
# ---------------------------------------------------------------------------


def test_el_error_NO_es_un_DescribeError_y_el_fail_open_del_consumidor_no_lo_traga() -> None:
    """🔴 Si heredara de `DescribeError`, el `except` que un consumidor ya escribió
    para tolerar caídas lo convertiría en «describe no contestó» — y un gate
    tolerante a caídas deja PASAR."""
    assert not issubclass(CaveatsNotComputedError, DescribeError)

    def gate_de_un_consumidor(rep: WalletReputation) -> Any:
        try:
            return require_full_caveats(rep)
        except DescribeError:
            return None  # «describe está caído»: la rama que un gate tolerante aprueba

    with pytest.raises(CaveatsNotComputedError):
        gate_de_un_consumidor(parse_wallet_reputation(WALLET_CHAINS_VIVA))


def test_la_recovery_nombra_la_ruta_que_si_los_evalua() -> None:
    """Los ANCLAJES, no la redacción — el mismo criterio que `test_recovery.py`."""
    texto = CaveatsNotComputedError.__dict__["recovery"]
    for anclaje in ("wallet_breakdown()", "payer=", "partner=", "fallback_reader"):
        assert anclaje in texto, anclaje


def test_la_recovery_es_una_constante_y_no_interpola_nada() -> None:
    secreto = "https://rpc.invalid/v2/CLAVE-FALSA-DE-TEST-NO-ES-REAL"
    exc = CaveatsNotComputedError(secreto, wallet=secreto, not_computed=[secreto])
    assert secreto not in exc.recovery
    assert exc.recovery is CaveatsNotComputedError.__dict__["recovery"]


def test_se_exporta_desde_el_paquete() -> None:
    for nombre in ("require_full_caveats", "CaveatsNotComputedError"):
        assert nombre in sdk.__all__, nombre
