"""`wallet_history()` — how a score MOVED, and the rule that keeps it honest.

The route existed in the service and no SDK exposed it, so every consumer that
wanted "is this going up or down" had to either hit the URL by hand or poll and
build its own series — and a first read has nothing to compare against, so the
answer was simply unavailable on day one.

The trap this file is mostly about: the service's own catalogue warns that
ratings with no on-chain date are ABSENT from the series but PRESENT in the
profile. With enough of them the last point sits legitimately below
`final_score`, and diffing the series reports a fall that never happened. So the
model refuses to answer rather than answering wrong — `change_over()` returns
`None`, never `0.0`, because "it did not move" and "I cannot tell" are different
statements and only one of them is true.
"""
from __future__ import annotations

import pytest

from uvd_describe_sdk import HistoryPoint, WalletHistory
from uvd_describe_sdk.errors import DescribeUnparseable
from uvd_describe_sdk.models import parse_history


CUERPO = {
    "wallet": "0x76e9be89a3be6c1bf581a1f4519cb82dca9c57b3",
    "bucket": "week",
    "points": [
        {"period": "2026-08-17T00:00:00Z", "score": 90.166667,
         "review_count": 6, "cumulative_score": 88.921881},
        {"period": "2026-08-24T00:00:00Z", "score": 89.575537,
         "review_count": 3, "cumulative_score": 88.772661},
        {"period": "2026-08-31T00:00:00Z", "score": 87.941169,
         "review_count": 38, "cumulative_score": 86.903663},
    ],
    "coverage": {"dated_reviews": 94, "undated_reviews": 0},
}


def test_parsea_la_forma_real_del_servicio():
    """El cuerpo es el que devolvió producción el 2026-09-08, no uno inventado."""
    h = parse_history(CUERPO)
    assert h.bucket == "week"
    assert len(h.points) == 3
    assert h.latest.cumulative_score == pytest.approx(86.903663)
    assert h.dated_reviews == 94 and h.undated_reviews == 0


def test_el_cambio_usa_el_acumulado_no_el_del_bucket():
    """`score` es el promedio de ESE bucket; `cumulative_score` es la serie que
    se alinea con `final_score`. Confundirlos convierte una semana floja en una caída."""
    h = parse_history(CUERPO)
    # último acumulado menos el anterior: bajó ~1.87 puntos
    c = h.change_over(1)
    assert c.delta == pytest.approx(86.903663 - 88.772661)
    assert c.direction == "down"
    # y NO es la resta de los `score` del bucket, que daría otro número
    assert c.delta != pytest.approx(87.941169 - 89.575537)
    # el cambio viene con sus fundamentos: sobre qué tramo y con cuántas fechadas
    assert c.from_period == "2026-08-24T00:00:00Z" and c.to_period == "2026-08-31T00:00:00Z"
    assert c.dated_reviews == 94
    assert h.change_over(2).delta == pytest.approx(86.903663 - 88.921881)


def test_sin_fechas_suficientes_NO_contesta_en_vez_de_contestar_mal():
    """El aviso del propio servicio: con muchos undated, la serie no sirve para decidir."""
    cuerpo = dict(CUERPO, coverage={"dated_reviews": 2, "undated_reviews": 90})
    h = parse_history(cuerpo)
    assert h.series_is_decidable is False
    assert h.change_over(1) is None, "un None dice 'no puedo saber'; un 0.0 mentiría"


def test_cero_dated_no_es_una_serie():
    h = parse_history(dict(CUERPO, coverage={"dated_reviews": 0, "undated_reviews": 5}))
    assert h.series_is_decidable is False


def test_serie_corta_no_inventa_un_cambio():
    h = parse_history(dict(CUERPO, points=[CUERPO["points"][-1]]))
    assert h.change_over(1) is None


def test_un_breakdown_parseado_aca_no_pasa_por_historial_vacio():
    """Guard de parser equivocado: el breakdown también trae `wallet`, así que sin
    esto se parsearía como una wallet SIN historial, que es una afirmación falsa."""
    with pytest.raises(DescribeUnparseable, match="points"):
        parse_history({"wallet": "0xabc", "final_score": 88.0, "total_reviews": 94})


def test_el_punto_conserva_los_dos_scores():
    p = parse_history(CUERPO).points[0]
    assert isinstance(p, HistoryPoint)
    assert p.score == pytest.approx(90.166667)
    assert p.cumulative_score == pytest.approx(88.921881)


def test_es_un_WalletHistory_congelado():
    h = parse_history(CUERPO)
    assert isinstance(h, WalletHistory)
    with pytest.raises(Exception):
        h.wallet = "otra"        # frozen: el objeto no se edita después de parsear
