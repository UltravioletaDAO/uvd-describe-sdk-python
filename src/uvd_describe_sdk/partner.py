"""El riel de PARTNER — entrar a las rutas medidas sin gastar un centavo.

════════════════════════════════════════════════════════════════════════════
QUÉ ES, Y POR QUÉ NO ES UN TOKEN
════════════════════════════════════════════════════════════════════════════
describe no tiene cuentas ni API keys —«el pago es la autenticación»— y sin
embargo los servicios de la casa (Execution Market, KarmaKadabra, MeshRelay)
delegaron su lectura de reputación acá y consultan cientos de veces por día.
El servicio resolvió eso el 2026-08-28 con un mecanismo que **no le exige a
describe custodiar ningún secreto ajeno**: una allowlist de DIRECCIONES
PÚBLICAS, y cada partner firma sus requests con una wallet dedicada.

    `describe-net/describenet/partner.py` — el gate, del otro lado.

Es mejor que un token por una propiedad que ningún token tiene: la allowlist
se puede commitear, loguear y publicar en un `get-function-configuration` sin
filtrar nada, porque **una brecha de describe no compromete a ningún partner**.
No hay allá una credencial tuya que robar. Sólo tu dirección, que es pública.

Este módulo es la mitad cliente de ese mecanismo: firma la request con el
primitivo del `uvd-x402-sdk` y devuelve los dos headers de RFC 9421.

════════════════════════════════════════════════════════════════════════════
🔴 ACÁ NO HAY, NI HABRÁ, UNA CLAVE PRIVADA
════════════════════════════════════════════════════════════════════════════
**Este módulo no lee una env var, no acepta una clave por parámetro, no la
guarda y no la ve.** Recibe un OBJETO FIRMANTE inyectado (`PartnerSigner`) y le
pide dos cosas: `get_address()` y `sign_message()`. La clave se queda del lado
del firmante — un adaptador de env var, un KMS, un signer remoto, una Ledger.

    🔴 NUNCA escribas una private key en un archivo, ni «temporalmente», ni
    «para probar». Hay bots barriendo GitHub por `0x`+64 hex que drenan en
    minutos: la casa ya perdió DOS wallets así (INC-2026-03-30).

Y usá una wallet **dedicada y sin fondos**: lo único que hace es firmar. Esa es
la propiedad que hace barato el peor caso — una firma filtrada sirve contra el
MISMO método y la MISMA URL, y sólo por 300 segundos (`MAX_VALIDITY_SEC` del
gate). No es una credencial permanente y no puede mover plata.

    # La clave sale del entorno del CONSUMIDOR, jamás de este SDK.
    from uvd_x402_sdk.wallet import EnvKeyAdapter   # lee WALLET_PRIVATE_KEY
    from uvd_describe_sdk import DescribeClient

    with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as d:
        b = d.wallet_breakdown("0x97cd…0996")   # $0,01 para un tercero, $0 acá

════════════════════════════════════════════════════════════════════════════
UPSTREAM-FIRST: EL PRIMITIVO YA ESTABA, Y SE MIDIÓ ANTES DE ESCRIBIR ESTO
════════════════════════════════════════════════════════════════════════════
`uvd-x402-sdk` **0.70.0 ya firma ERC-8128** — `sign_request` de
`uvd_x402_sdk.erc8128`, con los vectores dorados de la flota de EM pinneados en
el paquete. Así que acá NO se implementa RFC 9421, no se arma una base de firma
y no se toca EIP-191: se delega, igual que `payment.py` delega EIP-3009.

Medido el 2026-08-30 contra el gate REAL del servicio (importando
`describenet.partner` y su misma `VerifyPolicy`, con una clave efímera de
memoria):

    verify_request ok = True · wallet recuperada == la que firmó
    PartnerGate.check con wallet LISTADA     -> 'mi-partner'
    PartnerGate.check con wallet NO listada  -> None   (paga; el discriminante)
    firmada contra `localhost:8088`          -> None   (authority equivocada)
    firmada SIN query, pedida CON query      -> None   ← ver abajo

Y los defaults del SDK de pagos **ya coinciden** con lo que el gate pide, lo
cual no es suerte sino el mismo ecosistema: `DEFAULT_CHAIN_ID = 8453` (Base),
`DEFAULT_VALIDITY_SEC = 300`, `alg="eip191"`, keyid en minúsculas. El
`chain_id` se pasa igual EXPLÍCITO (ver `PARTNER_CHAIN_ID`).

════════════════════════════════════════════════════════════════════════════
🔴 SE FIRMA LO QUE SE MANDA, BYTE A BYTE — INCLUIDA LA QUERY
════════════════════════════════════════════════════════════════════════════
La base de firma cubre `@method`, `@authority`, `@path` y —**sólo cuando la
URL tiene query**— `@query`. La quinta línea de la medición de arriba es el
modo de falla: una firma hecha sobre la URL sin `?snapshot=true` y mandada con
`?snapshot=true` **no verifica**, y el partner cae al 402 sin entender por qué.

Por eso el cliente firma la URL que `httpx` ya construyó (`build_request`) y no
una que rearme a mano: la que se firma y la que sale son la misma cadena por
construcción, no por cuidado. `test_partner_riel_gratis.py` lo ata comparando
las líneas `"@path"` / `"@query"` de la base firmada contra la URL que el
transporte vio salir.

Y el `authority` sale de la URL, o sea de tu `base_url`. Consecuencia: **si
apuntás el cliente a cualquier host que no sea `api.describe.net`, el riel no
funciona** — el gate reconstruye la base contra su authority pinneada y a
propósito nunca contra el Host del request (derivar la authority de un header
que controla el cliente es cómo se rompió un verificador ajeno). No es un bug
de este SDK: es fail-closed, y se entera ruidosamente por
`PartnerRejectedError`.

════════════════════════════════════════════════════════════════════════════
DEFAULT-COBRAR ES DEL SERVIDOR, Y ESO ES LO QUE LO HACE UNA GARANTÍA
════════════════════════════════════════════════════════════════════════════
Nada de lo que pase en este archivo puede eximir a nadie. La allowlist vive en
el servicio; env ausente, vacía o con JSON inválido ⇒ allowlist VACÍA ⇒ 402
para todos. Este módulo sólo produce dos headers; quien decide es el otro lado.
Dicho al revés: **no hay ninguna forma de que un bug de este SDK regale el
producto**, y sí hay una de que te haga gastar USDC en silencio. Contra esa
está `PartnerRejectedError` (ver `client.py::_paid`).
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

try:  # pragma: no cover - depende de la versión de Python
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable

from .errors import PartnerSigningError

#: La cadena del keyid ERC-8128 (`erc8128:<chain>:0x…`). **8453, Base**, y el
#: número no se elige acá: lo pinnea el gate del servicio
#: (`describenet/partner.py::CHAIN_ID`), que fija UNA a propósito porque
#: «cualquier cadena» sería innecesariamente laxo para una allowlist de tres
#: servicios propios. Se pasa EXPLÍCITO a `sign_request` aunque el default del
#: SDK de pagos hoy coincida (medido 2026-08-30: `DEFAULT_CHAIN_ID = 8453`):
#: heredar un default ajeno para un valor que el servidor compara es firmar
#: contra lo que otro repo decida mañana.
PARTNER_CHAIN_ID = 8453

#: El dominio contra el que el gate reconstruye la base. NO se usa para firmar
#: —el `authority` sale de la URL que se manda, ver la cabecera— y se publica
#: sólo para que un error pueda decir contra qué authority firmaste y contra
#: cuál te iban a verificar. Espejo de `describenet/partner.py::AUTHORITY`.
PARTNER_AUTHORITY = "api.describe.net"


@runtime_checkable
class PartnerSigner(Protocol):
    """Lo único que este SDK le pide a quien firma. **La clave nunca sale de acá.**

    Es, a propósito, el mismo par de métodos que el `WalletAdapter` del
    `uvd-x402-sdk`: cualquier adaptador suyo lo satisface **estructuralmente**,
    sin heredar nada nuestro, y un firmante propio (KMS, HSM, signer remoto)
    entra con dos métodos y sin importar una línea de este paquete.

        class MiFirmanteRemoto:
            def get_address(self) -> str: ...        # la dirección PÚBLICA
            def sign_message(self, message: str) -> str: ...   # EIP-191, hex

    `sign_message` recibe la base de firma de RFC 9421 (texto plano) y devuelve
    la firma `personal_sign` en hex. Que la interfaz sea de dos métodos es lo
    que hace que los tests de este repo corran **sin una sola clave y sin
    criptografía**: el doble devuelve hex fijo.
    """

    def get_address(self) -> str:
        """La dirección EVM del firmante. Es PÚBLICA: va a la allowlist."""
        ...

    def sign_message(self, message: str) -> str:
        """Firma EIP-191 (`personal_sign`) del mensaje, en hex."""
        ...


class PartnerSignature:
    """Los headers firmados **y** la dirección con la que se firmó.

    La dirección viaja de vuelta por una razón operativa, no decorativa: cuando
    el riel se rompe, el 99 % de las veces es «esa wallet no está en la
    allowlist», y el remedio exige saber CUÁL wallet firmó. Sacarla de acá y no
    de un segundo `get_address()` importa cuando el firmante es remoto: ese
    segundo llamado puede fallar justo cuando estás armando el mensaje de error.
    """

    __slots__ = ("headers", "wallet")

    def __init__(self, headers: Dict[str, str], wallet: str) -> None:
        self.headers = headers
        self.wallet = wallet


def sign_partner_headers(
    signer: PartnerSigner,
    *,
    method: str,
    url: str,
    chain_id: int = PARTNER_CHAIN_ID,
    now: Optional[Callable[[], int]] = None,
) -> PartnerSignature:
    """Firmar una request para el riel de partner. Delega TODO en el SDK de pagos.

    Args:
        signer: el objeto firmante inyectado. **Este SDK jamás construye uno**,
            no lo lee de una env var y no toca su clave.
        method: el método HTTP, tal cual va a salir.
        url: la URL COMPLETA que se va a mandar, con su query si la tiene. Ver
            la cabecera: firmar otra cosa produce un 402 que no se entiende.
        now: reloj inyectable (epoch en segundos), para tests.

    Raises:
        `PartnerSigningError` ante cualquier fallo — el extra sin instalar, un
        firmante que levanta, un KMS caído, una firma que no es hex. **Nunca se
        devuelve un diccionario a medias**: seguir sin firma es exactamente el
        camino que termina gastando USDC en silencio.
    """
    try:
        # El import va acá adentro y no arriba, igual que en el gate del
        # servicio: este módulo tiene que poder importarse —y su Protocol
        # usarse para tipar un firmante— sin el SDK de pagos presente. El
        # camino gratis no arrastra nada que firme.
        from uvd_x402_sdk.erc8128 import sign_request
    except ImportError as exc:
        # El mensaje nombra la causa REAL. Es la lección que `payment.py` ya
        # se comió con `chain_name_for`: un error que manda al lugar
        # equivocado es peor que ninguno, porque el que lo lee ejecuta la
        # receta, sigue en rojo y no busca más.
        raise PartnerSigningError(
            "el riel de partner necesita `uvd-x402-sdk` (es quien sabe firmar "
            "ERC-8128) y no está instalado: `pip install uvd-describe-sdk[partner]`. "
            "NO se pidió nada y NO se gastó nada.",
            wallet=None,
        ) from exc

    # Se declara afuera del `try` para que el error pueda nombrar la wallet
    # cuando el fallo fue al FIRMAR y no al pedir la dirección: «tu KMS de
    # 0x… no contestó» es accionable, «el firmante falló» no.
    wallet: Optional[str] = None
    try:
        wallet = signer.get_address()
        headers = sign_request(
            signer,
            method=method,
            url=url,
            chain_id=chain_id,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001
        # Se traga TODA excepción del firmante a propósito y se re-levanta como
        # una nuestra: quien llama no debería tener que importar el SDK de
        # pagos —ni el cliente de su KMS— para escribir su `except`. Lo que NO
        # se hace nunca es seguir sin firma.
        raise PartnerSigningError(
            f"el firmante del riel de partner falló ({type(exc).__name__}: {exc}). "
            "NO se pidió nada y NO se gastó nada: arreglá el firmante, o construí "
            "el cliente SIN `partner=` si de verdad querés pagar.",
            wallet=wallet,
        ) from exc

    return PartnerSignature(headers=dict(headers), wallet=str(wallet))
