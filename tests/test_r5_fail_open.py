"""R5 — el fail-open, y la parte que lo hace no-mentiroso: **es observable**.

Lo que Saul pidió (2026-08-28, literal): *«pon un fallback si es que describe
está caído»*.

Lo que este archivo defiende es la mitad que NO pidió y que sin ella el fallback
miente: que ese `None` **nunca salga callado**. Un fail-open silencioso convierte
«describe está caído» en «esta wallet no tiene reputación» — la misma confusión
que R1 existe para impedir, ahora fabricada por el mecanismo que la iba a evitar.

🔴 **Verificación discriminante.** Un test que sólo afirme `rep is None` estaría
verde igual con el bug: también daría `None` si el SDK devolviera `None` para
una wallet sin calificar. Por eso cada test de acá afirma **las dos mitades**:
que hubo `None` *y* que hubo observación; y el test de contraste afirma que el
caso legítimo devuelve un OBJETO y **cero** observaciones.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from uvd_describe_sdk import DescribeError, DescribeHTTPError, DescribeTimeout

from .conftest import WALLET_REGISTRADA_SIN_CALIFICAR, json_response


def _boom_timeout(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("cold start")


def _boom_dns(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("dns")


def _boom_500(_request: httpx.Request) -> httpx.Response:
    return json_response({"detail": "boom"}, status=500)


# ---------------------------------------------------------------------------
# El fail-open devuelve None Y observa. Las dos mitades, siempre.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,kind",
    [
        (_boom_timeout, "timeout"),
        (_boom_dns, "unreachable"),
        (_boom_500, "http_error"),
    ],
)
def test_fail_open_devuelve_none_y_lo_reporta(make_client, handler, kind) -> None:
    visto: list[DescribeError] = []
    with make_client(handler, fail_open=True, on_error=visto.append) as c:
        rep = c.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert rep is None
    # La mitad que hace la diferencia: alguien se enteró, y sabe de QUÉ tipo fue.
    assert len(visto) == 1
    assert visto[0].kind == kind
    assert isinstance(visto[0], DescribeError)


def test_sin_on_error_igual_loguea_en_warning(make_client, caplog) -> None:
    """No existe el modo silencioso. El default no es «no hacer nada»: es loguear.

    Sin esto, un consumidor que no pase `on_error` tendría un fail-open mudo — y
    los logs son donde se investiga el incidente.
    """
    with caplog.at_level(logging.WARNING, logger="uvd_describe_sdk"):
        with make_client(_boom_500, fail_open=True) as c:
            rep = c.wallet("0xdead")

    assert rep is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    mensaje = " ".join(r.getMessage() for r in caplog.records)
    # El mensaje tiene que decir explícitamente que None no es «sin reputación»:
    # es lo que va a leer el que abra el log a las 3 AM.
    assert "sin reputación" in mensaje


def test_el_caso_legitimo_no_produce_ninguna_observacion(make_client) -> None:
    """🔴 EL TEST DISCRIMINANTE.

    Montamos el estado BUENO —una wallet real, registrada, sin calificaciones— y
    exigimos lo contrario en las dos mitades: **objeto**, no `None`; y **cero**
    observaciones. Si alguien implementara «sin ratings ⇒ devolvé None», los
    tests de arriba seguirían verdes y este se pondría rojo. Es la única razón
    por la que este archivo prueba algo.
    """
    visto: list[DescribeError] = []
    with make_client(
        lambda _r: json_response(WALLET_REGISTRADA_SIN_CALIFICAR),
        fail_open=True,
        on_error=visto.append,
    ) as c:
        rep = c.wallet("0x00000000000000000000000000000000000000aa")

    assert rep is not None
    assert rep.global_score is None
    assert visto == []


def test_fail_open_false_levanta_en_vez_de_tragar(make_client) -> None:
    visto: list[DescribeError] = []
    with make_client(_boom_timeout, fail_open=False, on_error=visto.append) as c:
        with pytest.raises(DescribeTimeout):
            c.wallet("0xdead")
    # Y si levanta, NO se observa: observar sería reportar dos veces el mismo
    # hecho a quien ya lo va a ver en su `except`.
    assert visto == []


def test_un_on_error_roto_no_tumba_al_llamador(make_client, caplog) -> None:
    """El observador es de quien llama y puede explotar. Que su bug convierta
    una degradación prevista en un crash sería peor que no tener observador."""

    def observador_roto(_exc: DescribeError) -> None:
        raise RuntimeError("el logger del consumidor se rompió")

    with caplog.at_level(logging.ERROR, logger="uvd_describe_sdk"):
        with make_client(_boom_500, fail_open=True, on_error=observador_roto) as c:
            rep = c.wallet("0xdead")

    assert rep is None
    assert any("on_error" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# El alcance del fail-open: la tabla de tipos del contrato, al pie de la letra
# ---------------------------------------------------------------------------


def test_leaderboard_y_health_no_son_nullables(make_client) -> None:
    """El contrato núcleo v0.1 marca `| null` SÓLO en `wallet()`.

    Una lista vacía devuelta por un fallo se lee como «el índice está vacío»,
    que es una afirmación mucho más fuerte que «no pude leerlo». Un consumidor
    que quiera degradar acá lo hace en su `except`, explícito.

    ⚠️ Reportado como ambigüedad del contrato (R5 no acota; la tabla de tipos
    sí). Se implementa la tabla porque es la afirmación más específica.
    """
    with make_client(_boom_500, fail_open=True) as c:
        with pytest.raises(DescribeHTTPError):
            c.leaderboard()
        with pytest.raises(DescribeHTTPError):
            c.health()


def test_badge_url_no_toca_la_red_ni_con_el_indice_caido(make_client) -> None:
    """El badge es string puro: sigue funcionando con todo roto.

    Es la superficie que Saul puso primero (el copy-paste tipo like button) y no
    puede depender de que el proceso que la genera alcance la API.
    """
    with make_client(_boom_500) as c:
        url = c.badge_url("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
    assert url.endswith("/badge/0x97cd97cfe21799bacbf39d0a53469e5f82f30996.svg")
