"""El parser equivocado FALLA RUIDOSO — espejo del guard del gemelo TypeScript.

Las dos rutas de wallet comparten la clave `wallet` en el cuerpo, así que el
parser equivocado no revienta solo: se MEDIÓ el 2026-08-31, antes de escribir
el guard, qué hacía cada cruce:

    cuerpo GRATIS  → `parse_breakdown`          SILENCIOSO: `final_score=None`
    cuerpo PAGO    → `parse_wallet_reputation`  SILENCIOSO: `global_score=None`
    objeto YA PARSEADO → cualquiera de los dos  RUIDOSO: `DescribeUnparseable`
                                                («missing `wallet`»)

Los dos primeros son la mentira cara: un score que existe (y en el caso pago,
que se PAGÓ) evaporado en un `None` que R1 enseña a leer como «todavía sin
calificar». Por eso ganaron guard. El tercero ya reventaba claro, así que acá
sólo se FIJA — si alguien «ayuda» aceptando objetos parseados (por ejemplo vía
`.raw`), este archivo se pone rojo y obliga a discutirlo como cambio de
contrato, no a colarlo.

El discriminador del guard son los DOS marcadores de nivel superior
(`global_score` / `final_score`), nunca uno solo: un payload futuro que traiga
ambos no dispara nada — tolerancia aditiva, la misma que defiende `hashes.py`.
"""

from __future__ import annotations

import pytest

from uvd_describe_sdk import DescribeUnparseable
from uvd_describe_sdk.models import parse_breakdown, parse_wallet_reputation

from .conftest import BREAKDOWN, WALLET_CON_REPUTACION


def test_cuerpo_gratis_al_parser_pago_falla_nombrando_al_correcto() -> None:
    """La dirección del guard TS: el preview gratis no se disfraza de pagado."""
    with pytest.raises(DescribeUnparseable) as exc:
        parse_breakdown(WALLET_CON_REPUTACION)
    assert "parse_wallet_reputation" in str(exc.value)
    assert "FREE" in str(exc.value)


def test_cuerpo_pago_al_parser_gratis_falla_nombrando_al_correcto() -> None:
    """La dirección inversa, medida igual de silenciosa antes del guard."""
    with pytest.raises(DescribeUnparseable) as exc:
        parse_wallet_reputation(BREAKDOWN)
    assert "parse_breakdown" in str(exc.value)
    assert "METERED" in str(exc.value)


def test_el_guard_no_es_demasiado_estricto() -> None:
    """El contraste: cada parser sigue aceptando SU cuerpo capturado en vivo.

    Sin esto, «rechazá todo» pasaría verde — la misma trampa que el archivo de
    hashes deja escrita: la alarma que suena en el camino feliz se aprende a
    ignorar.
    """
    w = parse_wallet_reputation(WALLET_CON_REPUTACION)
    assert w.global_score == 100.0
    b = parse_breakdown(BREAKDOWN)
    assert b.final_score == 86.653045


@pytest.mark.parametrize(
    ("parser", "cuerpo"),
    [
        (parse_wallet_reputation, WALLET_CON_REPUTACION),
        (parse_breakdown, BREAKDOWN),
    ],
    ids=["wallet_reputation", "breakdown"],
)
def test_reparsear_el_objeto_parseado_ya_revienta_y_eso_se_fija(parser, cuerpo) -> None:
    """Medido 2026-08-31: el re-parseo ya era RUIDOSO — no hace falta guard.

    `_require` exige un dict con `wallet`, y un dataclass congelado no lo es.
    Este test no agrega comportamiento: lo FIJA, para que aceptar objetos
    parseados «por comodidad» sea un rojo y no un accidente.
    """
    parseado = parser(cuerpo)
    with pytest.raises(DescribeUnparseable):
        parser(parseado)
