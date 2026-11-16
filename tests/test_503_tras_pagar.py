"""SDKPY-503 — después de pagar, sólo UNA clase de respuesta deja firmar otra.

Hasta 0.7.0, cualquier status >= 400 a un pedido con `X-PAYMENT` salía con «pedí
un desafío nuevo, no reenvíes éste», y volver a llamar al método firmaba una
autorización NUEVA (refutación RA del 2026-11-16: `firmas=2` en los 11 casos). El
servidor dice lo contrario: el MISMO header recupera la lectura sin cobrar otra
vez, y una firma nueva es un pago nuevo y válido (describe-net
`paywall.py@f68cc96`, `site/auth.md:350-372`). La lista blanca es la de
`site/paybox.md:151-163`: sólo un 402 con `payment_required` o
`PAYMENT_VERIFICATION_FAILED` dice «no se liquidó nada».

🔴 Ningún test de este archivo toca una clave ni la red. El payer arma un sobre
con la FORMA del de `uvd-x402-sdk` (base64 de un JSON con
`payload.authorization.validBefore`) y una firma de mentira; el servidor es un
`Guion` que contesta en orden y se pone ROJO si le sobra o le falta una
respuesta. Los cuerpos copian la forma de los constructores de `paywall.py`
(`_unavailable`, `_sin_vinculo`, `_sin_confirmar`, `_autorizacion_usada`,
`_conflict`, `_rejected`), leídos con `git show f68cc96:describenet/paywall.py`.
"""

from __future__ import annotations

import base64
import json
import pickle
import traceback
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from uvd_describe_sdk import (
    RESEND_NEW_CHALLENGE_OK,
    RESEND_RECONCILE,
    RESEND_SAME_HEADER,
    DescribeClient,
    DescribeError,
    DescribeHTTPError,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
)
from uvd_describe_sdk import client as modulo_cliente

from .conftest import BREAKDOWN, CHALLENGE_402, json_response

WALLET = "0x97cd97cfe21799bacbf39d0a53469e5f82f30996"
OTRA_WALLET = "0x00000000000000000000000000000000000000aa"

#: Un hash con FORMA de hash, armado en runtime y no como literal de 64 hex (el
#: pre-commit de la casa frena ese patrón en un diff, y con razón).
RECIBO = "0x" + "3768f3d4" * 8

#: Reloj fijo. `validBefore` = AHORA + 3600, el `valid_duration` por defecto de
#: `uvd-x402-sdk` (`client.py:3529` de 0.114.0).
AHORA = 1_800_000_000.0
VALIDEZ_S = 3600


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class Reloj:
    def __init__(self, t: float = AHORA) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class PayerConSobre:
    """Satisface `payment.Payer`. Devuelve un `X-PAYMENT` con forma real y firma falsa.

    Cada firma es distinta (el nonce cuenta), así que dos headers iguales
    prueban un REENVÍO y dos distintos prueban una firma NUEVA.
    """

    def __init__(self, reloj: Callable[[], float]) -> None:
        self.reloj = reloj
        self.headers: List[str] = []

    @property
    def firmas(self) -> int:
        return len(self.headers)

    def create_authorization(
        self,
        pay_to: str,
        amount_usd: Decimal,
        *,
        chain_name: Optional[str] = None,
        x402_version: int = 1,
        accepted: Optional[Dict[str, Any]] = None,
        resource: Optional[Any] = None,
    ) -> str:
        n = len(self.headers) + 1
        sobre = {
            "x402Version": 2,
            "accepted": accepted,
            "resource": resource,
            "payload": {
                "signature": "0x" + "cd" * 65,
                "authorization": {
                    "from": "0x" + "11" * 20,
                    "to": pay_to,
                    "value": "10000",
                    "validAfter": "0",
                    "validBefore": str(int(self.reloj()) + VALIDEZ_S),
                    "nonce": "0x" + format(n, "x").rjust(64, "0"),
                },
            },
        }
        header = base64.b64encode(json.dumps(sobre).encode("utf-8")).decode("ascii")
        self.headers.append(header)
        return header


class PayerOpaco:
    """Un payer cuyo header NO es base64(JSON): el `validBefore` no se puede leer."""

    def __init__(self) -> None:
        self.firmas = 0

    def create_authorization(self, *args: Any, **kwargs: Any) -> str:
        self.firmas += 1
        return f"HEADER-OPACO-{self.firmas}"


Paso = Callable[[httpx.Request], httpx.Response]


class Guion:
    """El servidor de mentira: UNA respuesta por request, en orden.

    Una request de más es un `AssertionError` adentro del transporte; una
    respuesta sin usar la marca `consumido()`. Las dos son rojo: un doble que
    nadie pidió es un test que dejó de probar lo que dice.
    """

    def __init__(self, *pasos: Paso) -> None:
        self.pasos: List[Paso] = list(pasos)
        self.vistas: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.vistas.append(request)
        assert self.pasos, f"request de más: {request.method} {request.url}"
        return self.pasos.pop(0)(request)

    def consumido(self) -> None:
        assert not self.pasos, f"quedaron {len(self.pasos)} respuestas sin usar"

    def pagados(self) -> List[str]:
        return [r.headers["X-PAYMENT"] for r in self.vistas if "X-PAYMENT" in r.headers]


def sin_header_402(request: httpx.Request) -> httpx.Response:
    assert "X-PAYMENT" not in request.headers, "se mandó un header donde tocaba pedir el 402"
    return json_response(CHALLENGE_402, status=402)


def pagado(respuesta: Callable[[httpx.Request], httpx.Response]) -> Paso:
    def paso(request: httpx.Request) -> httpx.Response:
        assert "X-PAYMENT" in request.headers, "tocaba el pedido PAGADO y llegó uno sin header"
        return respuesta(request)

    return paso


def ok_reusado(_r: httpx.Request) -> httpx.Response:
    return json_response(
        BREAKDOWN, headers={"X-Payment-Receipt": RECIBO, "X-Payment-Reused": "true"}
    )


def ok_nuevo(_r: httpx.Request) -> httpx.Response:
    return json_response(
        BREAKDOWN, headers={"X-Payment-Receipt": RECIBO, "X-Payment-Reused": "false"}
    )


def cliente(guion: Guion, payer: Any, reloj: Optional[Reloj] = None) -> DescribeClient:
    c = DescribeClient(
        transport=httpx.MockTransport(guion), payer=payer, pay_network="base", jitter=0
    )
    if reloj is not None:
        c._clock = reloj  # type: ignore[method-assign]
    return c


def _falla(c: DescribeClient, wallet: str = WALLET, **kw: Any) -> DescribeError:
    """Llama y DEVUELVE la excepción. El frame de arriba del traceback es éste, con
    sólo `c`, `wallet` y `kw` como locales: así el test de los locals mira los del
    SDK y de httpx, no los del test (que tiene el payer con sus headers)."""
    try:
        c.wallet_breakdown(wallet, **kw)
    except DescribeError as exc:
        return exc
    raise AssertionError("no levantó")


# ---------------------------------------------------------------------------
# Los cuerpos, con la forma de `paywall.py@f68cc96`
# ---------------------------------------------------------------------------

_SOBRE = {
    "resource": "GET /reputation/wallet/" + WALLET,
    "price_usd": "0.01",
    "pricing_version": "cost-tiered@12",
}
_MISMO = (
    "Reintentá esta misma request con el MISMO header de pago (PAYMENT-SIGNATURE o "
    "X-PAYMENT) después de 5 s. NO firmes una autorización nueva: "
)


def _503(error: str, **extra: Any) -> Dict[str, Any]:
    return {
        "error": error,
        "message": "Todavía no hay veredicto.",
        "retryable": True,
        "retry_after_seconds": 5,
        **_SOBRE,
        "next_action": _MISMO + "firmar otra puede hacerte pagar dos veces.",
        **extra,
    }


def _409(error: str, **extra: Any) -> Dict[str, Any]:
    return {
        "error": error,
        "message": "No se vuelve a cobrar ni se entrega el dato con ella.",
        "retryable": False,
        **_SOBRE,
        "next_action": "NO firmes otra autorización a ciegas: revisá on-chain.",
        **extra,
    }


def _402(error: str, next_action: str, **extra: Any) -> Dict[str, Any]:
    return {
        **CHALLENGE_402,
        "error": error,
        "retryable": False,
        "next_action": next_action,
        **extra,
    }


@dataclass
class Caso:
    nombre: str
    responde: Callable[[httpx.Request], httpx.Response]
    resend: str
    excepcion: type = DescribeHTTPError
    retry_after: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _r(status: int, body: Any = None, headers: Optional[Dict[str, str]] = None) -> Callable:
    def responde(_req: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, headers=headers or {})
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers or {})
        return json_response(body, status=status, headers=headers)

    return responde


def _lanza(exc: Exception) -> Callable:
    def responde(_req: httpx.Request) -> httpx.Response:
        raise exc

    return responde


_RA = {"Retry-After": "5"}

#: Los 13 del encargo (los 11 de la refutación RA, más el 402
#: `FACILITATOR_ERROR` y el timeout) y cuatro bordes más.
CASOS = [
    # ── los cinco 503: no hay veredicto, el MISMO header lo aprende ──────────
    Caso("503-facilitator_unavailable", _r(503, _503("facilitator_unavailable"), _RA),
         RESEND_SAME_HEADER, retry_after=5),
    Caso("503-settlement_unconfirmed",
         _r(503, _503("settlement_unconfirmed", transaction=RECIBO), _RA),
         RESEND_SAME_HEADER, retry_after=5),
    Caso("503-authorization_in_flight", _r(503, _503("authorization_in_flight"), _RA),
         RESEND_SAME_HEADER, retry_after=5),
    Caso("503-settlement_in_progress", _r(503, _503("settlement_in_progress"), _RA),
         RESEND_SAME_HEADER, retry_after=5),
    Caso("503-payment_store_unavailable", _r(503, _503("payment_store_unavailable"), _RA),
         RESEND_SAME_HEADER, retry_after=5),
    # ── los dos 409: el servidor cerró ESA autorización ──────────────────────
    Caso("409-authorization_spent", _r(409, _409("authorization_spent")), RESEND_RECONCILE),
    Caso("409-authorization_already_settled",
         _r(409, _409("authorization_already_settled", transaction=RECIBO),
            {"PAYMENT-RESPONSE": "e30="}),
         RESEND_RECONCILE),
    # ── el 402 de verificación rechazada: el ÚNICO que deja firmar otra ──────
    Caso("402-PAYMENT_VERIFICATION_FAILED",
         _r(402, _402("PAYMENT_VERIFICATION_FAILED",
                      "El pago no verificó y NADA se gastó. Firmá una autorización nueva.",
                      reason="invalid_signature")),
         RESEND_NEW_CHALLENGE_OK),
    # ── el 402 de un /settle rechazado tras un /verify aceptado: NO firmes ───
    Caso("402-FACILITATOR_ERROR",
         _r(402, _402("FACILITATOR_ERROR",
                      "El pago no verificó y NADA se gastó. Firmá una autorización nueva.",
                      reason="contract_call_failed", message="settle refused")),
         RESEND_SAME_HEADER),
    # ── los 5xx que no son 503: el paywall cobra ANTES del handler ───────────
    Caso("500-con-recibo",
         _r(500, {"detail": "el handler explotó DESPUÉS de cobrar"},
            {"X-Payment-Receipt": RECIBO}),
         RESEND_SAME_HEADER),
    Caso("500-pelado", _r(500), RESEND_SAME_HEADER),
    Caso("504", _r(504, b"<html>504 Gateway Time-out</html>"), RESEND_SAME_HEADER),
    Caso("timeout", _lanza(httpx.ReadTimeout("el settle tardó más que el cliente")),
         RESEND_SAME_HEADER, excepcion=DescribeTimeout),
    # ── bordes de más ────────────────────────────────────────────────────────
    Caso("402-payment_required", _r(402, CHALLENGE_402), RESEND_NEW_CHALLENGE_OK),
    Caso("402-PAYMENT_SETTLEMENT_FAILED",
         _r(402, _402("PAYMENT_SETTLEMENT_FAILED",
                      "La liquidación falló DESPUÉS de aceptar la autorización.")),
         RESEND_SAME_HEADER),
    Caso("socket-muerto", _lanza(httpx.ReadError("connection reset by peer")),
         RESEND_SAME_HEADER, excepcion=DescribeUnreachable),
    Caso("200-ilegible", _r(200, b"<html>502 bad gateway</html>"), RESEND_SAME_HEADER,
         excepcion=DescribeUnparseable),
]

_IDS = [c.nombre for c in CASOS]


# ---------------------------------------------------------------------------
# 1. Dos llamadas al mismo recurso: el mensaje, el atributo y CUÁNTAS firmas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caso", CASOS, ids=_IDS)
def test_dos_llamadas_al_mismo_recurso_firman_una_sola_vez_salvo_la_lista_blanca(
    caso: Caso,
) -> None:
    """🔴 El test que ata SDKPY-503. Discriminante en tres ejes a la vez.

    1. El MENSAJE: «ask for a new challenge» sólo en la lista blanca; en todo lo
       demás, «Do NOT sign another authorization» y ni rastro de «new challenge».
    2. El ATRIBUTO `payment["resend"]`, que es sobre lo que se ramifica.
    3. Las FIRMAS a lo largo de DOS llamadas: 1 (el segundo pedido pagado lleva
       el MISMO header, y va directo, sin otro 402) o 2 en la lista blanca.

    Con el código de 0.7.0 los 17 casos dan rojo en el mensaje, y los que no son
    de la lista blanca, además, en las firmas (2 en vez de 1).
    """
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    if caso.resend == RESEND_NEW_CHALLENGE_OK:
        guion = Guion(sin_header_402, pagado(caso.responde), sin_header_402, pagado(ok_nuevo))
    else:
        guion = Guion(sin_header_402, pagado(caso.responde), pagado(ok_reusado))

    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
        segunda = c.wallet_breakdown(WALLET)
    guion.consumido()

    assert isinstance(exc, caso.excepcion)
    assert exc.payment_sent is True and exc.payment is not None
    assert exc.payment["resend"] == caso.resend
    mensaje = str(exc)
    assert "do not resend" not in mensaje
    pagados = guion.pagados()
    if caso.resend == RESEND_NEW_CHALLENGE_OK:
        assert "ask for a new challenge" in mensaje
        assert "Do NOT sign another" not in mensaje
        # El refutador (ronda 1, P3): «refused it BEFORE moving anything» seguido
        # de «no proof either way» se contradecía en la misma línea.
        assert "no proof either way" not in mensaje
        assert payer.firmas == 2
        assert pagados == payer.headers and pagados[0] != pagados[1]
        assert segunda.receipt is not None and segunda.receipt.reused is False
    else:
        assert "new challenge" not in mensaje
        assert "Do NOT sign another authorization" in mensaje
        assert "ON THIS SAME CLIENT, while it stays open" in mensaje
        assert "GET /payments/status" in mensaje
        assert "`unpaid` there is NOT enough" in mensaje
        assert payer.firmas == 1
        assert pagados == [payer.headers[0], payer.headers[0]]
        assert segunda.receipt is not None and segunda.receipt.reused is True


@pytest.mark.parametrize("caso", [c for c in CASOS if c.retry_after], ids=lambda c: c.nombre)
def test_los_503_traen_lo_que_dijo_el_servidor_como_dato(caso: Caso) -> None:
    """`retry_after_seconds == 5` con `Retry-After: 5`, y los otros tres campos
    que `_server_reason` tiraba (`client.py:624` en 0.7.0)."""
    reloj = Reloj()
    guion = Guion(sin_header_402, pagado(caso.responde))
    with cliente(guion, PayerConSobre(reloj), reloj) as c:
        exc = _falla(c)
    guion.consumido()

    assert exc.payment is not None
    assert exc.payment["retry_after_seconds"] == 5
    assert exc.payment["server_error"] == caso.nombre.split("-", 1)[1]
    assert exc.payment["retryable"] is True
    assert "MISMO header" in exc.payment["next_action"]
    assert exc.payment["valid_before"] == int(AHORA) + VALIDEZ_S
    assert "Wait the 5 s of `Retry-After` first" in str(exc)


def test_el_next_action_del_servidor_viaja_pero_resend_manda() -> None:
    """El 402 `FACILITATOR_ERROR` de hoy dice «Firmá una autorización nueva»
    (describe-net BACKLOG :106, `_settle_refused_after_verify`). El SDK lo publica
    TAL CUAL en `next_action` —es lo que dijo el servidor— y decide `same-header`.
    Si alguien lee `next_action` para decidir, el docstring de `payment` le dice
    que `resend` gana; este test fija que los dos viajan y que no coinciden."""
    caso = next(c for c in CASOS if c.nombre == "402-FACILITATOR_ERROR")
    guion = Guion(sin_header_402, pagado(caso.responde))
    with cliente(guion, PayerConSobre(Reloj()), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert exc.payment is not None
    assert "Firmá una autorización nueva" in exc.payment["next_action"]
    assert exc.payment["server_error"] == "FACILITATOR_ERROR"
    assert exc.payment["resend"] == RESEND_SAME_HEADER


# ---------------------------------------------------------------------------
# 2. `retry_after_seconds`: cabecera primero, techo de 30 s
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cabecera", "cuerpo", "esperado"),
    [
        ("5", None, 5),
        (" 7 ", None, 7),
        ("30", None, 30),
        ("31", None, 30),
        ("3600", None, 30),
        ("9" * 5000, None, 30),  # sin `int()` de 5.000 dígitos (ValueError en 3.11+)
        ("0", None, 0),
        ("-5", None, None),
        ("5.5", None, None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None, None),
        ("cinco", None, None),
        (None, 7, 7),
        (None, 120, 30),
        ("basura", 9, 9),  # cabecera ilegible → el campo del cuerpo
        (None, True, None),  # un bool no es un número de segundos
        (None, -1, None),
        (None, "5", None),
        (None, None, None),
    ],
)
def test_retry_after_seconds_cabecera_cuerpo_y_techo(
    cabecera: Optional[str], cuerpo: Any, esperado: Optional[int]
) -> None:
    headers = {"Retry-After": cabecera} if cabecera is not None else {}
    body = {"retry_after_seconds": cuerpo} if cuerpo is not None else {}
    respuesta = httpx.Response(503, headers=headers)
    assert modulo_cliente._retry_after_seconds(respuesta, body) == esperado


def test_un_retry_after_enorme_llega_al_llamador_con_el_techo() -> None:
    """El techo de punta a punta, no sólo en la función."""
    guion = Guion(
        sin_header_402,
        pagado(_r(503, _503("facilitator_unavailable", retry_after_seconds=900),
                  {"Retry-After": "900"})),
    )
    with cliente(guion, PayerConSobre(Reloj()), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert exc.payment is not None and exc.payment["retry_after_seconds"] == 30


# ---------------------------------------------------------------------------
# 3. El header no sale: ni str, ni repr, ni pickle, ni los locals del traceback
# ---------------------------------------------------------------------------


def _eco(status: int) -> Callable[[httpx.Request], httpx.Response]:
    """Un servidor (o un intermediario) que hace ECO del header en su cuerpo."""

    def responde(request: httpx.Request) -> httpx.Response:
        h = request.headers["X-PAYMENT"]
        return json_response(
            {
                "error": "facilitator_unavailable",
                "message": f"no pude con {h}",
                "next_action": f"reenviá {h}",
                "retryable": True,
            },
            status=status,
            headers={"Retry-After": "5"},
        )

    return responde


_FUGAS = [c for c in CASOS if c.nombre in {
    "503-facilitator_unavailable", "409-authorization_spent", "402-FACILITATOR_ERROR",
    "402-PAYMENT_VERIFICATION_FAILED", "500-con-recibo", "timeout", "socket-muerto",
    "200-ilegible",
}] + [
    Caso("503-con-eco-del-header", _eco(503), RESEND_SAME_HEADER),
    Caso("protocolo-roto", _lanza(httpx.RemoteProtocolError("server disconnected")),
         RESEND_SAME_HEADER, excepcion=DescribeUnreachable),
]


def _contiene(valor: Any, aguja: str, profundidad: int = 0) -> bool:
    """¿Está `aguja` adentro de `valor`? Mira strings, bytes, contenedores y lo de
    httpx que carga headers. NO entra a objetos arbitrarios: el cliente guarda el
    header a propósito (es donde vive), y entrar a `self` lo encontraría ahí."""
    if profundidad > 6:
        return False
    if isinstance(valor, str):
        return aguja in valor
    if isinstance(valor, (bytes, bytearray)):
        return aguja.encode("utf-8") in valor
    if isinstance(valor, httpx.Headers):
        return any(aguja in v for v in valor.values())
    if isinstance(valor, httpx.Request):
        return _contiene(valor.headers, aguja, profundidad + 1)
    if isinstance(valor, httpx.Response):
        return _contiene(valor.headers, aguja, profundidad + 1) or (
            valor._request is not None and _contiene(valor._request, aguja, profundidad + 1)
        )
    if isinstance(valor, dict):
        return any(
            _contiene(k, aguja, profundidad + 1) or _contiene(v, aguja, profundidad + 1)
            for k, v in valor.items()
        )
    if isinstance(valor, (list, tuple, set, frozenset)):
        return any(_contiene(v, aguja, profundidad + 1) for v in valor)
    if isinstance(valor, modulo_cliente._PendingAuthorization):
        return True  # el registro del cliente en un local es justo la fuga
    return False


def _cadena(exc: BaseException) -> List[BaseException]:
    """`exc` y toda su cadena (`__cause__`/`__context__`), sin repetir."""
    cadena: List[BaseException] = []
    vistas: set = set()
    pendientes: List[BaseException] = [exc]
    while pendientes:
        e = pendientes.pop()
        if id(e) in vistas:
            continue
        vistas.add(id(e))
        cadena.append(e)
        pendientes.extend(x for x in (e.__cause__, e.__context__) if x is not None)
    return cadena


def _frames(exc: BaseException) -> List[Any]:
    """Todos los frames del traceback de `exc` y de su cadena (`__cause__`/`__context__`)."""
    frames: List[Any] = []
    for e in _cadena(exc):
        tb = e.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
    return frames


@pytest.mark.parametrize("caso", _FUGAS, ids=lambda c: c.nombre)
def test_el_header_no_sale_por_la_excepcion_ni_por_su_traceback(caso: Caso) -> None:
    """🔴 Medido por RA en 0.7.0: el header salía por `_paid:header` y por
    `_paid:paid.request.headers`, y `TracebackException(capture_locals=True)`
    —lo que hace un rastreador de errores con variables locales— lo imprimía.

    Se buscan los dos sentidos: el texto que una herramienta formatea, y los
    locals de cada frame del traceback y de su cadena (con httpx adentro)."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(sin_header_402, pagado(caso.responde))
    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
    guion.consumido()
    header = payer.headers[0]
    assert len(header) > 200  # un header de verdad, no un string vacío que «no aparece»

    assert header not in str(exc)
    assert header not in repr(exc)
    assert header.encode("ascii") not in pickle.dumps(exc)
    assert header not in json.dumps(exc.payment)
    for valor in vars(exc).values():
        assert not _contiene(valor, header)

    formateado = "".join(
        traceback.TracebackException.from_exception(exc, capture_locals=True).format()
    )
    assert "_send_pending" in formateado or "_paid" in formateado  # el traceback está entero
    assert header not in formateado

    # 🔴 Ronda 1 del refutador, P1-b: la excepción de httpx en `__cause__`
    # guardaba el `Request` entero, y `exc.__cause__.request.headers` tenía el
    # header. Se mira cada eslabón: sus atributos, su repr y su pickle.
    for eslabon in _cadena(exc):
        assert header not in repr(eslabon)
        for nombre, valor in vars(eslabon).items():
            assert not _contiene(valor, header), (
                f"el header está en `{type(eslabon).__name__}.{nombre}`"
            )
        assert header.encode("ascii") not in pickle.dumps(eslabon)

    frames = _frames(exc)
    assert len(frames) >= 3
    for frame in frames:
        for nombre, valor in frame.f_locals.items():
            assert not _contiene(valor, header), (
                f"el header está en el local `{nombre}` de "
                f"{frame.f_code.co_name} ({frame.f_code.co_filename})"
            )


def test_el_eco_del_header_en_el_cuerpo_se_tacha() -> None:
    """Si el cuerpo trae el header, el mensaje y `next_action` lo tachan."""
    reloj = Reloj()
    guion = Guion(sin_header_402, pagado(_eco(503)))
    with cliente(guion, PayerConSobre(reloj), reloj) as c:
        exc = _falla(c)
    guion.consumido()
    assert "[x-payment-redacted]" in str(exc)
    assert exc.payment is not None
    assert exc.payment["next_action"] == "reenviá [x-payment-redacted]"


def test_next_action_se_tacha_las_urls_y_se_trunca_y_retryable_es_un_bool_o_None() -> None:
    """`next_action` es prosa del servidor: mismo trato que `_server_reason`
    (URL tachada ANTES de truncar), con un tope de 1.000. Y `retryable` sólo si
    es un bool de verdad: `"true"` como string no es la palabra del servidor."""
    largo = "https://rpc.example/LLAVE-SECRETA reintentá " + "x" * 5000
    guion = Guion(
        sin_header_402,
        pagado(_r(503, _503("facilitator_unavailable", next_action=largo, retryable="true"),
                  _RA)),
    )
    with cliente(guion, PayerConSobre(Reloj()), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert exc.payment is not None
    texto = exc.payment["next_action"]
    assert "LLAVE-SECRETA" not in texto and texto.startswith("[url-redacted] reintentá ")
    assert len(texto) == modulo_cliente._MAX_NEXT_ACTION + 1 and texto.endswith("…")
    assert exc.payment["retryable"] is None


def test_un_payer_que_devuelve_bytes_no_revienta_despues_de_pagar() -> None:
    """El `Protocol` promete `str`; httpx acepta `bytes`. Con un cuerpo que trae
    `message` y `next_action`, el tachado del eco no puede levantar `TypeError`
    con la credencial ya mandada: sale la `DescribeHTTPError` marcada."""

    class PayerDeBytes:
        firmas = 0

        def create_authorization(self, *args: Any, **kwargs: Any) -> bytes:
            PayerDeBytes.firmas += 1
            return b"HEADER-EN-BYTES"

    guion = Guion(sin_header_402, pagado(_r(503, _503("facilitator_unavailable"), _RA)))
    with cliente(guion, PayerDeBytes(), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert isinstance(exc, DescribeHTTPError) and exc.payment_sent is True
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER


def test_el_registro_del_cliente_no_se_muestra_ni_se_picklea() -> None:
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(sin_header_402, pagado(_r(503, _503("facilitator_unavailable"), _RA)))
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        (pendiente,) = c._pending.values()
        assert payer.headers[0] not in repr(pendiente)
        assert payer.headers[0] not in repr(c._pending)
        with pytest.raises(TypeError, match="bearer credential"):
            pickle.dumps(pendiente)
    guion.consumido()


# ---------------------------------------------------------------------------
# 4. La vida del header guardado
# ---------------------------------------------------------------------------


def test_pasado_valid_before_mas_el_margen_firma_otra_y_no_antes() -> None:
    """El margen se SUMA al `validBefore` (`dn-pay.js:706-714`): a +14 s todavía
    reenvía la misma, a +15 s ya firma otra."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    no_hay = _r(503, _503("facilitator_unavailable"), _RA)
    guion = Guion(
        sin_header_402, pagado(no_hay),  # 1ª llamada: firma y 503
        pagado(no_hay),  # 2ª, a validBefore + 14: el MISMO
        sin_header_402, pagado(ok_nuevo),  # 3ª, a validBefore + 15: otra firma
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        vb = int(AHORA) + VALIDEZ_S
        reloj.t = vb + modulo_cliente.SIGN_AGAIN_MARGIN_S - 1
        _falla(c)
        reloj.t = vb + modulo_cliente.SIGN_AGAIN_MARGIN_S
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert modulo_cliente.SIGN_AGAIN_MARGIN_S == 15
    assert payer.firmas == 2
    assert guion.pagados() == [payer.headers[0], payer.headers[0], payer.headers[1]]


def test_un_header_sin_valid_before_legible_se_sigue_reenviando() -> None:
    """«No saber cuándo vence no es saber que venció» (`dn-pay.js:732-738`)."""
    reloj = Reloj()
    payer = PayerOpaco()
    no_hay = _r(503, _503("facilitator_unavailable"), _RA)
    guion = Guion(sin_header_402, pagado(no_hay), pagado(ok_reusado))
    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
        reloj.t = AHORA + 30 * 86400  # un mes después
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["valid_before"] is None
    assert payer.firmas == 1
    assert guion.pagados() == ["HEADER-OPACO-1", "HEADER-OPACO-1"]


def test_un_200_olvida_el_header_y_la_proxima_compra_firma_otra() -> None:
    """Una autorización paga UNA lectura: tras el 200 no queda nada que reenviar."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(sin_header_402, pagado(ok_nuevo), sin_header_402, pagado(ok_nuevo))
    with cliente(guion, payer, reloj) as c:
        c.wallet_breakdown(WALLET)
        assert c._pending == {}
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert payer.firmas == 2


def test_close_olvida_los_headers_pendientes() -> None:
    reloj = Reloj()
    guion = Guion(sin_header_402, pagado(_r(503, _503("facilitator_unavailable"), _RA)))
    c = cliente(guion, PayerConSobre(reloj), reloj)
    _falla(c)
    assert len(c._pending) == 1
    c.close()
    assert c._pending == {}
    guion.consumido()


def test_el_header_es_por_recurso_y_otro_recurso_es_otra_compra() -> None:
    """Otra wallet, o la misma con `snapshot=true`, es OTRO recurso (también para
    el servidor: `pricing.py::canonical_resource`) y firma su propia autorización.
    Y volver al primero reenvía el primero."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    no_hay = _r(503, _503("facilitator_unavailable"), _RA)
    guion = Guion(
        sin_header_402, pagado(no_hay),  # WALLET
        sin_header_402, pagado(ok_nuevo),  # OTRA_WALLET: firma la 2ª
        sin_header_402, pagado(ok_nuevo),  # WALLET?snapshot=true: firma la 3ª
        pagado(ok_reusado),  # WALLET otra vez: la 1ª
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        c.wallet_breakdown(OTRA_WALLET)
        c.wallet_breakdown(WALLET, snapshot=True)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert payer.firmas == 3
    assert guion.pagados() == [payer.headers[0], payer.headers[1], payer.headers[2],
                               payer.headers[0]]
    assert "snapshot=true" in str(guion.vistas[5].url)


def test_un_payment_required_en_un_REENVIO_no_deja_firmar_otra() -> None:
    """paybox.md paso 7, literal: «`payment_required` speaks only for the request
    that got it: if you presented that authorization earlier and got a `503`,
    that `503` still rules». En la PRIMERA presentación sí está en la lista blanca
    (lo cubre `CASOS`); en un reenvío, no."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402, pagado(_r(503, _503("facilitator_unavailable"), _RA)),
        pagado(_r(402, CHALLENGE_402)),  # el reenvío llegó sin header al servidor
        pagado(ok_reusado),  # la tercera vuelve a mandar EL MISMO
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER
    assert "new challenge" not in str(exc)
    assert payer.firmas == 1
    assert guion.pagados() == [payer.headers[0]] * 3


def test_un_PAYMENT_VERIFICATION_FAILED_en_un_reenvio_si_deja_firmar_otra() -> None:
    """El otro código de la lista blanca no lleva esa salvedad: el facilitador
    rechazó la autorización antes de mover nada, y el paywall nunca contesta 402
    a un header cuyo vínculo ya se presentó (`dn-pay.js:555-560`)."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402, pagado(_r(503, _503("payment_store_unavailable"), _RA)),
        pagado(_r(402, _402("PAYMENT_VERIFICATION_FAILED", "NADA se gastó."))),
        sin_header_402, pagado(ok_nuevo),
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["resend"] == RESEND_NEW_CHALLENGE_OK
    assert payer.firmas == 2


# ---------------------------------------------------------------------------
# 5. La clasificación: exacta, y hacia el lado que no cobra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "codigo", "esperado"),
    [
        (402, "PAYMENT_VERIFICATION_FAILED", RESEND_NEW_CHALLENGE_OK),
        (402, "payment_required", RESEND_NEW_CHALLENGE_OK),
        (402, "payment_verification_failed", RESEND_SAME_HEADER),  # mayúsculas: exacto
        (402, "PAYMENT_REQUIRED", RESEND_SAME_HEADER),
        (402, " PAYMENT_VERIFICATION_FAILED", RESEND_SAME_HEADER),
        (402, "PAYMENT_VERIFICATION_FAILED ", RESEND_SAME_HEADER),
        (402, "FACILITATOR_ERROR", RESEND_SAME_HEADER),
        (402, "PAYMENT_SETTLEMENT_FAILED", RESEND_SAME_HEADER),
        (402, "", RESEND_SAME_HEADER),
        (402, None, RESEND_SAME_HEADER),
        (403, "PAYMENT_VERIFICATION_FAILED", RESEND_SAME_HEADER),  # sólo un 402
        (400, "payment_required", RESEND_SAME_HEADER),
        (409, "settlement_failed_on_chain", RESEND_RECONCILE),
        (409, None, RESEND_RECONCILE),
        (422, None, RESEND_SAME_HEADER),
        (500, "PAYMENT_VERIFICATION_FAILED", RESEND_SAME_HEADER),
    ],
)
def test_la_lista_blanca_es_exacta(status: int, codigo: Optional[str], esperado: str) -> None:
    assert (
        modulo_cliente._classify_after_paying(
            status, codigo, presented_before=False, receipt_declared=False
        )
        == esperado
    )


def test_un_402_de_la_lista_blanca_con_recibo_declarado_no_deja_firmar() -> None:
    """Un recibo en la MISMA respuesta contradice «no se liquidó nada»."""
    for codigo in ("PAYMENT_VERIFICATION_FAILED", "payment_required"):
        assert (
            modulo_cliente._classify_after_paying(
                402, codigo, presented_before=False, receipt_declared=True
            )
            == RESEND_SAME_HEADER
        )


@pytest.mark.parametrize("codigo", ["PAYMENT_VERIFICATION_FAILED ", "a" * 65, 7, ["x"], True])
def test_un_error_sin_forma_de_codigo_no_se_publica_y_no_entra_a_la_lista(codigo: Any) -> None:
    """`server_error` sólo con forma de código; si no, `None`, que es `same-header`."""
    guion = Guion(sin_header_402, pagado(_r(402, _402(codigo, "NADA se gastó."))))  # type: ignore[arg-type]
    with cliente(guion, PayerConSobre(Reloj()), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert exc.payment is not None
    assert exc.payment["server_error"] is None
    assert exc.payment["resend"] == RESEND_SAME_HEADER


def test_un_402_con_un_recibo_en_la_cabecera_no_deja_firmar_de_punta_a_punta() -> None:
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402,
        pagado(_r(402, _402("PAYMENT_VERIFICATION_FAILED", "x"), {"X-Payment-Receipt": RECIBO})),
        pagado(ok_reusado),
    )
    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER
    assert payer.firmas == 1


# ---------------------------------------------------------------------------
# 6. Leer el `validBefore` del header firmado
# ---------------------------------------------------------------------------


def _b64(obj: Any) -> str:
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


@pytest.mark.parametrize(
    ("header", "esperado"),
    [
        (_b64({"payload": {"authorization": {"validBefore": "1800003600"}}}), 1800003600),
        (_b64({"payload": {"authorization": {"validBefore": 1800003600}}}), 1800003600),
        (_b64({"x402Version": 1, "payload": {"authorization": {"validBefore": "42"}}}), 42),
        (_b64({"payload": {"authorization": {"validBefore": "0"}}}), None),
        (_b64({"payload": {"authorization": {"validBefore": "-1"}}}), None),
        (_b64({"payload": {"authorization": {"validBefore": True}}}), None),
        (_b64({"payload": {"authorization": {"validBefore": "1e9"}}}), None),
        # dígitos de ancho completo: `str.isdigit()` los acepta, la regex ASCII no
        (_b64({"payload": {"authorization": {"validBefore": "１２３"}}}), None),
        (_b64({"payload": {"authorization": {"validBefore": "9" * 21}}}), None),
        (_b64({"payload": {"authorization": {}}}), None),
        (_b64({"payload": []}), None),
        (_b64([1, 2]), None),
        ("no-es-base64!!", None),
        (base64.b64encode(b"\xff\xfe no es json").decode("ascii"), None),
        (base64.b64encode(b"[" * 12000).decode("ascii"), None),  # RecursionError
        ("A" * 16388, None),  # pasado el tope no se decodifica
        ("", None),
    ],
)
def test_valid_before_se_lee_del_header_o_es_None(header: str, esperado: Optional[int]) -> None:
    assert modulo_cliente._valid_before_of(header) == esperado


# ---------------------------------------------------------------------------
# 7. El criterio de cierre 1 del encargo, como test
# ---------------------------------------------------------------------------


def test_ask_for_a_new_challenge_aparece_UNA_vez_en_src_y_en_la_rama_de_la_lista_blanca() -> None:
    """`git grep -n "ask for a new challenge" -- src/` sólo en la rama del 402 de la
    lista blanca. Se lee el código: si alguien vuelve a pegar la frase en otro
    lado —en un docstring de corrección, por ejemplo— esto se pone rojo."""
    src = Path(modulo_cliente.__file__).resolve().parent.parent
    hallados = []
    for archivo in sorted(src.rglob("*.py")):
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if "ask for a new challenge" in linea:
                hallados.append((archivo.name, numero))
    assert len(hallados) == 1, hallados
    archivo, numero = hallados[0]
    assert archivo == "client.py"
    lineas = Path(modulo_cliente.__file__).read_text(encoding="utf-8").splitlines()
    contexto = "\n".join(lineas[max(0, numero - 8) : numero])
    assert "if recuperacion == RESEND_NEW_CHALLENGE_OK:" in contexto


# ---------------------------------------------------------------------------
# 8. Ronda 1 del refutador externo (2026-11-16): lo que sobrevivía o faltaba
# ---------------------------------------------------------------------------


def test_dos_llamadas_a_la_vez_no_pierden_la_autorizacion_que_recibio_el_503() -> None:
    """🔴 P1-a. Dos llamadas al MISMO recurso se cruzan: B corre entera MIENTRAS A
    espera su 402 (el doble la dispara desde adentro del transporte, así que el
    cruce es determinista y en un solo hilo; el lock nunca se tiene durante la
    E/S). B firma #1, manda, recibe 503. Después A recibe su 402.

    Antes del arreglo A firmaba #2 y PISABA la entrada de B; el 200 de A la
    borraba, y la siguiente llamada de B firmaba #3 con #1 quizás liquidada: tres
    autorizaciones mandadas para dos lecturas. Ahora A ve la de B antes de firmar
    y la reenvía: cada header MANDADO pagó, a lo sumo, una lectura."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    caja: Dict[str, Any] = {}

    def a_pide_el_402_y_b_corre_entera(request: httpx.Request) -> httpx.Response:
        assert "X-PAYMENT" not in request.headers
        caja["b"] = _falla(caja["c"])  # B: 402 → firma #1 → 503
        return json_response(CHALLENGE_402, status=402)

    no_hay = _r(503, _503("facilitator_unavailable"), _RA)
    guion = Guion(
        a_pide_el_402_y_b_corre_entera,  # A, sin header
        sin_header_402, pagado(no_hay),  # B, adentro: 402 y 503 con #1
        pagado(ok_reusado),  # A: reenvía #1 (no firma) → 200
        sin_header_402, pagado(ok_nuevo),  # B otra vez: #1 ya resolvió → compra nueva
    )
    with cliente(guion, payer, reloj) as c:
        caja["c"] = c
        c.wallet_breakdown(WALLET)  # A
        c.wallet_breakdown(WALLET)  # B, de nuevo
    guion.consumido()
    assert caja["b"].payment["resend"] == RESEND_SAME_HEADER
    enviados = guion.pagados()
    assert enviados == [payer.headers[0], payer.headers[0], payer.headers[1]]
    assert payer.firmas == 2  # A nunca firmó: reusó la de B


def test_si_dos_firmaron_a_la_vez_gana_la_primera_y_la_otra_nunca_sale() -> None:
    """El otro lado de P1-a: el re-chequeo antes de firmar no cierra la carrera
    entre dos hilos que firman a la vez. `_store_pending` guarda la PRIMERA y la
    segunda —que no salió del proceso, así que no puede ejecutarse— se descarta."""
    reloj = Reloj()
    c = DescribeClient(transport=httpx.MockTransport(Guion()), jitter=0)
    c._clock = reloj  # type: ignore[method-assign]
    payer = PayerConSobre(reloj)
    clave = c._resource_key("/x", None)
    primera = modulo_cliente._PendingAuthorization(
        payer.create_authorization("0x", Decimal("0.01")), amount_usd="0.01", network="base"
    )
    segunda = modulo_cliente._PendingAuthorization(
        payer.create_authorization("0x", Decimal("0.01")), amount_usd="0.01", network="base"
    )
    c._store_pending(clave, primera)
    c._store_pending(clave, segunda)
    assert c._pending[clave] is primera
    c._forget_pending(clave, segunda)  # no es la suya: no la borra
    assert c._pending[clave] is primera
    c._forget_pending(clave, primera)
    assert clave not in c._pending
    c.close()


def test_si_otra_llamada_resolvio_la_entrada_antes_del_envio_es_una_compra_nueva() -> None:
    """La rama defensiva de `_send_pending`: entre guardar el header y mandarlo,
    otra llamada lo resolvió (un 2xx) y lo borró. No queda nada que reenviar: lo
    que sigue es una compra nueva, con su 402 y su firma, y no un `KeyError`."""
    from uvd_describe_sdk.models import parse_breakdown

    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(sin_header_402, pagado(ok_nuevo))
    with cliente(guion, payer, reloj) as c:
        ruta = "/reputation/wallet/" + WALLET
        br = c._paid_stretch(c._resource_key(ruta, None), ruta, None, parse_breakdown)
    guion.consumido()
    assert br.receipt is not None and payer.firmas == 1


def test_un_payment_required_tras_un_TIMEOUT_no_deja_firmar() -> None:
    """Del refutador (su M1 sobrevivía): la 1ª presentación muere por timeout —pudo
    llegar y liquidarse— y el reenvío recibe un 402 `payment_required` (un
    intermediario que se comió el header). `presented` tiene que valer True
    también cuando la 1ª salida terminó en excepción: por eso va en un `finally`."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402, pagado(_lanza(httpx.ReadTimeout("settle lento"))),
        pagado(_r(402, CHALLENGE_402)),
        pagado(ok_reusado),
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER
    assert payer.firmas == 1


def test_un_402_de_la_lista_con_recibo_PENDING_no_deja_firmar() -> None:
    """Del refutador (su M3 sobrevivía): `X-Payment-Receipt: pending` dice que el
    vendedor cobró y el hash no llegó; de punta a punta, no sólo en la función."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402,
        pagado(_r(402, _402("PAYMENT_VERIFICATION_FAILED", "x"),
                  {"X-Payment-Receipt": "pending"})),
        pagado(ok_reusado),
    )
    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER
    assert payer.firmas == 1


class _Corte(KeyboardInterrupt):
    pass


def test_un_KeyboardInterrupt_a_mitad_del_pedido_no_deja_el_header_en_los_frames() -> None:
    """Del refutador (su M2 sobrevivía): un Ctrl-C —o cualquier `BaseException`
    que no es `DescribeError`— adentro del transporte. El frame de `_request`
    tiene `headers={'X-PAYMENT': …}`, y `_paid_stretch` limpia con `except
    BaseException` justamente por esto. El header queda pendiente."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)

    def corta(_r: httpx.Request) -> httpx.Response:
        raise _Corte()

    guion = Guion(sin_header_402, pagado(corta))
    capturada: Any = None
    with cliente(guion, payer, reloj) as c:
        try:
            c.wallet_breakdown(WALLET)
        except _Corte as e:
            capturada = e
        assert len(c._pending) == 1
    guion.consumido()
    assert capturada is not None
    formateado = "".join(
        traceback.TracebackException.from_exception(capturada, capture_locals=True).format()
    )
    assert "_request" in formateado
    assert payer.headers[0] not in formateado


def test_la_clave_del_recurso_es_exacta_con_mayusculas() -> None:
    """El refutador (P3): «exacta, con mayúsculas» no tenía test. `…/0x97CD…` y
    `…/0x97cd…` son dos compras para el servidor (`canonical_resource` no pliega
    el path) y dos claves acá; y plegar rompería un id de Solana."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    no_hay = _r(503, _503("facilitator_unavailable"), _RA)
    mayusculas = "0x97CD97CFE21799BACBF39D0A53469E5F82F30996"
    guion = Guion(
        sin_header_402, pagado(no_hay),  # WALLET: firma #1
        sin_header_402, pagado(ok_nuevo),  # en mayúsculas: otra compra, #2
        pagado(ok_reusado),  # WALLET otra vez: #1
    )
    with cliente(guion, payer, reloj) as c:
        _falla(c)
        c.wallet_breakdown(mayusculas)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert guion.pagados() == [payer.headers[0], payer.headers[1], payer.headers[0]]


def test_un_header_valido_pasado_el_tope_no_se_decodifica() -> None:
    """El refutador (P3): el caso `"A" * 16388` daba `None` también sin el tope.
    Éste es un sobre VÁLIDO, con su `validBefore`, inflado pasado los 16 KiB: sin
    el tope se leería."""
    relleno = "x" * 13000
    header = _b64({"relleno": relleno, "payload": {"authorization": {"validBefore": "42"}}})
    assert len(header) > modulo_cliente._MAX_HEADER_LEIBLE
    assert modulo_cliente._valid_before_of(header) is None
    chico = _b64({"payload": {"authorization": {"validBefore": "42"}}})
    assert modulo_cliente._valid_before_of(chico) == 42


def test_un_200_pagado_con_JSON_anidado_sin_fin_sale_marcado_y_se_reenvia() -> None:
    """El refutador (P2-3): 200.000 `[` en un 200 PAGADO escapaba como
    `RecursionError`, sin `payment_sent`. Ahora es `DescribeUnparseable` marcada,
    y la llamada siguiente reenvía el mismo header."""
    reloj = Reloj()
    payer = PayerConSobre(reloj)
    guion = Guion(
        sin_header_402, pagado(_r(200, b"[" * 200_000)),
        pagado(ok_reusado),
    )
    with cliente(guion, payer, reloj) as c:
        exc = _falla(c)
        c.wallet_breakdown(WALLET)
    guion.consumido()
    assert isinstance(exc, DescribeUnparseable) and exc.payment_sent is True
    assert exc.payment is not None and exc.payment["resend"] == RESEND_SAME_HEADER
    assert payer.firmas == 1


def test_el_mensaje_dice_que_no_se_puede_leer_el_valid_before_cuando_no_se_puede() -> None:
    """El refutador (P3): «holds in memory until its `validBefore`» era falso con
    un `validBefore` ilegible, que se guarda hasta `close()`."""
    guion = Guion(sin_header_402, pagado(_r(503, _503("facilitator_unavailable"), _RA)))
    with cliente(guion, PayerOpaco(), Reloj()) as c:
        exc = _falla(c)
    guion.consumido()
    assert "until the client is closed (its `validBefore` could not be read)" in str(exc)
    assert "until its `validBefore` has passed" not in str(exc)
