"""El riel de PARTNER: entrar gratis a las rutas medidas, y enterarse si se cae.

QUÉ FIJA ESTE ARCHIVO, Y POR QUÉ NO ES EL CAMINO FELIZ
------------------------------------------------------
Que un partner con la wallet dada de alta entre gratis es fácil de testear y no
prueba casi nada: un cliente que ignorara la firma y pagara religiosamente
**también devolvería el breakdown**. La respuesta llega igual, el código
funciona, y lo único distinto es una factura de USDC que aparece semanas
después. Ese es exactamente el bug que un riel silencioso produce, y es el
espejo del que persigue el gate del otro lado: *«un bug acá no rompe nada, no
tira error, y regala el producto»* (`describe-net/tests/test_partner_gate.py`).

🔴 **EL TEST QUE IMPORTA es `test_si_el_riel_CAE_el_cliente_LEVANTA_y_NO_paga`.**
Monta el estado malo —402 pese a la firma— con un `payer` que EXPLOTA si lo
llaman, y exige `PartnerRejectedError`. Sin esa rama en `client._paid`, el
cliente cae al camino de pago y el test se pone rojo por el payer. Es la única
prueba que distingue «el riel entró» de «el riel entró o pagó, quién sabe».

El segundo par que importa es `test_lo_que_se_firma_es_BYTE_A_BYTE_lo_que_sale`:
la base de firma cubre `@query` sólo cuando la URL tiene query, así que firmar
una URL rearmada a mano y mandar otra produce un 402 que nadie entiende.
Medido el 2026-08-30 contra el gate REAL del servicio: firmada sin
`?snapshot=true` y pedida con él ⇒ `PartnerGate.check` devuelve `None`.

CERO CLAVES, CERO CRIPTOGRAFÍA, CERO RED
-----------------------------------------
Ningún test de acá toca una private key, y no es por prolijidad: el
`PartnerSigner` es un Protocol de dos métodos, así que el doble devuelve un hex
fijo y `sign_request` lo mete en base64 sin mirarlo. Lo que se prueba es la
POLÍTICA del cliente —cuándo firma, qué firma, y qué hace cuando no puede—; de
la criptografía responde el `uvd-x402-sdk` con sus vectores dorados.

Que el riel de verdad abre la puerta del servicio no se puede probar acá y se
dice en vez de esconderse: eso se midió aparte, contra `describenet.partner`
importado y su misma `VerifyPolicy`, y está anotado en `partner.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import pytest

from uvd_describe_sdk import (
    DescribeClient,
    PartnerRejectedError,
    PartnerSigner,
    PartnerSigningError,
    PaymentRequiredError,
)

from .conftest import BREAKDOWN, CHALLENGE_402, HEALTH, LEADERBOARD, json_response
from .conftest import WALLET_CON_REPUTACION as WALLET

WALLET_PARTNER = "0x00000000000000000000000000000000000000ab"
RUTA = "/reputation/wallet/0x97cd97cfe21799bacbf39d0a53469e5f82f30996"


class FirmanteEspia:
    """Satisface `PartnerSigner` estructuralmente. **No hay ninguna clave acá.**

    Devuelve un hex fijo de 65 bytes: `sign_request` lo pasa por
    `bytes.fromhex` y lo emite en base64 sin verificar nada. Lo valioso es
    `bases`, que guarda el texto EXACTO que se le pidió firmar — o sea las
    líneas `"@method"`, `"@authority"`, `"@path"` y `"@query"` de RFC 9421.
    Ahí se lee, en claro, qué request se firmó.
    """

    def __init__(self, address: str = WALLET_PARTNER) -> None:
        self._address = address
        self.bases: List[str] = []

    def get_address(self) -> str:
        return self._address

    def sign_message(self, message: str) -> str:
        self.bases.append(message)
        return "0x" + "11" * 65


class FirmanteQueExplota:
    """Un firmante roto: un KMS caído, una Ledger desconectada, un typo."""

    def get_address(self) -> str:
        return WALLET_PARTNER

    def sign_message(self, message: str) -> str:
        raise RuntimeError("el KMS no contestó")


class PayerQueExplota:
    """Un payer que falla si lo llaman. Prueba que NO se lo llamó."""

    def create_authorization(self, *args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            "🔴 EL CLIENTE PAGÓ. Con `partner=` configurado, un 402 es un riel "
            "roto y tiene que LEVANTAR: pagar en silencio es la factura que "
            "aparece semanas después."
        )


def linea(base: str, componente: str) -> Optional[str]:
    """El valor de un componente de la base de firma (`"@path": /foo`)."""
    for renglon in base.splitlines():
        if renglon.startswith(f'"{componente}": '):
            return renglon.split(": ", 1)[1]
    return None


# ---------------------------------------------------------------------------
# El camino feliz: el riel abre, y el payer ni se entera
# ---------------------------------------------------------------------------


def test_el_partner_entra_a_una_ruta_medida_SIN_pagar(make_client) -> None:
    """Con firma, la ruta de $0,01 vuelve 200 en el PRIMER intento.

    Un solo GET en toda la historia: no hay 402, no hay challenge, no hay
    segunda request con `X-PAYMENT`. Y el `payer` está puesto justamente para
    que su explosión pruebe que no se lo tocó.
    """
    vistas: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        if "signature" in request.headers:
            return json_response(BREAKDOWN)
        return json_response(CHALLENGE_402, status=402)

    client = make_client(
        handler, partner=FirmanteEspia(), payer=PayerQueExplota()
    )
    resultado = client.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert resultado.final_score == 86.653045
    assert len(vistas) == 1, "el riel no debería necesitar un segundo intento"
    assert "x-payment" not in vistas[0].headers


def test_el_cliente_dice_si_sale_con_riel(make_client) -> None:
    """`is_partner` afirma la CONFIGURACIÓN, no la exención.

    Es la distinción honesta: si la wallet está en la allowlist sólo lo sabe
    describe, y sólo se sabe pidiendo. Lo que esta propiedad evita es arrancar
    creyendo que hay riel cuando nadie lo configuró.
    """
    sin = make_client(lambda r: json_response(WALLET))
    con = make_client(lambda r: json_response(WALLET), partner=FirmanteEspia())
    assert sin.is_partner is False
    assert con.is_partner is True


# ---------------------------------------------------------------------------
# 🔴 EL TEST QUE IMPORTA: el riel roto LEVANTA, y no paga
# ---------------------------------------------------------------------------


def test_si_el_riel_CAE_el_cliente_LEVANTA_y_NO_paga(make_client) -> None:
    """402 pese a la firma ⇒ `PartnerRejectedError`. **El payer no se toca.**

    Es el estado malo entero: la wallet no está en la allowlist (o el reloj se
    corrió, o el `base_url` no es el de describe). Un cliente que dejara caer
    el partner al camino de pago devolvería el mismo breakdown y nadie se
    enteraría hasta la factura. Por eso el payer explota: es lo único que
    convierte «pagó en silencio» en un rojo.

    Y `payment_sent is False` no es decorativo: dice que el consumidor perdió
    el riel **sin haber gastado** el USDC que el riel le ahorraba.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(CHALLENGE_402, status=402)

    client = make_client(handler, partner=FirmanteEspia(), payer=PayerQueExplota())

    with pytest.raises(PartnerRejectedError) as exc:
        client.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert exc.value.payment_sent is False
    assert exc.value.payment is None
    assert exc.value.kind == "partner_rejected"


def test_el_error_del_riel_roto_NOMBRA_la_wallet_publica(make_client) -> None:
    """El remedio exige saber cuál wallet firmó: va en el atributo y en el texto.

    La dirección es pública por diseño —es lo único que describe guarda de un
    partner— así que decirla no filtra nada y es lo primero que hay que citar
    para el alta. Se afirma en el ATRIBUTO (que es por donde se ramifica) y en
    el mensaje (que es lo único que hay en un traceback a las 3 AM).
    """
    client = make_client(
        lambda r: json_response(CHALLENGE_402, status=402), partner=FirmanteEspia()
    )
    with pytest.raises(PartnerRejectedError) as exc:
        client.wallet_breakdown("0x97cd…")

    assert exc.value.wallet == WALLET_PARTNER
    assert WALLET_PARTNER in str(exc.value)


def test_el_riel_roto_trae_el_challenge_y_dice_cuanto_iba_a_costar(
    make_client,
) -> None:
    """Hereda de `PaymentRequiredError`, y la herencia dice algo verdadero.

    Los dos casos son «llegó un 402 y este cliente no puso un centavo». Quien
    ya escribía `except PaymentRequiredError` la atrapa sin cambiar una línea,
    y `price_usd` le dice cuánto le iba a costar el riel roto.
    """
    client = make_client(
        lambda r: json_response(CHALLENGE_402, status=402), partner=FirmanteEspia()
    )
    with pytest.raises(PaymentRequiredError) as exc:
        client.wallet_breakdown("0x97cd…")

    assert isinstance(exc.value, PartnerRejectedError)
    assert exc.value.price_usd == "0.01"
    assert exc.value.challenge["free_preview"]["endpoint"] == "GET /wallets/{wallet}/chains"


def test_un_402_ILEGIBLE_con_partner_sigue_siendo_un_riel_roto(make_client) -> None:
    """Un cuerpo que no es JSON no puede desviar el diagnóstico.

    Reportarlo como `DescribeUnparseable` mandaría a revisar el JSON del
    servicio cuando lo que hay que mirar es la allowlist — la clase de entrada
    que manda al lugar equivocado, que es peor que ninguna.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, content=b"<html>no soy json</html>")

    client = make_client(handler, partner=FirmanteEspia(), payer=PayerQueExplota())
    with pytest.raises(PartnerRejectedError) as exc:
        client.wallet_breakdown("0x97cd…")
    assert exc.value.challenge == {}


def test_el_fail_open_NO_traga_un_riel_roto(make_client) -> None:
    """Ni con `fail_open=True` explícito: no es una caída del índice.

    Es configuración de quien llama, igual que `PaymentRequiredError`.
    Degradarla a `None` convertiría «tu riel gratis está roto» en «esta wallet
    no tiene reputación», que es la mentira que R1 existe para impedir.
    """
    client = make_client(
        lambda r: json_response(CHALLENGE_402, status=402),
        partner=FirmanteEspia(),
        fail_open=True,
    )
    with pytest.raises(PartnerRejectedError):
        client.wallet_breakdown("0x97cd…")


# ---------------------------------------------------------------------------
# Un firmante roto levanta ANTES de pedir nada
# ---------------------------------------------------------------------------


def test_un_firmante_roto_LEVANTA_y_no_sale_una_sola_request(make_client) -> None:
    """El KMS caído se descubre antes del primer byte, no con la factura.

    Seguir sin firma sería el camino silencioso al gasto: la request saldría
    limpia, describe cobraría con razón, y el consumidor creería que su riel
    funciona. `PartnerSigningError` con CERO requests es lo contrario.
    """
    vistas: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return json_response(BREAKDOWN)

    client = make_client(
        handler, partner=FirmanteQueExplota(), payer=PayerQueExplota()
    )
    with pytest.raises(PartnerSigningError) as exc:
        client.wallet_breakdown("0x97cd…")

    assert vistas == [], "se pidió algo pese a no haber podido firmar"
    assert exc.value.payment_sent is False
    assert exc.value.kind == "partner_signing"
    # El mensaje nombra la causa REAL, no «falló el partner».
    assert "el KMS no contestó" in str(exc.value)
    assert exc.value.wallet == WALLET_PARTNER


def test_sin_el_sdk_de_pagos_el_error_nombra_LO_QUE_FALTA(
    make_client, monkeypatch
) -> None:
    """El extra sin instalar tiene que decirse por su nombre.

    Es la lección que `payment.chain_name_for` ya se comió una vez: sin el
    `uvd-x402-sdk`, el error nombraba la causa equivocada y mandaba a revisar
    el challenge del servicio cuando faltaba un `pip install`.
    """
    import builtins

    real_import = builtins.__import__

    def sin_erc8128(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("uvd_x402_sdk.erc8128"):
            raise ImportError("No module named 'uvd_x402_sdk'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sin_erc8128)

    client = make_client(lambda r: json_response(BREAKDOWN), partner=FirmanteEspia())
    with pytest.raises(PartnerSigningError) as exc:
        client.wallet_breakdown("0x97cd…")

    assert "uvd-describe-sdk[partner]" in str(exc.value)


# ---------------------------------------------------------------------------
# 🔴 Se firma lo que se manda, byte a byte
# ---------------------------------------------------------------------------


def test_lo_que_se_firma_es_BYTE_A_BYTE_lo_que_sale(make_client) -> None:
    """La base firmada y la URL que vio el transporte son la misma request.

    Es el modo de falla medido contra el gate real (2026-08-30): firmar sin
    `?snapshot=true` y mandar con él ⇒ el gate no verifica y se cobra. Acá se
    compara la línea `"@path"` de la base contra el path que salió, y la
    `"@query"` contra la query que salió — o sea los componentes exactos que el
    servidor va a reconstruir.
    """
    firmante = FirmanteEspia()
    vistas: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return json_response(BREAKDOWN)

    client = make_client(handler, partner=firmante)
    client.wallet_breakdown(
        "0x97cd97cfe21799bacbf39d0a53469e5f82f30996", snapshot=True
    )

    base = firmante.bases[0]
    salida = vistas[0].url
    assert linea(base, "@path") == salida.path
    assert linea(base, "@query") == f"?{salida.query.decode()}"
    assert linea(base, "@authority") == "api.describe.net"
    assert linea(base, "@method") == "GET"


def test_sin_query_la_base_NO_inventa_una(make_client) -> None:
    """`@query` se cubre sólo cuando la URL tiene query — la regla canónica.

    Cubrir un `?` vacío produciría una base que el verificador no reconstruye
    igual. Se afirma el par completo: con `snapshot` hay `@query`, sin él no.
    """
    firmante = FirmanteEspia()
    client = make_client(lambda r: json_response(BREAKDOWN), partner=firmante)
    client.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert linea(firmante.bases[0], "@query") is None
    assert linea(firmante.bases[0], "@path") is not None


def test_la_firma_pinnea_la_cadena_8453(make_client) -> None:
    """El keyid lleva Base, y el número no se hereda de un default ajeno.

    El gate del servicio fija UNA cadena (`describenet/partner.py::CHAIN_ID`).
    Que hoy coincida con el default del SDK de pagos no lo hace seguro: heredar
    un default ajeno para un valor que el servidor compara es firmar contra lo
    que otro repo decida mañana.
    """
    firmante = FirmanteEspia()
    client = make_client(lambda r: json_response(BREAKDOWN), partner=firmante)
    client.wallet_breakdown("0x97cd…")

    params = linea(firmante.bases[0], "@signature-params") or ""
    assert f'keyid="erc8128:8453:{WALLET_PARTNER}"' in params
    assert 'alg="eip191"' in params


# ---------------------------------------------------------------------------
# Las rutas GRATIS no se firman — y está medido del otro lado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "payload"),
    [
        ("wallet", WALLET),
        ("leaderboard", LEADERBOARD),
        ("health", HEALTH),
    ],
)
def test_las_rutas_GRATIS_no_se_firman(make_client, metodo, payload) -> None:
    """Firmar `/health` no cambia NADA, y con un firmante remoto cuesta.

    Medido del otro lado: `authorize` devuelve `Decision(True, REASON_FREE, …)`
    antes de mirar el `partner_id` (`describe-net/describenet/paywall.py:772`
    contra :794). O sea que una firma en una ruta gratis no puede cambiar el
    resultado — y sí puede costar una ida al KMS por cada lectura.
    """
    firmante = FirmanteEspia()
    vistas: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return json_response(payload)

    client = make_client(handler, partner=firmante)
    args = ("0x97cd…",) if metodo == "wallet" else ()
    getattr(client, metodo)(*args)

    assert firmante.bases == [], "se firmó una ruta que es gratis para todos"
    assert "signature" not in vistas[0].headers


# ---------------------------------------------------------------------------
# El modo partner NO mueve R5, ni en un sentido ni en el otro
# ---------------------------------------------------------------------------


def test_con_partner_las_rutas_GRATIS_siguen_haciendo_fail_open(
    make_client, recorded_errors
) -> None:
    """Un partner no deja de ser un lector: `None` observado, nunca `[]`."""
    def caido(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("describe no contesta")

    client = make_client(
        caido, partner=FirmanteEspia(), on_error=recorded_errors.append
    )
    assert client.wallet("0x97cd…") is None
    assert client.leaderboard() is None
    assert client.health() is None
    assert len(recorded_errors) == 3


def test_con_partner_las_rutas_MEDIDAS_siguen_levantando(make_client) -> None:
    """Que te salgan gratis no las convierte en rutas gratis.

    El criterio de R5 nunca fue el precio que pagaste sino si hubo dinero de
    por medio; un timeout después de un 200 del riel sigue siendo un fallo
    ruidoso, con `fail_open=True` explícito incluido.
    """
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("tardó")

    client = make_client(timeout, partner=FirmanteEspia(), fail_open=True)
    with pytest.raises(Exception) as exc:
        client.wallet_breakdown("0x97cd…")
    assert exc.value.__class__.__name__ == "DescribeTimeout"
    assert exc.value.payment_sent is False


# ---------------------------------------------------------------------------
# Sin partner, nada cambió
# ---------------------------------------------------------------------------


def test_sin_partner_el_camino_de_pago_sigue_INTACTO(make_client) -> None:
    """La regresión que importa: el 99 % de los consumidores no es partner.

    Sin `partner=` no se firma nada, el 402 sigue siendo una señal de cobro y
    el baile de dos requests con `X-PAYMENT` funciona igual que antes.
    """
    intentos: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        intentos.append({"pago": request.headers.get("x-payment")})
        if request.headers.get("x-payment"):
            return json_response(BREAKDOWN)
        return json_response(CHALLENGE_402, status=402)

    class PayerBobo:
        def create_authorization(self, *a: Any, **k: Any) -> str:
            return "BASE64-DE-MENTIRA"

    client = make_client(handler, payer=PayerBobo())
    assert client.wallet_breakdown("0x97cd…").final_score == 86.653045
    assert [i["pago"] for i in intentos] == [None, "BASE64-DE-MENTIRA"]


def test_el_Protocol_lo_satisface_cualquiera_con_dos_metodos() -> None:
    """`PartnerSigner` es estructural: un firmante propio no importa nada nuestro.

    Es lo que permite un KMS, un HSM o una Ledger sin que este SDK sepa que
    existen — y lo que permite que estos tests corran sin una sola clave.
    """
    assert isinstance(FirmanteEspia(), PartnerSigner)

    class NoFirma:
        def get_address(self) -> str:
            return WALLET_PARTNER

    assert not isinstance(NoFirma(), PartnerSigner)


def test_el_cliente_de_ejemplo_del_docstring_no_toca_ninguna_clave() -> None:
    """Guardia contra la regresión más cara que este archivo puede tener.

    El día que alguien «simplifique» el modo partner aceptando una clave por
    parámetro o leyéndola de una env var, este test debería ser lo que lo
    frene: la firma pública de `DescribeClient` no tiene ningún parámetro que
    huela a secreto. (INC-2026-03-30: dos wallets de la casa drenadas por
    claves en repos públicos.)
    """
    import inspect

    firma = inspect.signature(DescribeClient.__init__).parameters
    prohibidos = [p for p in firma if "key" in p.lower() or "secret" in p.lower()]
    assert prohibidos == []
    assert "partner" in firma
