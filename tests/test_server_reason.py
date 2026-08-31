"""`server_reason` — el cuerpo del error viaja EN la excepción. Aporte de EM.

Aporte de **Execution Market** (verificado aditivo, 2026-08-31): el `recovery`
de `DescribeHTTPError` promete desde el día uno que «the body names the field»
— y hasta hoy la excepción tiraba ese cuerpo a la basura y viajaba con el HTTP
pelado. El que cazaba un 422 tenía que re-pedir para enterarse de QUÉ campo.

Lo que este archivo fija, en orden de importancia:

1. Los `error` / `code` / `message` del cuerpo llegan en `server_reason` y
   anexados al mensaje — en las TRES puertas que levantan `DescribeHTTPError`
   (ruta gratis, pre-402 de la ruta paga, y post-pago).
2. 🔴 Toda URL del cuerpo sale REDACTADA. El cuerpo lo escribe el SERVIDOR: un
   5xx puede hacer eco de una URL upstream con la API key en el path — la forma
   exacta que el servicio borra de su lado (`chain/rpc.py::_redact`). No nos
   toca asumir que siempre lo hizo.
3. Un cuerpo no-JSON (o no-dict) da `None`, nunca una segunda excepción encima
   de la que se está levantando.
4. La razón se trunca a ~300 chars DESPUÉS de redactar — el otro orden podría
   cortar una URL a la mitad y dejar la key en pie.

Lo que este archivo NO toca: `recovery`. Sigue siendo la constante de clase que
`test_recovery.py` fija por identidad de objeto — acá no se interpola nada ahí.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from uvd_describe_sdk import DescribeHTTPError

from .conftest import CHALLENGE_402, json_response

#: Una clave de MENTIRA con la forma del incidente real: la URL de un upstream
#: con su credencial en el path. No es ninguna clave real y no tiene forma de
#: private key (nada de `0x` + 64 hex).
CUERPO_CON_URL = {
    "error": "upstream_failed",
    "message": "gateway WSS://rpc.invalid/v2/CLAVE-FALSA-DE-TEST-NO-REAL timed out",
}


def test_el_422_llega_con_lo_que_el_cuerpo_dijo(make_client) -> None:
    """La puerta gratis: el campo que el `recovery` promete, ahora de verdad."""
    with make_client(
        lambda _r: json_response(
            {"error": "invalid_address", "code": 422, "message": "not hex"},
            status=422,
        ),
        fail_open=False,
        jitter=0,
    ) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet("0xzz")

    assert exc.value.status_code == 422
    assert exc.value.server_reason is not None
    assert "invalid_address" in exc.value.server_reason
    assert "not hex" in exc.value.server_reason
    assert "invalid_address" in str(exc.value), "anexado al mensaje, no solo al atributo"


def test_un_cuerpo_no_json_da_server_reason_none(make_client) -> None:
    """Tolerante: la ausencia de razón no puede tapar la excepción original."""
    with make_client(
        lambda _r: httpx.Response(500, content=b"<html>gateway error</html>"),
        fail_open=False,
        jitter=0,
    ) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet("0xdead")

    assert exc.value.server_reason is None
    assert "the server says" not in str(exc.value)


def test_toda_url_del_cuerpo_sale_redactada(make_client) -> None:
    """🔴 El guard de secretos: la clave del upstream no viaja en la excepción."""
    with make_client(
        lambda _r: json_response(CUERPO_CON_URL, status=502),
        fail_open=False,
        jitter=0,
    ) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet("0xdead")

    assert exc.value.server_reason is not None
    assert "CLAVE-FALSA-DE-TEST" not in exc.value.server_reason
    assert "rpc.invalid" not in exc.value.server_reason
    assert "CLAVE-FALSA-DE-TEST" not in str(exc.value)
    assert "[url-redacted]" in exc.value.server_reason
    assert "upstream_failed" in exc.value.server_reason, (
        "se redacta la URL, no el resto: sin esto el guard podría cumplirse "
        "devolviendo None y el aporte entero quedaría apagado"
    )


def test_la_razon_se_trunca_despues_de_redactar(make_client) -> None:
    """~300 chars de techo, y la URL ya no está cuando la tijera corta."""
    relleno = "x" * 400
    with make_client(
        lambda _r: json_response(
            {"message": f"https://rpc.invalid/v2/CLAVE-FALSA-DE-TEST-NO-REAL {relleno}"},
            status=500,
        ),
        fail_open=False,
        jitter=0,
    ) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet("0xdead")

    assert exc.value.server_reason is not None
    assert len(exc.value.server_reason) <= 301  # 300 + la elipsis
    assert "CLAVE-FALSA-DE-TEST" not in exc.value.server_reason


def test_el_error_post_pago_tambien_trae_la_razon(make_client) -> None:
    """La puerta más cara: un 4xx/5xx DESPUÉS de pagar dice qué dijo el server."""

    class _Payer:
        def create_authorization(self, *_a: Any, **_k: Any) -> str:
            return "BASE64-DE-MENTIRA"

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response(
            {"error": "snapshot_failed", "message": "matview refresh in progress"},
            status=503,
        )

    with make_client(handler, payer=_Payer(), jitter=0) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet_breakdown("0xdead")

    assert exc.value.status_code == 503
    assert exc.value.server_reason is not None
    assert "snapshot_failed" in exc.value.server_reason
    assert "AFTER paying" in str(exc.value), "la marca post-pago no se pierde"
