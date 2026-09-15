"""`ratings[].author_class` y el code `facilitator-authored` — tipados sin cerrar el set.

De dónde sale: la fila del 2026-08-30 de describe-net (la clase de autor por
rating, cerrada el 2026-09-14) y la fila upstream-first
`describe-net/docs/BACKLOG.md:19`. El servicio sirve la clase en cada `Rating` de
`GET /reputation/agent/{n}/{id}` — ruta PAGA: acá no hay captura viva de ese
cuerpo, y no se paga para tenerla. Lo que sí se capturó vivo es el ESQUEMA
(`tests/fixtures/openapi_2026-09-15_WalletChains_Rating.json`), y el set del SDK
se ata contra su `enum`: una tercera clase del servicio se ve roja al recapturar.

════════════════════════════════════════════════════════════════════════════
🔴 LOS DOS BORDES QUE ESTE ARCHIVO FIJA, uno por lado
════════════════════════════════════════════════════════════════════════════

    demasiado cerrado  una clase nueva rompe la lectura o desaparece
                       → `test_una_clase_desconocida_llega_entera_…`
    demasiado abierto  la AUSENCIA se lee como `rater-authored`
                       → `test_una_fila_sin_la_clase_es_None_…`

El segundo es el caro: un default `rater-authored` certificaría como firma del
propio calificador una fila que nadie clasificó, y `rater-authored` ni siquiera
prueba eso cuando el servicio lo manda (`describenet/rating_roles.py:320-325`).
"""

from __future__ import annotations

import typing
from typing import Any, Dict, List

import pytest

import uvd_describe_sdk as sdk
from uvd_describe_sdk import (
    FREE_GATE_CAVEAT_CODES,
    KNOWN_AUTHOR_CLASSES,
    KNOWN_CAVEAT_CODES,
    AuthorClass,
    CaveatCode,
    KnownAuthorClass,
    is_known,
    is_known_author_class,
)
from uvd_describe_sdk.models import parse_agent_reputation

from .conftest import OPENAPI_ESQUEMAS

#: La wallet del facilitador en las mainnets EVM: la única entrada de
#: `FACILITATOR_AUTHORS` (`describenet/rating_roles.py:335-339`, `01f6c4a`).
#: Dirección pública.
FACILITADOR = "0x103040545ac5031a11e8c03dd11324c7333a13c7"
CALIFICADOR = "0x6b51d0d67ff41dab76e499546abe6b8b03cf8732"


def _fila(**extra: Any) -> Dict[str, Any]:
    """Una fila con las claves que el esquema vivo de `Rating` declara REQUERIDAS,
    menos `author_class`, que cada test pone o quita."""
    fila: Dict[str, Any] = {
        "client": CALIFICADOR,
        "feedback_index": 1,
        "value": 100,
        "value_decimals": 0,
        "normalized_value": 100.0,
        "tag1": "trust",
        "tag2": None,
        "is_revoked": False,
        "is_self": False,
        "tx_hash": None,
        "block_number": None,
        "log_index": None,
        "feedback_uri": None,
        "feedback_hash": None,
        "revoked_tx": None,
    }
    fila.update(extra)
    return fila


def _agente(filas: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "network": "base",
        "agent_id": "1197",
        "score": 90.0,
        "review_count": len(filas),
        "caveats": [],
        "ratings": filas,
        "policy_version": "credibility-weight-per-chain@1",
    }


# ---------------------------------------------------------------------------
# 1. El set, atado al esquema vivo
# ---------------------------------------------------------------------------


def test_el_set_del_sdk_es_el_enum_del_esquema_vivo() -> None:
    esquema = OPENAPI_ESQUEMAS["Rating"]
    assert "author_class" in esquema["required"]
    assert KNOWN_AUTHOR_CLASSES == set(esquema["properties"]["author_class"]["enum"])


def test_la_fila_de_mentira_tiene_la_forma_del_esquema_vivo() -> None:
    """Sin esto, `_fila()` podría ser la idea del que la escribió."""
    assert set(_fila()) | {"author_class"} == set(OPENAPI_ESQUEMAS["Rating"]["required"])


def test_el_literal_y_el_set_no_pueden_divergir() -> None:
    assert set(typing.get_args(KnownAuthorClass)) == KNOWN_AUTHOR_CLASSES
    assert {AuthorClass.FACILITATOR_AUTHORED, AuthorClass.RATER_AUTHORED} == KNOWN_AUTHOR_CLASSES


# ---------------------------------------------------------------------------
# 2. Lo que se parsea
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cliente,clase",
    [
        (FACILITADOR, AuthorClass.FACILITATOR_AUTHORED),
        (CALIFICADOR, AuthorClass.RATER_AUTHORED),
    ],
    ids=["facilitador", "calificador"],
)
def test_las_dos_clases_conocidas_se_tipan(cliente: str, clase: str) -> None:
    agente = parse_agent_reputation(_agente([_fila(client=cliente, author_class=clase)]))
    (rating,) = agente.ratings
    assert rating.author_class == clase
    assert is_known_author_class(rating.author_class) is True


def test_una_fila_sin_la_clase_es_None_y_NO_rater_authored() -> None:
    """🔴 Una fila servida antes del 2026-09-14 no trae clase. Leerla como
    `rater-authored` es inventar una clasificación que el índice no hizo."""
    agente = parse_agent_reputation(_agente([_fila()]))
    (rating,) = agente.ratings
    assert rating.author_class is None
    assert is_known_author_class(rating.author_class) is False


def test_una_clase_desconocida_llega_entera_y_no_tumba_la_lectura() -> None:
    """🔴 El borde del set cerrado — el patrón `isKnownCaveatCode` del gemelo
    TypeScript, en Python. El esquema vivo dice `enum` de dos, y el día que el
    servicio agregue un relayer de otra naturaleza una tercera clase tiene que
    llegar, no reventar el parser ni evaporarse."""
    filas = [
        _fila(client=FACILITADOR, author_class=AuthorClass.FACILITATOR_AUTHORED, feedback_index=1),
        _fila(author_class="relayer-authored", feedback_index=2),
        _fila(author_class=AuthorClass.RATER_AUTHORED, feedback_index=3),
    ]
    agente = parse_agent_reputation(_agente(filas))
    assert [r.author_class for r in agente.ratings] == [
        "facilitator-authored",
        "relayer-authored",
        "rater-authored",
    ]
    nueva = agente.ratings[1]
    assert is_known_author_class(nueva.author_class) is False
    assert nueva.raw["author_class"] == "relayer-authored"


@pytest.mark.parametrize("basura", [42, "", None, ["facilitator-authored"]], ids=repr)
def test_una_clase_que_no_es_texto_es_None_y_la_fila_sobrevive(basura: Any) -> None:
    agente = parse_agent_reputation(_agente([_fila(author_class=basura)]))
    (rating,) = agente.ratings
    assert rating.author_class is None
    assert rating.client == CALIFICADOR


def test_filtrar_por_clase_antes_de_contar_calificadores() -> None:
    """El uso que la clase existe para habilitar, con la advertencia del servicio:
    todas las filas del facilitador comparten `client`, así que contarlas por
    `client` junta a N calificadores reales en uno."""
    filas = [
        _fila(client=FACILITADOR, author_class=AuthorClass.FACILITATOR_AUTHORED, feedback_index=i)
        for i in range(1, 4)
    ] + [_fila(author_class=AuthorClass.RATER_AUTHORED, feedback_index=9)]
    agente = parse_agent_reputation(_agente(filas))
    firmadas_por_el_calificador = [
        r for r in agente.ratings if r.author_class == AuthorClass.RATER_AUTHORED
    ]
    assert len({r.client for r in agente.ratings}) == 2  # lo que un conteo ingenuo diría
    assert len(firmadas_por_el_calificador) == 1


# ---------------------------------------------------------------------------
# 3. El code `facilitator-authored`
# ---------------------------------------------------------------------------


def test_el_code_facilitator_authored_es_conocido_y_no_es_de_la_puerta_gratis() -> None:
    assert CaveatCode.FACILITATOR_AUTHORED == "facilitator-authored"
    assert CaveatCode.FACILITATOR_AUTHORED in KNOWN_CAVEAT_CODES
    assert is_known("facilitator-authored") is True
    # Scope agente: la puerta gratis no lo dispara nunca.
    assert CaveatCode.FACILITATOR_AUTHORED not in FREE_GATE_CAVEAT_CODES


def test_el_caveat_del_agente_se_ramifica_por_su_code() -> None:
    body = _agente([_fila(client=FACILITADOR, author_class=AuthorClass.FACILITATOR_AUTHORED)])
    body["caveats"] = [{"code": "facilitator-authored", "text": "Hay ratings en `ratings[]`…"}]
    agente = parse_agent_reputation(body)
    assert CaveatCode.FACILITATOR_AUTHORED in agente.caveat_codes


def test_author_class_no_se_instancia() -> None:
    with pytest.raises(TypeError):
        AuthorClass()


def test_se_exporta_desde_el_paquete() -> None:
    nombres = ("AuthorClass", "KnownAuthorClass", "KNOWN_AUTHOR_CLASSES", "is_known_author_class")
    for nombre in nombres:
        assert nombre in sdk.__all__, nombre
