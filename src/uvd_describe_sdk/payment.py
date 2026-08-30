"""La única caseta de peaje — R6. Este SDK **jamás** firma, custodia ni deriva.

════════════════════════════════════════════════════════════════════════════
LA REGLA, Y NO ES NEGOCIABLE
════════════════════════════════════════════════════════════════════════════
El 402 lo resuelve `uvd-x402-sdk` (PyPI `uvd-x402-sdk`, 0.70.0 al 2026-08-30).
Acá **no se reimplementa EIP-3009, no se arma el sobre a mano, no se toca una
clave privada y no se lee una env var con un secreto**. Es la regla
*upstream-first* de la casa: si al SDK de pagos le falta algo, se sube ALLÁ y
después se consume — no se parchea acá.

Lo que este módulo hace es exactamente tres cosas, y ninguna es criptográfica:

  1. **Verificar a quién se le va a pagar** contra la tesorería pinneada.
  2. **Elegir** cuál de los `accepts[]` corresponde a la red que el llamador
     dice tener fondos.
  3. **Delegar** la firma al payer, echando de vuelta el accept VERBATIM.

════════════════════════════════════════════════════════════════════════════
🔴 EL CHEQUEO DE DESTINATARIO: `DO_NOT_PAY`, NO UN RETRY
════════════════════════════════════════════════════════════════════════════
Paso 2 de la guía publicada (docs.describe.net, «Paying with x402»):

    «The only address this service ever asks to be paid at is
     0xe4dc963c56979E0260fc146b87eE24F18220e545. If the challenge names another
     address, do not pay: either it did not come from describe, or the treasury
     changed and the server did not find out. Raw HTTP does not make this
     comparison for you. Pin the address in your own code.»

Reintentar ahí sería convertir un desvío de fondos en un desvío de fondos con
reintentos. Por eso `DoNotPayError` **no la traga el fail-open** y no hay un
`retries=` que la pueda pisar.

La tesorería está pinneada como default y es configurable por constructor —
para un despliegue propio del índice, no para «desactivar el chequeo». Si
alguien pasa `treasury=None` no se paga: se levanta `ConfigurationError` del
propio payer o el chequeo falla. Nunca hay un camino silencioso.

════════════════════════════════════════════════════════════════════════════
POR QUÉ NO HAY UNA TABLA `nombre de red → chain id` EN ESTE REPO
════════════════════════════════════════════════════════════════════════════
El `accepts[].network` viene en CAIP-2 (`eip155:8453`) y `create_authorization`
quiere un nombre (`"base"`). La traducción **se le pregunta al registro del
`uvd-x402-sdk`**, que es su dueño: se recorre `get_supported_network_names()` y
se matchea por `chain_id`. Una tabla local sería una copia que se pudre, y
copiar lo que el SDK ya sabe es justo lo que upstream-first prohíbe.

Verificado el 2026-08-30 contra `uvd_x402_sdk.networks.base`: las seis cadenas
que describe acepta hoy —8453 base, 43114 avalanche, 42161 arbitrum, 10
optimism, 137 polygon, 42220 celo— resuelven las seis en el registro.

════════════════════════════════════════════════════════════════════════════
EL `payer` ES UN PROTOCOL, NO UNA DEPENDENCIA DURA
════════════════════════════════════════════════════════════════════════════
`uvd-x402-sdk` es un **extra** (`pip install uvd-describe-sdk[x402]`), no una
dependencia base. Un consumidor que sólo usa las rutas gratis —que es el camino
feliz del producto: R «gratis-primero», y el 402 mismo lo dice: *«Si no hay
reputación ahí, este cobro no devuelve nada»*— no arrastra `eth-account` ni
nada que firme.

`X402Client` satisface `Payer` estructuralmente, sin heredar de nada nuestro.
Y para los tests eso vale doble: se mockea con un objeto de diez líneas y
**ningún test toca una clave**.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - depende de la versión de Python
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable

from .errors import DoNotPayError

#: La tesorería de describe, pinneada. Leída VIVA del challenge de
#: `GET /reputation/wallet/{w}` el 2026-08-30 (campos `recipient` y
#: `recipients.evm`, y el `payTo` de las seis entradas de `accepts[]`).
#: Coincide con `paywall.TREASURY_EVM` del servicio, que la mantiene sincronizada
#: con `api_docs.PUBLISHED_TREASURY` y con su Terraform (invariante 5 del repo).
TREASURY_EVM = "0xe4dc963c56979E0260fc146b87eE24F18220e545"

#: USDC tiene 6 decimales en las seis cadenas que describe acepta. Se usa SÓLO
#: para reconciliar el precio en dólares contra las unidades base que mandó el
#: servidor — nunca para construir el monto, que lo calcula el SDK de pagos.
_USDC_DECIMALS = 6


@runtime_checkable
class Payer(Protocol):
    """Lo único que este SDK le pide a quien firma.

    `uvd_x402_sdk.X402Client` lo satisface tal cual, sin heredar nada:

        from uvd_x402_sdk import X402Client
        payer = X402Client(recipient_address=TREASURY_EVM)
        payer.connect_with_private_key(os.environ["MI_CLAVE"], chain="base")
        client = DescribeClient(payer=payer, pay_network="base")

    🔴 La clave sale de una env var o de un signer remoto
    (`connect_with_signer`). **Jamás se escribe en un archivo**, ni «para
    probar»: hay bots barriendo GitHub por `0x`+64 hex y drenan en minutos
    (INC-2026-03-30 de la casa, dos wallets perdidas así).
    """

    def create_authorization(
        self,
        pay_to: str,
        amount_usd: Decimal,
        *,
        chain_name: Optional[str] = ...,
        x402_version: int = ...,
        accepted: Optional[Dict[str, Any]] = ...,
        resource: Optional[Any] = ...,
    ) -> str:
        """Devuelve el valor base64 del header `X-PAYMENT`."""
        ...


def _chain_id_of(caip2: str) -> Optional[int]:
    """`"eip155:8453"` → `8453`. Cualquier otra cosa → `None`.

    No se asume el prefijo: un `solana:...` no tiene chain id EVM y devolver
    algo acá lo mandaría a firmar contra la red equivocada.
    """
    if not isinstance(caip2, str) or not caip2.startswith("eip155:"):
        return None
    try:
        return int(caip2.split(":", 1)[1])
    except ValueError:
        return None


def _registry_available() -> bool:
    """¿Está instalado el `uvd-x402-sdk`, o sea el dueño del registro de redes?

    Existe como pregunta separada por una lección que este mismo archivo se
    comió al escribirlo: sin el extra instalado, `chain_name_for` devolvía
    `None` para las seis cadenas y `select_accept` levantaba **«describe no
    ofrece pagar en `base`»** — un mensaje que manda a revisar el challenge del
    servicio cuando el problema es un `pip install` que falta.

    Es exactamente el modo de falla que el repo del servicio persigue: *«una
    entrada que manda al lugar equivocado es peor que ninguna»*, porque el que
    la lee ejecuta la receta, sigue en rojo y no busca más. Así que la causa se
    distingue y se dice con su nombre.
    """
    try:
        import uvd_x402_sdk.networks.base  # noqa: F401
    except ImportError:
        return False
    return True


def chain_name_for(caip2: str) -> Optional[str]:
    """CAIP-2 → el nombre de red del `uvd-x402-sdk`, preguntándole a su registro.

    Devuelve `None` si el SDK de pagos no está instalado o no conoce esa cadena.
    Para distinguir esas dos causas está `_registry_available()`. Ninguna tabla
    local: ver la cabecera del módulo.
    """
    chain_id = _chain_id_of(caip2)
    if chain_id is None:
        return None
    try:
        from uvd_x402_sdk.networks.base import get_network, get_supported_network_names
    except ImportError:
        return None
    for name in get_supported_network_names():
        network = get_network(name)
        if network is not None and getattr(network, "chain_id", None) == chain_id:
            # `str()` y no `name` a secas: el registro del SDK de pagos no trae
            # `py.typed`, asi que mypy ve `Any` y `warn_return_any` lo marca.
            # Es un hueco UPSTREAM (ver README, "Lo que le falta al SDK de
            # pagos"): se convierte aca en vez de silenciar el warning.
            return str(name)
    return None


def _matches(accept: Dict[str, Any], network: str) -> bool:
    """¿Este accept es la red que pidió quien llama?

    Se acepta el nombre (`"base"`), el CAIP-2 (`"eip155:8453"`) y el chain id
    (`8453` o `"8453"`). Los dos últimos **no necesitan el registro del SDK de
    pagos**, así que quien no lo tenga instalado igual puede seleccionar — y el
    error, si falta algo, va a nombrar lo que de verdad falta.
    """
    caip2 = str(accept.get("network") or "")
    if network == caip2:
        return True
    chain_id = _chain_id_of(caip2)
    if chain_id is not None and str(network) == str(chain_id):
        return True
    return chain_name_for(caip2) == network


def select_accept(challenge: Dict[str, Any], network: str) -> Dict[str, Any]:
    """Elegir la entrada de `accepts[]` para la red donde el llamador tiene fondos.

    Se devuelve **la entrada tal cual llegó**, sin normalizar ni reconstruir: el
    `uvd-x402-sdk` la echa de vuelta VERBATIM en el sobre v2 y su propio código
    lo advierte — *«Reconstructing it instead of echoing is how a payment gets
    rejected by a server that did nothing wrong»* (`client.py:1799-1802`).

    Si la red pedida no está entre las ofrecidas, el error **lista las que sí**:
    un «unsupported network» sin la lista obliga a leer el challenge a mano.
    """
    accepts = challenge.get("accepts") or []
    offered: List[str] = []
    for accept in accepts:
        if not isinstance(accept, dict):
            continue
        caip2 = str(accept.get("network") or "")
        offered.append(chain_name_for(caip2) or caip2)
        if _matches(accept, network):
            return accept

    falta_el_sdk = (
        "" if _registry_available() else
        " — ojo: `uvd-x402-sdk` NO está instalado, así que los nombres de red no "
        "se pueden resolver y arriba ves los CAIP-2 crudos. Instalá el extra: "
        "`pip install uvd-describe-sdk[x402]`"
    )
    raise DoNotPayError(
        f"describe no ofrece pagar en `{network}`; ofrece: "
        f"{', '.join(offered) or '(ninguna)'}{falta_el_sdk}",
        expected=network,
        offered=", ".join(offered),
    )


def assert_recipient(accept: Dict[str, Any], treasury: str) -> None:
    """El chequeo del paso 2. Falla ⇒ `DO_NOT_PAY`, jamás un reintento.

    La comparación es case-insensitive porque una dirección EVM es la misma con
    o sin checksum EIP-55 — el challenge la manda con mayúsculas y una wallet
    puede tenerla en minúsculas. Lo que NO se afloja es la dirección: compara
    los 20 bytes, no un prefijo.
    """
    pay_to = str(accept.get("payTo") or "")
    if not pay_to or pay_to.lower() != treasury.lower():
        raise DoNotPayError(
            "el 402 pide pagar a una dirección que NO es la tesorería pinneada de "
            f"describe. NO se paga y NO se reintenta. esperada={treasury} "
            f"ofrecida={pay_to or '(vacía)'}",
            expected=treasury,
            offered=pay_to,
        )


def _amount_usd(challenge: Dict[str, Any], accept: Dict[str, Any]) -> Decimal:
    """El precio en USD, tomado del challenge y **reconciliado** con las unidades base.

    `Decimal`, nunca `float`: un `float("0.01")` ya no es 0,01 y esto es plata.

    La reconciliación existe por un modo de falla que la guía publicada nombra:
    *«A 4xx after paying is almost always a spent credential or one signed for a
    different amount»*. Si `price_usd` y `accepts[].amount` no cierran, firmar
    igual produce un pago que el servidor rechaza **después** de que la
    credencial se consumió. Preferimos negarnos antes.

    Sólo se reconcilia cuando el token declarado es USDC (6 decimales en las
    seis cadenas de hoy). Con otro token no se inventa una escala: se firma por
    `price_usd` y se deja dicho acá que ese caso no está cubierto.
    """
    raw_price = challenge.get("price_usd", challenge.get("amount"))
    if raw_price is None:
        raise DoNotPayError(
            "el 402 no trae `price_usd` ni `amount`: no hay precio que firmar",
            expected="price_usd",
            offered="(ausente)",
        )
    price = Decimal(str(raw_price))

    token = str(challenge.get("token") or "").upper()
    base_units = accept.get("amount")
    if token == "USDC" and base_units is not None:
        expected_base = int(price * (10**_USDC_DECIMALS))
        if int(base_units) != expected_base:
            raise DoNotPayError(
                f"el 402 no cierra consigo mismo: price_usd={price} son "
                f"{expected_base} unidades base pero `accepts[].amount` dice "
                f"{base_units}. Firmar por un monto distinto del pedido gasta la "
                "credencial y el servidor igual contesta 4xx.",
                expected=str(expected_base),
                offered=str(base_units),
            )
    return price


def build_payment_header(
    payer: Payer,
    challenge: Dict[str, Any],
    *,
    network: str,
    treasury: str = TREASURY_EVM,
) -> str:
    """Del challenge 402 al valor del header `X-PAYMENT`. La firma la hace el payer.

    Orden deliberado: **primero se verifica a quién se paga, después se firma.**
    Al revés existiría, aunque sea por un instante, una autorización firmada
    hacia una dirección no verificada.

    Todo lo que viaja sale del challenge —monto, token, `payTo`, red, recurso—
    y nada de una tabla local: paso 1 de la guía publicada, *«Take the values
    from there, never from a cached table.»*
    """
    accept = select_accept(challenge, network)
    assert_recipient(accept, treasury)
    amount = _amount_usd(challenge, accept)

    chain_name = chain_name_for(str(accept.get("network") or ""))
    if chain_name is None:
        # Acá SÍ hace falta el registro: `create_authorization` quiere un nombre
        # y no hay forma de derivarlo de un chain id sin quien lo sepa. El
        # mensaje nombra la causa real en vez de mandar a mirar el challenge.
        detalle = (
            "`uvd-x402-sdk` no está instalado: `pip install uvd-describe-sdk[x402]`"
            if not _registry_available()
            else f"su registro de redes no conoce `{accept.get('network')}`"
        )
        raise DoNotPayError(
            f"no se puede firmar para `{accept.get('network')}` — {detalle}",
            expected=network,
            offered=str(accept.get("network")),
        )

    # `resource` como OBJETO, no como el string pelado del challenge: el
    # facilitator exige url + description + mimeType y un string suelto no
    # matchea ninguna variante de su envelope, fallando con el opaco «data did
    # not match any variant» (documentado en `uvd_x402_sdk/client.py:1846-1852`).
    resource = {
        "url": str(challenge.get("resource") or ""),
        "description": str(challenge.get("description") or ""),
        "mimeType": str(challenge.get("mimeType") or "application/json"),
    }

    return payer.create_authorization(
        pay_to=str(accept["payTo"]),
        amount_usd=amount,
        chain_name=chain_name,
        # v2 porque es lo que describe anuncia: `x402Version: 2`, medido vivo el
        # 2026-08-30. En v2 el accept elegido se echa de vuelta VERBATIM.
        x402_version=int(challenge.get("x402Version") or 2),
        accepted=dict(accept),
        resource=resource,
    )
