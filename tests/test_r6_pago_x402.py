"""R6 — la caseta de peaje. **Ningún test de este archivo toca una clave privada.**

El payer es un `Protocol`, así que se mockea con un objeto de diez líneas que
registra con qué lo llamaron. Eso permite verificar lo que de verdad importa
—que se verifica el destinatario ANTES de firmar, que el accept se echa
VERBATIM, que un `payTo` distinto es `DO_NOT_PAY` y no un retry— sin una clave
ni un centavo de USDC.

🔴 Regla absoluta de la casa (INC-2026-03-30, dos wallets drenadas): **nunca se
hardcodea una private key, ni «para probar»**. Este archivo es la demostración
de que no hace falta.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
import pytest

from uvd_describe_sdk import (
    TREASURY_EVM,
    DescribeHTTPError,
    DoNotPayError,
    PaymentRequiredError,
    build_payment_header,
    chain_name_for,
)

from .conftest import BREAKDOWN, CHALLENGE_402, json_response


class PayerEspia:
    """Satisface `payment.Payer` estructuralmente. Registra y devuelve un token.

    No firma nada: devolver un string cualquiera alcanza, porque lo que se está
    probando es **qué se le pide firmar**, no la criptografía — esa es del
    `uvd-x402-sdk` y tiene sus propios 610 tests.
    """

    def __init__(self) -> None:
        self.llamadas: List[Dict[str, Any]] = []

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
        self.llamadas.append(
            {
                "pay_to": pay_to,
                "amount_usd": amount_usd,
                "chain_name": chain_name,
                "x402_version": x402_version,
                "accepted": accepted,
                "resource": resource,
            }
        )
        return "BASE64-DE-MENTIRA"


class PayerQueExplota:
    """Un payer que falla si lo llaman. Sirve para probar que NO se lo llamó."""

    def create_authorization(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            "se intentó FIRMAR sin haber verificado antes al destinatario"
        )


# ---------------------------------------------------------------------------
# Sin payer: se levanta con el challenge entero, y NO lo traga el fail-open
# ---------------------------------------------------------------------------


def test_sin_payer_levanta_con_el_challenge_entero(make_client) -> None:
    """Y trae el `price_usd` y el `free_preview`: quien lo reciba puede decidir
    si paga o si se conforma con la puerta gratis."""
    with make_client(
        lambda _r: json_response(CHALLENGE_402, status=402), fail_open=True
    ) as c:
        with pytest.raises(PaymentRequiredError) as exc:
            c.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert exc.value.price_usd == "0.01"
    assert exc.value.challenge["free_preview"]["endpoint"] == "GET /wallets/{wallet}/chains"
    assert len(exc.value.challenge["accepts"]) == 2


def test_el_fail_open_no_tapa_una_falta_de_configuracion(make_client) -> None:
    """🔴 Discriminante: con `fail_open=True` —el default— igual LEVANTA.

    Tragarla convertiría «te olvidaste de configurar el pago» en «esta wallet no
    tiene reputación», que es la misma mentira que R1 impide. El fail-open es
    para la disponibilidad del índice, no para la config de quien llama.
    """
    with make_client(
        lambda _r: json_response(CHALLENGE_402, status=402), fail_open=True
    ) as c:
        with pytest.raises(PaymentRequiredError):
            c.wallet_breakdown("0xdead")


# ---------------------------------------------------------------------------
# Con payer: el baile completo
# ---------------------------------------------------------------------------


def test_el_baile_del_402_completo(make_client) -> None:
    """Pedir sin header → 402 → firmar → repetir LA MISMA request con el header."""
    vistos: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response(
            BREAKDOWN,
            headers={
                "X-Payment-Receipt": "0xabc123",
                "X-Payment-Reused": "false",
                "X-Pricing-Version": "cost-tiered@5",
            },
        )

    payer = PayerEspia()
    with make_client(handler, payer=payer, pay_network="base") as c:
        br = c.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    # dos requests a LA MISMA url: «same arguments, one more field»
    assert len(vistos) == 2
    assert str(vistos[0].url) == str(vistos[1].url)
    assert vistos[1].headers["X-PAYMENT"] == "BASE64-DE-MENTIRA"

    # y el resultado trae el recibo, que hasta hoy ningún cliente leía
    assert br.receipt is not None
    assert br.receipt.transaction_hash == "0xabc123"
    assert br.receipt.reused is False
    assert br.receipt.pricing_version == "cost-tiered@5"


def test_el_accept_se_echa_verbatim_y_el_monto_sale_del_challenge() -> None:
    """«Reconstructing it instead of echoing is how a payment gets rejected by a
    server that did nothing wrong» (`uvd_x402_sdk/client.py:1799-1802`)."""
    payer = PayerEspia()
    build_payment_header(payer, CHALLENGE_402, network="base")

    llamada = payer.llamadas[0]
    assert llamada["accepted"] == CHALLENGE_402["accepts"][0]  # VERBATIM
    assert llamada["pay_to"] == TREASURY_EVM
    assert llamada["chain_name"] == "base"
    assert llamada["x402_version"] == 2
    # `Decimal`, jamás `float`: es plata.
    assert llamada["amount_usd"] == Decimal("0.01")
    assert isinstance(llamada["amount_usd"], Decimal)
    # `resource` como OBJETO, no el string pelado (el facilitator lo exige así)
    assert llamada["resource"]["url"] == CHALLENGE_402["resource"]
    assert llamada["resource"]["mimeType"] == "application/json"


def test_se_elige_la_red_que_pidio_el_llamador() -> None:
    payer = PayerEspia()
    build_payment_header(payer, CHALLENGE_402, network="avalanche")
    assert payer.llamadas[0]["chain_name"] == "avalanche"
    assert payer.llamadas[0]["accepted"]["network"] == "eip155:43114"


def test_una_red_que_el_servidor_no_ofrece_lista_las_que_si() -> None:
    """Un «unsupported network» sin la lista obliga a leer el challenge a mano."""
    with pytest.raises(DoNotPayError) as exc:
        build_payment_header(PayerEspia(), CHALLENGE_402, network="ethereum")
    assert "base" in str(exc.value) and "avalanche" in str(exc.value)


# ---------------------------------------------------------------------------
# 🔴 DO_NOT_PAY — el chequeo que existe para que no se desvíen fondos
# ---------------------------------------------------------------------------


def test_un_payto_distinto_es_do_not_pay_y_NO_se_firma() -> None:
    """El test más importante del archivo, y es discriminante en dos ejes.

    1. Se levanta `DoNotPayError` — no un retry, no un warning.
    2. El payer usado **explota si lo llaman**: prueba que la verificación pasa
       ANTES de la firma. Un test que sólo afirmara la excepción estaría verde
       aunque la firma ya hubiera ocurrido.
    """
    challenge = dict(CHALLENGE_402)
    challenge["accepts"] = [
        dict(CHALLENGE_402["accepts"][0], payTo="0xATACANTE00000000000000000000000000000000")
    ]

    with pytest.raises(DoNotPayError) as exc:
        build_payment_header(PayerQueExplota(), challenge, network="base")

    assert exc.value.expected.lower() == TREASURY_EVM.lower()
    assert exc.value.offered.startswith("0xATACANTE")
    assert "NO se reintenta" in str(exc.value)


def test_el_payto_se_compara_case_insensitive_pero_no_por_prefijo() -> None:
    """EIP-55 es sólo un checksum: la misma dirección en minúsculas es la misma.

    Lo que NO se afloja son los 20 bytes: un prefijo igual y el resto distinto
    es una dirección distinta, y ahí no se paga.
    """
    igual = dict(CHALLENGE_402)
    igual["accepts"] = [dict(CHALLENGE_402["accepts"][0], payTo=TREASURY_EVM.lower())]
    build_payment_header(PayerEspia(), igual, network="base")  # no levanta

    casi = dict(CHALLENGE_402)
    casi["accepts"] = [
        dict(CHALLENGE_402["accepts"][0], payTo=TREASURY_EVM[:-4] + "dead")
    ]
    with pytest.raises(DoNotPayError):
        build_payment_header(PayerQueExplota(), casi, network="base")


def test_un_402_que_no_cierra_consigo_mismo_no_se_firma() -> None:
    """`price_usd` = 0,01 pero `accepts[].amount` = 50.000 unidades base.

    Firmar por un monto distinto del pedido gasta la credencial y el servidor
    igual contesta 4xx — la guía publicada lo nombra como la causa más común de
    un «4xx después de pagar». Preferimos negarnos antes de gastar el nonce.
    """
    torcido = dict(CHALLENGE_402)
    torcido["accepts"] = [dict(CHALLENGE_402["accepts"][0], amount="50000")]
    with pytest.raises(DoNotPayError) as exc:
        build_payment_header(PayerQueExplota(), torcido, network="base")
    assert "10000" in str(exc.value) and "50000" in str(exc.value)


def test_un_402_sin_precio_no_se_firma() -> None:
    sin_precio = {k: v for k, v in CHALLENGE_402.items() if k not in ("price_usd", "amount")}
    with pytest.raises(DoNotPayError):
        build_payment_header(PayerQueExplota(), sin_precio, network="base")


# ---------------------------------------------------------------------------
# El registro de redes se le pregunta al uvd-x402-sdk, no hay tabla local
# ---------------------------------------------------------------------------


def test_las_seis_cadenas_resuelven_contra_el_registro_del_sdk_de_pagos() -> None:
    """No hay tabla `nombre → chain id` en este repo: se la pregunta a su dueño.

    Si el SDK de pagos no está instalado (es un extra), esto se saltea — el
    camino gratis no lo necesita.
    """
    pytest.importorskip("uvd_x402_sdk")
    esperado = {
        "eip155:8453": "base",
        "eip155:43114": "avalanche",
        "eip155:42161": "arbitrum",
        "eip155:10": "optimism",
        "eip155:137": "polygon",
        "eip155:42220": "celo",
    }
    for caip2, nombre in esperado.items():
        assert chain_name_for(caip2) == nombre, caip2


def test_sin_el_sdk_de_pagos_el_error_nombra_LO_QUE_FALTA(monkeypatch) -> None:
    """🔴 Regresión de un bug que este archivo encontró al escribirse.

    Antes, sin el extra `[x402]` instalado, `chain_name_for` devolvía `None`
    para las seis cadenas y el mensaje era **«describe no ofrece pagar en
    `base`; ofrece: eip155:8453, eip155:43114»** — que manda a revisar el
    challenge del servicio cuando lo que falta es un `pip install`.

    Es exactamente el modo de falla que el repo del servicio persigue en su
    propia documentación: *«una entrada que manda al lugar equivocado es peor
    que ninguna»*, porque quien la lee ejecuta la receta, sigue en rojo y no
    busca más. Este test exige que el mensaje diga la causa real.
    """
    from uvd_describe_sdk import payment

    monkeypatch.setattr(payment, "chain_name_for", lambda _caip2: None)
    monkeypatch.setattr(payment, "_registry_available", lambda: False)

    with pytest.raises(DoNotPayError) as exc:
        payment.build_payment_header(PayerQueExplota(), CHALLENGE_402, network="base")

    mensaje = str(exc.value)
    assert "uvd-x402-sdk" in mensaje
    assert "pip install uvd-describe-sdk[x402]" in mensaje


def test_sin_el_registro_pero_con_caip2_igual_se_puede_seleccionar(monkeypatch) -> None:
    """Seleccionar por CAIP-2 o por chain id no necesita el registro.

    Sirve para que el fallo, cuando llega, sea el de FIRMAR (que sí lo necesita)
    y no el de elegir — dos causas distintas, dos mensajes distintos.
    """
    from uvd_describe_sdk import payment

    monkeypatch.setattr(payment, "chain_name_for", lambda _caip2: None)
    assert payment.select_accept(CHALLENGE_402, "eip155:43114")["amount"] == "10000"
    assert payment.select_accept(CHALLENGE_402, "8453")["network"] == "eip155:8453"


def test_un_caip2_no_evm_no_resuelve_a_nada() -> None:
    """Un `solana:...` no tiene chain id EVM. Devolver algo lo mandaría a firmar
    contra la red equivocada."""
    assert chain_name_for("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp") is None
    assert chain_name_for("basura") is None
    assert chain_name_for("eip155:no-es-un-numero") is None


# ---------------------------------------------------------------------------
# Después de pagar
# ---------------------------------------------------------------------------


def test_un_4xx_despues_de_pagar_dice_que_no_se_reintente(make_client) -> None:
    """El nonce se consume en el settlement: reenviar la misma credencial no
    vuelve a pagar. El mensaje tiene que mandar a pedir un challenge NUEVO."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response({"detail": "nonce spent"}, status=409)

    with make_client(handler, payer=PayerEspia()) as c:
        with pytest.raises(DescribeHTTPError) as exc:
            c.wallet_breakdown("0xdead")
    assert exc.value.status_code == 409
    assert "no" in str(exc.value).lower() and "reenvíes" in str(exc.value)


def test_no_hay_reintento_automatico(make_client) -> None:
    """Un `retries=` acá quemaría credenciales. Se cuentan las requests."""
    intentos: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        intentos.append(request)
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response({"detail": "nope"}, status=500)

    with make_client(handler, payer=PayerEspia()) as c:
        with pytest.raises(DescribeHTTPError):
            c.wallet_breakdown("0xdead")
    assert len(intentos) == 2  # uno sin header, uno con. Y se acabó.
