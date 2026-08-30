"""R1 — `null` NUNCA `0`, y los tres hechos que el tipo tiene que separar.

Este archivo es la red debajo de la invariante 7 del servicio. Si un día alguien
"simplifica" un parser con `float(x or 0)`, acá se pone rojo.

Los tres hechos, y por qué los tres son RESPUESTAS y ninguno un error:

    no registrada             identity_count == 0, chains == []
    registrada sin calificar  identity_count  > 0, global_score is None
    no se pudo leer           el método devuelve None   (ver test_r5_*)
"""

from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk import format_score
from uvd_describe_sdk.models import (
    _opt_float,
    parse_breakdown,
    parse_leaderboard,
    parse_wallet_reputation,
)

from .conftest import (
    BREAKDOWN,
    LEADERBOARD,
    WALLET_CON_REPUTACION,
    WALLET_NO_REGISTRADA,
    WALLET_REGISTRADA_SIN_CALIFICAR,
    json_response,
)


def test_opt_float_no_convierte_none_en_cero() -> None:
    """La función de tres líneas donde vive R1 entera."""
    assert _opt_float(None) is None
    assert _opt_float(0) == 0.0  # un cero MEDIDO sí es un cero
    assert _opt_float(86.653045) == 86.653045


def test_wallet_registrada_sin_calificar_no_es_cero() -> None:
    rep = parse_wallet_reputation(WALLET_REGISTRADA_SIN_CALIFICAR)
    assert rep.global_score is None
    assert rep.global_score != 0
    assert rep.chains[0].final_score is None
    # …y sin embargo la wallet EXISTE. Ese es el hecho que el `None` del score
    # no puede dar solo.
    assert rep.has_identity is True


def test_wallet_no_registrada_se_distingue_de_la_registrada_sin_calificar() -> None:
    """El corazón de R1: dos objetos con el MISMO score `None` y hechos distintos.

    Si alguien colapsara los dos casos en «devolvé None», este test moriría —
    que es exactamente lo que tiene que impedir.
    """
    sin_calificar = parse_wallet_reputation(WALLET_REGISTRADA_SIN_CALIFICAR)
    no_registrada = parse_wallet_reputation(WALLET_NO_REGISTRADA)

    assert sin_calificar.global_score is None
    assert no_registrada.global_score is None
    assert sin_calificar.global_score == no_registrada.global_score  # indistinguibles

    # …y aun así, distinguibles:
    assert sin_calificar.has_identity is True
    assert no_registrada.has_identity is False
    assert sin_calificar.chains_with_identity == 1
    assert no_registrada.chains == []


def test_breakdown_weighted_score_none_no_es_cero() -> None:
    br = parse_breakdown(BREAKDOWN)
    assert br.weighted_score is None
    assert br.final_score == 86.653045
    # `self_rated.score` también: nadie se autocalificó, no es un cero.
    assert br.self_rated.count == 0
    assert br.self_rated.score is None


def test_leaderboard_fila_sin_score_queda_none() -> None:
    filas = parse_leaderboard(LEADERBOARD)
    assert filas[0].final_score == 100.0
    assert filas[1].final_score is None
    assert filas[1].shrunk_score is None
    # `distinct_raters` SÍ es 0: es un contador, no un score.
    assert filas[1].distinct_raters == 0


def test_format_score_no_imprime_cero_por_ausencia() -> None:
    """R1 en la última línea del camino, que es donde más fácil se rompe.

    Un `format_score(None)` que devolviera `"0"` sería la invariante rota con
    un solo carácter, y el usuario final vería un cero donde no hay dato.
    """
    assert format_score(None) == "—"
    assert format_score(0.0) == "0"
    assert format_score(None) != format_score(0.0)


def test_el_cliente_devuelve_un_objeto_no_un_none_cuando_no_hay_ratings(
    make_client,
) -> None:
    """El nivel de arriba: la wallet sin calificar vuelve como OBJETO.

    Es la mitad de R5 que se apoya en R1 — ver `test_r5_fail_open.py`.
    """
    with make_client(lambda _r: json_response(WALLET_REGISTRADA_SIN_CALIFICAR)) as c:
        rep = c.wallet("0x00000000000000000000000000000000000000aa")
    assert rep is not None
    assert rep.global_score is None
    assert rep.has_identity is True


@pytest.mark.parametrize("payload", [WALLET_CON_REPUTACION, WALLET_NO_REGISTRADA])
def test_ningun_score_se_vuelve_cero_al_pasar_por_el_cliente(make_client, payload) -> None:
    with make_client(lambda _r: json_response(payload)) as c:
        rep = c.wallet(payload["wallet"])
    assert rep is not None
    assert rep.global_score == payload["global_score"]


def test_un_chains_vacio_no_es_una_excepcion(make_client) -> None:
    """R4 vista desde R1: «no hay nada» es una forma válida, no un error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response(WALLET_NO_REGISTRADA)

    with make_client(handler, fail_open=False) as c:
        rep = c.wallet("0x00000000000000000000000000000000000000bb")
    assert rep is not None and rep.chains == []
