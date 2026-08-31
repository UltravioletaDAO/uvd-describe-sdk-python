"""La taxonomía de fallas — y la línea que separa «no pude leer» de «no hay dato».

REGLA R4 DEL CONTRATO NÚCLEO, y es la razón de que este módulo exista:
**se lanza una excepción SÓLO por transporte o protocolo** (timeout, 5xx, JSON
inválido, un cuerpo que no tiene la forma prometida). **Nunca por «no hay
datos».** Una wallet sin calificar es una RESPUESTA — la puerta A2A del servicio
lo dice con todas las letras: *«That is an answer, not an error»*.

Por qué importa, medido: el 2026-08-28 en KarmaKadabra un fallo de lectura se
reportó como si fuera el dato («no pude leer» leído como «no tiene reputación»)
y costó un reporte equivocado en el gate que decide con quién se comercia
(`karmakadabra/lib/reputation_scan.py:110-118`, escrito por ese incidente). Esa
confusión es exactamente lo que esta taxonomía existe para hacer imposible.

────────────────────────────────────────────────────────────────────────────
DE DÓNDE SALEN ESTOS NOMBRES, Y QUÉ CAMBIÓ RESPECTO DE LA REFERENCIA
────────────────────────────────────────────────────────────────────────────
La implementación de referencia es
`execution-market/mcp_server/integrations/describenet/types.py:37-95` (578
líneas en total con su cliente, el lector HTTP más completo de los tres
consumidores medidos). Su taxonomía se ABSORBE casi entera:

    DescribeNetError → Timeout | HTTPError | Unreachable | Unparseable
                     | PartialIndex

y sobre todo se absorbe el mecanismo que la hace útil: **un atributo `kind`
estable sobre el que se ramifica, nunca el texto del mensaje** — el mismo
principio que el servicio aplica a `caveats[].code` vs `caveats[].text`.

Dos diferencias, declaradas acá porque quien venga de EM las va a notar y
merece encontrar la razón en vez de una sorpresa:

1. **`kind = "http_5xx"` se renombra a `"http_error"`.** En EM el bucket se
   llama `http_5xx` y su propio docstring aclara que ahí caen también el 422 y
   el 429 «porque todo consumidor trata cualquier non-2xx igual»
   (`types.py:56-60`). Un nombre que hay que desmentir en su propio docstring es
   un nombre mal puesto. `status_code` sigue viajando, que es lo que se lee.
   👉 El `kind` de EM se conserva como alias legible en `HTTP_5XX_LEGACY_KIND`
   para quien esté migrando un `if err.kind == "http_5xx"`.

2. **`PartialIndex` NO se porta.** En EM el cliente «nunca la lanza»
   (`types.py:84-89`): existe para que capas superiores clasifiquen una
   cobertura parcial dentro de la misma taxonomía. Un índice parcial se sirve
   como un 200 cuyo `chains[]` simplemente no trae la fila — o sea, es un DATO,
   y por R4 un dato no puede ser una excepción. Portarla acá sería publicar una
   excepción que el SDK jamás levanta, invitando a un `except` muerto.

────────────────────────────────────────────────────────────────────────────
LO QUE EL FAIL-OPEN **NO** TAPA
────────────────────────────────────────────────────────────────────────────
`PaymentRequiredError` y `DoNotPayError` heredan de `DescribeError` pero
`DescribeClient` NO las traga nunca, ni con `fail_open=True`. El fail-open
existe para la DISPONIBILIDAD del índice («poné un fallback si describe está
caído» — Saul, 2026-08-28), no para la configuración de quien llama. Tragarlas
convertiría «te olvidaste de configurar el pago» en «esta wallet no tiene
reputación», que es la misma mentira que R1 existe para impedir.

Y desde el 2026-08-30 hay un segundo eje: **las rutas PAGAS no las traga el
fail-open nunca, sea cual sea la excepción y valga lo que valga `fail_open`.**
Ver `client.py`, bloque «QUÉ MÉTODO ES NULLABLE».

Las dos del riel de partner —`PartnerSigningError` y `PartnerRejectedError`—
caen en la misma bolsa y por la misma razón: son configuración de quien llama.
Pero cargan además una afirmación que ninguna otra hace, y es la mitad buena
del asunto: **`payment_sent is False` es verdad fuerte ahí**. Las dos se
levantan ANTES de firmar ninguna autorización de pago, así que quien las reciba
sabe que no gastó — se enteró de que perdió el riel gratis SIN haber gastado el
USDC que el riel le ahorraba. Esa es toda la decisión del modo partner.

────────────────────────────────────────────────────────────────────────────
🔴 `payment_sent` — LA MARCA QUE DISTINGUE «NO GASTASTE» DE «PUEDE QUE SÍ»
────────────────────────────────────────────────────────────────────────────
Un `DescribeTimeout` pelado no distingue dos hechos que valen plata distinta:

    se cayó ANTES de firmar   → no salió una credencial. No gastaste nada.
    se cayó DESPUÉS de firmar → la autorización EIP-3009 ya está firmada y
                                despachada. El USDC pudo haberse movido.

El segundo caso es el que la R5 corregida protege: por eso las rutas pagas
levantan siempre. Pero levantar no alcanza si la excepción no dice de cuál de
los dos se trata — quien la reciba tiene que saber si le toca reconciliar.

`payment_sent` y `payment` son esa marca. Las pone `mark_payment_sent()` y sólo
las pone `DescribeClient` en el tramo posterior a la firma. En toda ruta gratis
valen `False` / `None`, siempre.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: El `kind` que usa `execution-market` para el mismo bucket. Se publica para
#: que una migración pueda comparar contra los dos sin adivinar.
HTTP_5XX_LEGACY_KIND = "http_5xx"


class DescribeError(Exception):
    """Base de toda falla de transporte o protocolo contra describe.

    `kind` es el contrato: se ramifica por ahí (o por la subclase), **jamás por
    el texto del mensaje**. Los mensajes se reescriben; los `kind` no.

    Lo mismo vale para `payment_sent`: se ramifica por el ATRIBUTO, nunca por
    buscar «después de pagar» en el mensaje.
    """

    kind: str = "unreachable"

    #: ¿Se levantó esta excepción DESPUÉS de que la credencial de pago firmada
    #: salió del proceso?
    #:
    #: 🔴 Qué prueba y qué NO prueba, porque la diferencia es la que importa:
    #:
    #:   * `True` prueba que existe una autorización EIP-3009 **firmada y
    #:     despachada** por el monto de `payment["amount_usd"]`. El settlement
    #:     PUDO haber ocurrido. Le toca reconciliar a quien llama.
    #:   * `True` **NO** prueba que el USDC se movió. Eso sólo se prueba con un
    #:     `payment["transaction_hash"]` presente, y ese hash sólo llega si el
    #:     servidor alcanzó a contestar con su cabecera `X-Payment-Receipt`.
    #:   * `False` sí es una afirmación fuerte en el otro sentido: no se firmó
    #:     nada, no salió ninguna credencial, no se gastó un centavo. Es el valor
    #:     de toda ruta gratis y del tramo previo al 402 de una ruta paga.
    payment_sent: bool = False

    #: Detalle de esa credencial cuando `payment_sent` es `True`; `None` si no.
    #: Claves: `amount_usd` (lo que se firmó, como STRING —es plata—),
    #: `network`, `resource` (la ruta) y `transaction_hash` (el
    #: `X-Payment-Receipt`, o `None` si el servidor no llegó a contestar).
    payment: Optional[Dict[str, Any]] = None


class DescribeTimeout(DescribeError):
    """La request superó el timeout del cliente.

    Incluye el arranque en frío del proveedor: su Lambda midió **15,2 s** de
    cold start (INC-2026-08-19, citado en `client.py:19-23` de EM). Un timeout
    corto convierte cada arranque en frío en un falso «no hay datos» — por eso
    el default de este SDK son 30 s (R7) y no 8 s, que ya rompió una
    integración real.
    """

    kind = "timeout"


class DescribeHTTPError(DescribeError):
    """describe contestó con un status no-2xx.

    El 422 (dirección inválida) y el 429 (rate limit compartido — el presupuesto
    vivo lo dice la cabecera `Ratelimit-Policy`, no un número copiado acá) caen
    junto con los 5xx: todo consumidor los trata igual —«no hay respuesta
    usable»— y lo que se lee para distinguirlos es `status_code`, no el bucket.
    """

    kind = "http_error"

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class DescribeUnreachable(DescribeError):
    """Falla de transporte antes de cualquier status HTTP (DNS, connect, reset)."""

    kind = "unreachable"


class DescribeUnparseable(DescribeError):
    """La respuesta llegó pero no se pudo leer con la forma prometida.

    Es protocolo, no dato: un `chains: []` es una forma válida que dice «no hay
    nada», y **no** pasa por acá. Sólo pasa un cuerpo que no es JSON o al que le
    falta la clave esencial que la ruta promete en su schema.
    """

    kind = "unparseable"


class DescribeMalformedHash(DescribeError):
    """Un campo de hash llegó con algo que **no tiene forma de hash**.

    Aporte de **KarmaKadabra** (`#agents`, 2026-08-30), del hallazgo que ellos
    llaman «el 200 sin tx»: *«si nosotros no chequeáramos el tx, habríamos
    contado 14 ratings que no existen»*. Un 200 que no hizo la cosa es peor que
    un 503 porque el cliente lo toma por bueno.

    🔴 **ESTA EXCEPCIÓN NUNCA SE LEVANTA. No escribas un `except` para ella.**
    Viaja únicamente como argumento de `on_error`, y existe como clase por una
    razón mecánica: el canal de observación está tipado
    `Callable[[DescribeError], None]`, así que para reutilizar el canal donde el
    consumidor YA está mirando —que es lo que pedía KK— el hecho tiene que SER
    un `DescribeError`. Se ramifica por `kind == "malformed_hash"` o por
    `isinstance`, jamás por un `try/except` que no se va a disparar nunca.

    ⚠️ Y hay una tensión con esta misma taxonomía que se declara en vez de
    taparse: la cabecera de este módulo explica que `PartialIndex` **no se
    portó** de Execution Market justamente porque «publicar una excepción que el
    SDK jamás levanta invita a un `except` muerto». El criterio que separa los
    dos casos no es «se levanta o no» sino **para qué existe la clase**:
    `PartialIndex` existía para que alguien la atrapara y nadie la iba a tirar;
    ésta existe para viajar por un canal ya tipado, y su docstring lo grita en la
    primera línea. Si algún día `on_error` acepta algo más que un `DescribeError`,
    esta clase deja de hacer falta.

    Por qué el fallo NO tumba la lectura: el resto de la respuesta puede ser
    perfectamente útil, y romper una descomposición de reputación entera por un
    campo accesorio sería peor que el bug que se está cazando. El campo tipado
    queda en `None` —para que nadie arme un link a un explorador con basura— y
    el valor crudo sobrevive en el `raw` del modelo, que es donde se investiga.

    🔴 **Ausente y malformado NO son lo mismo, y el modelo los distingue** (R1
    aplicada un nivel más abajo):

        rating.tx_hash is None y `malformed_hashes` vacío   → NO VINO
        rating.tx_hash is None y "tx_hash" en malformed     → VINO BASURA

    `fields` trae la ubicación de cada uno, con índice cuando está en una lista:
    `["ratings[3].tx_hash", "snapshot.inputs_digest"]`.
    """

    kind = "malformed_hash"

    def __init__(self, message: str, fields: Optional[List[str]] = None) -> None:
        super().__init__(message)
        #: Rutas de los campos que llegaron malformados, en orden de aparición.
        self.fields: List[str] = list(fields or [])


class PaymentRequiredError(DescribeError):
    """Una ruta medida contestó 402 y este cliente no tiene con qué pagar.

    **No la traga el fail-open.** Que falte un `payer` es configuración de quien
    llama, no una caída del índice: degradarla a `None` escondería un error de
    programación detrás del mismo valor que significa «no hay evidencia».

    `challenge` trae el 402 crudo tal cual llegó — `amount`, `token`,
    `recipient`, `accepts[]`, `free_preview`, `pricing`. Se guarda entero a
    propósito: la guía publicada manda tomar los valores del challenge y
    **nunca de una tabla cacheada** (docs.describe.net, «Paying with x402»,
    paso 1), así que el SDK no se queda con un resumen suyo.
    """

    kind = "payment_required"

    def __init__(
        self,
        message: str,
        challenge: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.challenge: Dict[str, Any] = challenge or {}

    @property
    def price_usd(self) -> Optional[str]:
        """El precio como STRING, tal cual lo mandó el servidor.

        String y no float a propósito: el precio es plata y un `float("0.01")`
        ya no es 0,01. Quien vaya a firmar lo pasa a `Decimal`, nunca a `float`.
        """
        value = self.challenge.get("price_usd") or self.challenge.get("amount")
        return str(value) if value is not None else None


class PartnerSigningError(DescribeError):
    """El riel de partner no pudo firmar. **No salió ninguna request.**

    Es configuración de quien llama —el extra sin instalar, un firmante que
    levanta, un KMS caído, una firma que no es hex— así que no la traga el
    fail-open, por lo mismo que `PaymentRequiredError`: degradarla a `None`
    convertiría «tu riel gratis está roto» en «esta wallet no tiene
    reputación».

    🔴 **`payment_sent` es `False` y eso es una afirmación fuerte, no un
    default**: la firma del riel ocurre ANTES de la primera request, así que
    cuando esto sale no se pidió nada, no se recibió ningún 402, no se firmó
    ninguna autorización EIP-3009 y no se movió un centavo. Es la mitad buena
    de «levanta, no degrada».

    `wallet` trae la dirección del firmante si se alcanzó a leer (`None` si el
    fallo fue justamente al pedirla). Es pública por diseño: es la que va en la
    allowlist del servicio.
    """

    kind = "partner_signing"

    def __init__(self, message: str, wallet: Optional[str] = None) -> None:
        super().__init__(message)
        self.wallet = wallet


class PartnerRejectedError(PaymentRequiredError):
    """Firmaste como partner y describe **igual pidió que pagues**. No se pagó.

    Hereda de `PaymentRequiredError` a propósito, y la herencia dice algo
    verdadero: los dos casos son «llegó un 402 y este cliente no puso un
    centavo». Un consumidor que ya escribía `except PaymentRequiredError` la
    atrapa sin cambiar una línea, y hereda `challenge` y `price_usd` — que acá
    valen doble, porque dicen **cuánto te iba a costar** el riel roto.

    🔴 POR QUÉ ESTO LEVANTA EN VEZ DE PAGAR, que es la decisión entera del
    modo partner:

        Un partner con `payer=` configurado y el riel caído tiene un camino
        obvio y silencioso: pagar. Y ahí el bug no se ve nunca — la respuesta
        llega igual, el código funciona, y la factura de USDC aparece semanas
        después. Es la misma familia del gate del servicio: *un bug acá no
        rompe nada, no tira error, y regala/gasta el producto.*

    Así que un 402 con `partner=` configurado es un FALLO y no una señal de
    cobro. **El `payer` no se usa aunque esté**, y el mensaje lo dice a gritos.
    Quien de verdad quiera pagar construye un cliente sin `partner=`: es una
    línea, es explícita, y queda escrita en su código.

    Las cuatro causas, todas fail-closed del lado del servicio:
      * la wallet no está en la allowlist (el alta la hace describe);
      * se firmó contra otro host (tu `base_url` no es `api.describe.net`);
      * el reloj del firmante se corrió más de 300 s (la ventana del gate);
      * la firma no cubría la URL que salió (query incluida).

    `wallet` es la dirección con la que se firmó: es lo primero que hay que
    mirar y lo que hay que citarle a describe para el alta.
    """

    kind = "partner_rejected"

    def __init__(
        self,
        message: str,
        wallet: str,
        challenge: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, challenge)
        self.wallet = wallet


class DoNotPayError(DescribeError):
    """El 402 pide que se pague a una dirección que NO es la tesorería pinneada.

    **Es `DO_NOT_PAY`, no un retry, y no la traga el fail-open.** Regla 4 de
    `F0-describe-sdk.md:192-205` y paso 2 de la guía publicada: *«If the
    challenge names another address, do not pay: either it did not come from
    describe, or the treasury changed and the server did not find out.»*

    Reintentar acá es lo peor que puede hacer un cliente: convierte un desvío
    de fondos en un desvío de fondos con reintentos.
    """

    kind = "do_not_pay"

    def __init__(self, message: str, expected: str, offered: str) -> None:
        super().__init__(message)
        self.expected = expected
        self.offered = offered


def mark_payment_sent(
    exc: DescribeError,
    *,
    amount_usd: Optional[str],
    network: str,
    resource: str,
    transaction_hash: Optional[str] = None,
) -> None:
    """Marcar una excepción como posterior a la firma. La llama sólo el cliente.

    Muta el objeto en vez de envolverlo en una excepción nueva a propósito: un
    `except DescribeTimeout` ya escrito en el código de un consumidor tiene que
    seguir atrapándola. Cambiar la CLASE para agregar un dato es romper el
    `except` de todo el mundo por una etiqueta.

    Y el aviso se escribe **también en el mensaje**, no sólo en el atributo,
    porque quien abre un traceback en un log a las 3 AM no tiene el objeto a
    mano — tiene una línea de texto. El atributo es para ramificar; el texto es
    para leer. (Es el mismo par que `caveats[].code` / `caveats[].text` del
    servicio: se decide por el código, se lee el texto.)
    """
    exc.payment_sent = True
    exc.payment = {
        "amount_usd": amount_usd,
        "network": network,
        "resource": resource,
        "transaction_hash": transaction_hash,
    }
    if transaction_hash:
        prueba = (
            f" HAY RECIBO: X-Payment-Receipt={transaction_hash} — el settlement "
            "ocurrió, el gasto está confirmado y es citable."
        )
    else:
        prueba = (
            " NO llegó `X-Payment-Receipt`, así que no hay prueba en ninguno de "
            "los dos sentidos: este SDK no puede afirmar que se liquidó ni que no."
        )
    base = str(exc.args[0]) if exc.args else str(exc)
    exc.args = (
        f"{base} — 🔴 LA CREDENCIAL DE PAGO YA SE FIRMÓ Y SE DESPACHÓ "
        f"({amount_usd or '?'} USD en {network}, {resource}): esto NO es «no pude "
        f"preguntar», es «puede que haya pagado y no sé qué recibí».{prueba} "
        "Reconciliá antes de reintentar: el nonce se consume en el settlement, "
        "así que reenviar esta credencial no vuelve a pagar y pedir un challenge "
        "NUEVO cobra otra vez.",
    )
