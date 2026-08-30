"""R4 — excepción SÓLO por transporte o protocolo. **Nunca** por «no hay datos».

La frase que lo fija es del propio servicio, en la respuesta de su puerta A2A:
*«That is an answer, not an error»*.

El caso que este archivo cuida más de cerca es el **404**: se degrada a `None`
con su observación, y lo hace **también con `fail_open=False`**. Negarse a
contestar sobre un sujeto ausente sería tratar la ausencia como una falla, que
es literalmente lo que R4 prohíbe.
"""

from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk import (
    DescribeError,
    DescribeHTTPError,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
)
from uvd_describe_sdk.errors import HTTP_5XX_LEGACY_KIND

from .conftest import WALLET_NO_REGISTRADA, json_response


def test_404_no_lanza_ni_con_fail_open_apagado(make_client) -> None:
    """El test que el contrato pide por nombre: «404 no lanza».

    Y con `fail_open=False`, que es donde de verdad se prueba: con el fail-open
    encendido, un 404 tratado como error también daría `None` y el test estaría
    verde con el bug.
    """
    visto: list[DescribeError] = []
    with make_client(
        lambda _r: json_response({"detail": "not found"}, status=404),
        fail_open=False,
        on_error=visto.append,
    ) as c:
        rep = c.wallet("0xnoexiste")

    assert rep is None
    assert len(visto) == 1 and visto[0].kind == "http_error"


def test_200_con_chains_vacio_no_es_error_y_no_se_observa(make_client) -> None:
    """El caso «no hay datos» de verdad: 200 y una respuesta legítima."""
    visto: list[DescribeError] = []
    with make_client(
        lambda _r: json_response(WALLET_NO_REGISTRADA),
        fail_open=False,
        on_error=visto.append,
    ) as c:
        rep = c.wallet("0x00000000000000000000000000000000000000bb")

    assert rep is not None
    assert rep.chains == []
    assert visto == []  # no pasó nada malo: no se reporta nada


# ---------------------------------------------------------------------------
# Lo que SÍ es una excepción, y con qué `kind` sale
# ---------------------------------------------------------------------------


def test_500_es_http_error(make_client) -> None:
    with make_client(lambda _r: json_response({}, status=500), fail_open=False) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.health()
    assert exc.value.status_code == 500
    assert exc.value.kind == "http_error"


def test_422_cae_en_el_mismo_bucket_con_su_status(make_client) -> None:
    """El 422 de `GET /leaderboard?limit=…` (medido vivo el 2026-08-30).

    Mismo bucket que el 5xx —todo consumidor los trata igual— pero el
    `status_code` viaja, que es lo que se lee para distinguirlos.
    """
    with make_client(
        lambda _r: json_response({"detail": {"error": "…"}}, status=422), fail_open=False
    ) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.leaderboard()
    assert exc.value.status_code == 422


def test_timeout_no_se_confunde_con_unreachable(make_client) -> None:
    """🔴 `httpx.TimeoutException` **hereda de** `TransportError`.

    Si el `except` del timeout no va PRIMERO, todo timeout sale etiquetado
    `unreachable` — dos causas distintas con la misma etiqueta, y el que
    investiga busca un problema de DNS que no existe. Este test es el que
    ordena esos dos `except` y se pone rojo si alguien los intercambia.
    """

    def timeout(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("cold start de 15,2 s")

    def dns(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such host")

    with make_client(timeout, fail_open=False) as c:
        with pytest.raises(DescribeTimeout) as t:
            c.health()
    assert t.value.kind == "timeout"

    with make_client(dns, fail_open=False) as c:
        with pytest.raises(DescribeUnreachable) as u:
            c.health()
    assert u.value.kind == "unreachable"


def test_cuerpo_no_json_es_unparseable(make_client) -> None:
    def html(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>502 bad gateway</html>")

    with make_client(html, fail_open=False) as c:
        with pytest.raises(DescribeUnparseable):
            c.health()


def test_json_valido_sin_la_forma_esencial_es_unparseable(make_client) -> None:
    """Un 200 con JSON legítimo pero sin la clave que la ruta promete.

    Es protocolo, no dato: la ruta declara `wallet` en su schema. Sin eso no se
    puede afirmar de quién se está hablando, y adivinar sería peor.
    """
    with make_client(lambda _r: json_response({"otra_cosa": 1}), fail_open=False) as c:
        with pytest.raises(DescribeUnparseable):
            c.wallet("0xdead")


def test_ninguna_excepcion_de_httpx_cruza_el_borde(make_client) -> None:
    """Quien llama no debería importar `httpx` para escribir su `except`."""

    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reset")

    with make_client(boom, fail_open=False) as c:
        with pytest.raises(DescribeError):
            c.health()
        try:
            c.health()
        except httpx.HTTPError:  # pragma: no cover - si entra acá, el test falló
            pytest.fail("una httpx.HTTPError cruzó el borde del SDK")
        except DescribeError:
            pass


def test_el_kind_legacy_de_execution_market_se_publica() -> None:
    """Quien migre de EM tiene un `if err.kind == "http_5xx"` escrito.

    El bucket se renombró (`http_5xx` → `http_error`) porque el nombre viejo hay
    que desmentirlo en su propio docstring: ahí caen 422 y 429. El nombre viejo
    se publica como constante para que la migración sea un diff mecánico y no
    una cacería.
    """
    assert HTTP_5XX_LEGACY_KIND == "http_5xx"
    assert DescribeHTTPError.kind == "http_error"
    assert DescribeHTTPError.kind != HTTP_5XX_LEGACY_KIND
