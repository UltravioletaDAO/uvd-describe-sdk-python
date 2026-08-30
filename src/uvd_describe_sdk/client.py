"""`DescribeClient` — el cliente sincrónico. Acá viven R4, R5 y R7.

Sincrónico a propósito: los tres consumidores medidos del ecosistema que leen
esta API lo son o lo pueden ser sin dolor, y una API async duplicada es la vía
más corta a que las dos ramas diverjan. (La única excepción real está
declarada como riesgo en el README: el cliente de Execution Market es `async` y
para adoptarlo tendría que envolver esto en un thread. Es una pregunta abierta,
no un descuido.)

════════════════════════════════════════════════════════════════════════════
🔴 R5 — EL FAIL-OPEN, Y POR QUÉ ES LO MÁS FÁCIL DE HACER MAL
════════════════════════════════════════════════════════════════════════════
Saul lo pidió textual el 2026-08-28: *«pon un fallback si es que describe está
caído»*. El default es `fail_open=True`.

Pero un fail-open ingenuo **rompe R1**. Mirá la trampa de frente:

    wallet() devuelve None  ──> ¿«describe está caído»?
                           └──> ¿o «esta wallet no tiene reputación»?

Si las dos cosas devolvieran lo mismo y nada más, el fail-open habría fabricado
exactamente la confusión que R1 existe para impedir — y no es hipotética: le
costó un reporte equivocado a KarmaKadabra el 2026-08-28, en el gate que decide
con quién se comercia.

**Cómo se resuelve acá, con dos mecanismos y no con un comentario:**

1. **La distinción vive en el TIPO, no en el valor.** Una wallet que el índice
   sí pudo leer vuelve como un `WalletReputation` — aunque no tenga ni una
   calificación, aunque no esté registrada. `None` significa **una sola cosa**:
   *no hubo respuesta*. Nunca *no hay reputación*.

       resultado is None                      → no se pudo leer
       resultado.has_identity is False        → no registrada
       resultado.global_score is None         → registrada, sin calificar

2. **Ningún `None` sale sin ser observado.** Todo camino que devuelve `None`
   pasa antes por `_observe()`, que llama a `on_error` y loguea en WARNING. Un
   fail-open silencioso convierte «describe está caído» en «esta wallet no
   tiene reputación» **en los logs**, que es donde se investiga. Por eso el
   default no es «no hacer nada»: es loguear.
   `tests/test_r5_fail_open.py` lo ata, y su test discriminante monta el estado
   BUENO —una wallet real sin calificaciones— para exigir objeto y CERO
   observaciones: sin él, «sin ratings ⇒ devolvé None» pasaría en verde.

**Lo que el fail-open NO tapa nunca:** `PaymentRequiredError` y `DoNotPayError`.
El fail-open es para la DISPONIBILIDAD del índice, no para la configuración de
quien llama ni para un desvío de fondos.

════════════════════════════════════════════════════════════════════════════
QUÉ MÉTODO ES «NULLABLE» — LA LÍNEA ES SI HUBO DINERO DE POR MEDIO
════════════════════════════════════════════════════════════════════════════
**R5 corregida, 2026-08-30. Es canon del contrato núcleo y los dos SDK
—Python y TypeScript— la implementan IGUAL.**

    RUTAS GRATIS      wallet() · leaderboard() · health()
                      Ante un fallo DE SERVICIO (timeout, unreachable, 5xx,
                      cuerpo ilegible) con `fail_open=True` devuelven `None`,
                      SIEMPRE observado (`on_error` + WARNING).
                      🔴 Nunca `[]`. Una lista vacía se lee como «el índice
                      está vacío», que es una afirmación FALSA sobre el mundo.
                      `None` se lee como «no pude preguntar».

    RUTAS PAGAS       wallet_breakdown() · agent()
                      LEVANTAN SIEMPRE. Incluso con `fail_open=True` explícito.

**Por qué las pagas no, y la razón es dinero y no simetría:** entre firmar el
sobre x402 y recibir la respuesta hay una ventana en la que el USDC ya se
movió. Devolver `None` ahí le oculta al llamador que gastó — es una credencial
gastada sin recibo, y nada distingue «pagué y se cayó» de «no había nada que
traer». Un fallo ruidoso después de pagar es recuperable (se reintenta, se
registra, se reclama); un `None` silencioso no lo es. Por eso no es una
preferencia del llamador sino una **propiedad del método**: un flag de
disponibilidad no puede comprar el derecho a tragar un recibo. Y para que
«ruidoso» sea además informativo, la excepción del tramo pagado sale marcada
con `payment_sent=True` (ver `errors.py`).

**Por qué las gratis sí, y no sólo `wallet()`:** `leaderboard()` y `health()`
son gratis. Un fallo ruidoso ahí obliga a cada consumidor a escribir su propio
`try/except` para algo que el SDK ya sabe hacer — que es justo la duplicación
que este SDK viene a borrar.

────────────────────────────────────────────────────────────────────────────
LO QUE ESTE BLOQUE DECÍA HASTA EL 2026-08-30, Y QUÉ SOBREVIVIÓ DE ESO
────────────────────────────────────────────────────────────────────────────
Se deja escrito, no se borra: es la convención de la casa, y acá se aplica al
propio razonamiento de este archivo.

La versión vieja decía que la tabla de tipos del contrato v0.1 marcaba `| null`
**sólo** en `wallet()`, que se seguía «al pie de la letra», y lo defendía así:
`wallet()` se dibuja al lado de un nombre en un perfil y ahí un hueco es un
render degradado aceptable, mientras que *«`leaderboard()` y `health()` son
lecturas operativas: quien pregunta por el índice entero o por su estado quiere
saber que falló, no recibir una lista vacía que parece un índice vacío»*. Y
marcaba el alcance de R5 como una ambigüedad REPORTADA y no resuelta acá.

**Qué sobrevivió, y no es poco:** la segunda mitad de esa frase. «Una lista
vacía parece un índice vacío» era correcto, se atendió, y por eso el contrato
corregido dice explícitamente **NUNCA `[]`**. `None` no es una lista vacía y no
se puede confundir con un índice vacío: la distinción sigue viviendo en el TIPO,
igual que en `wallet()`.

**Qué se cayó:** la primera mitad — «lecturas operativas» no era el criterio.
Nombraba una intuición sobre quién pregunta, no una consecuencia medible de
equivocarse. El criterio real es **si hubo dinero de por medio**, y eso también
desmiente la premisa: seguir «la tabla al pie de la letra» dejaba a
`walletBreakdown` y `agent` sin `| null` por accidente de tabla y no por
principio — el gemelo TypeScript leyó la otra mitad del contrato (la regla R5,
que no acotaba) y terminó haciendo **fail-open en las rutas pagas**, con
`walletBreakdown()` devolviendo `null` tras un timeout posterior al settlement.
Ese es el bug que esta corrección existe para hacer imposible en los dos
lenguajes: dos lecturas razonables del mismo contrato, y una costaba plata.

**Y la ambigüedad ya no está abierta:** el alcance de R5 quedó resuelto el
2026-08-30 con la regla de arriba. No queda una pregunta para Saul acá.

════════════════════════════════════════════════════════════════════════════
R7 — 30 s de timeout, y el número está razonado (no elegido)
════════════════════════════════════════════════════════════════════════════
Es el único de los tres consumidores que llegó a su timeout con una medición
detrás (`execution-market/.../client.py:19-23`, INC-2026-08-19):

  * el cold start de la Lambda del proveedor midió **15,2 s**;
  * su API Gateway corta a **29 s** — pedir más sería pedirle al aire;
  * y 30 es **deliberadamente distinto** de los 45 s del facilitator, «so the
    two clocks never race»: dos timeouts iguales expiran el mismo segundo y no
    hay forma de saber cuál falló.

KarmaKadabra usa 25 s con la misma medición del cold start y dejó escrito que
su default viejo de 12 s «convertía cada arranque en frío en un ilegible».
30 gana porque cubre el cold start con margen y no empata con nada.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, TypeVar
from urllib.parse import quote

import httpx

from .badge import badge_url as _badge_url
from .errors import (
    DescribeError,
    DescribeHTTPError,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
    PaymentRequiredError,
    mark_payment_sent,
)
from .models import (
    AgentReputation,
    Breakdown,
    IndexHealth,
    LeaderboardRow,
    PaymentReceipt,
    WalletReputation,
    parse_agent_reputation,
    parse_breakdown,
    parse_health,
    parse_leaderboard,
    parse_wallet_reputation,
)
from .payment import TREASURY_EVM, Payer, build_payment_header
from .version import default_user_agent

DEFAULT_BASE_URL = "https://api.describe.net"

#: R7. Ver la cabecera del módulo: 15,2 s de cold start medido, 29 s de techo
#: del API Gateway, y distinto de los 45 s del facilitator a propósito.
DEFAULT_TIMEOUT_S = 30.0

#: La red por la que se paga si el llamador no dice otra. `base` es la primera
#: de `supportedChains` en el challenge vivo (2026-08-30) y la más barata de las
#: seis. No hay forma de adivinar dónde tiene fondos quien llama: se elige un
#: default explícito y se documenta, en vez de probar las seis por turno —
#: probar gastaría credenciales.
DEFAULT_PAY_NETWORK = "base"

logger = logging.getLogger("uvd_describe_sdk")

#: Se llama con la excepción que se está tragando. Ver `_observe`.
ErrorObserver = Callable[[DescribeError], None]

#: El modelo que devuelve una ruta paga. Existe para que `_paid` sea una sola
#: función y no dos copias: donde vive la marca de «esto falló DESPUÉS de
#: pagar» no puede haber dos versiones que se desincronicen.
_Parsed = TypeVar("_Parsed")


class DescribeClient:
    """Cliente sincrónico de describe. Config por constructor, cero globals.

    Todo se pasa por constructor y nada se lee de una env var adentro: es lo que
    hace que el módulo sea testeable sin entorno y embebible en cualquier
    proceso. (La referencia de EM lo llama su «SDK-extractability contract» y
    por eso su cliente se pudo levantar tal cual a este repo.)

        with DescribeClient(product="karmakadabra") as describe:
            rep = describe.wallet("0x97cd…0996")
            if rep is None:
                ...  # el índice no contestó — NO es «no tiene reputación»
            elif not rep.has_identity:
                ...  # no está registrada
            elif rep.global_score is None:
                ...  # registrada y todavía sin calificar
            else:
                print(format_score(rep.global_score), rep.policy_version)
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        product: Optional[str] = None,
        user_agent: Optional[str] = None,
        fail_open: bool = True,
        on_error: Optional[ErrorObserver] = None,
        payer: Optional[Payer] = None,
        pay_network: str = DEFAULT_PAY_NETWORK,
        treasury: str = TREASURY_EVM,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """
        Args:
            product: quién consume (`"karmakadabra"`, `"meshrelay"`…). Va al
                User-Agent. Pasalo: el rate limit son 20 rps COMPARTIDOS sin
                bucket por partner, y sin atribución nadie puede saber quién lo
                gastó. Un request anónimo contra un límite compartido es
                free-riding.
            fail_open: default `True`. Cubre las rutas **GRATIS** —`wallet()`,
                `leaderboard()`, `health()`— que ante un fallo de servicio
                devuelven `None` (nunca `[]`) y siempre observado. **No cubre
                las pagas**: `wallet_breakdown()` y `agent()` levantan aunque
                acá se pase `True`, porque un fallo tragado después de firmar es
                una credencial gastada sin recibo. Ver la cabecera del módulo.
            on_error: se llama con la excepción **cada vez** que el fail-open
                traga una. Si no se pasa, igual se loguea en WARNING. No existe
                el modo silencioso.
            payer: quien firma el 402. Sólo hace falta para las rutas medidas.
                Ver `payment.Payer`.
            treasury: la dirección a la que se acepta pagar. Configurable para
                un despliegue propio del índice, **no** para desactivar el
                chequeo: si el challenge nombra otra, es `DO_NOT_PAY`.
            transport: para los tests (`httpx.MockTransport`). Es el seam que
                permite que la suite entera corra **sin red**.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._user_agent = user_agent or default_user_agent(product)
        self._fail_open = fail_open
        self._on_error = on_error
        self._payer = payer
        self._pay_network = pay_network
        self._treasury = treasury
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": self._user_agent, "Accept": "application/json"},
            transport=transport,
        )

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DescribeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def user_agent(self) -> str:
        """El UA efectivo. Se publica para poder afirmarlo en un test en vez de
        confiar en que se armó bien."""
        return self._user_agent

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------

    def _observe(self, exc: DescribeError, context: str) -> None:
        """Todo `None` que devuelve este cliente pasa por acá primero.

        Es el mecanismo que hace que el fail-open sea **observable**. Si el
        observador de quien llama explota, se loguea y se sigue: un callback
        roto no puede convertir una degradación prevista en un crash.
        """
        logger.warning(
            "describe no contestó (%s en %s): %s — se devuelve None, "
            "que NO significa «sin reputación»",
            exc.kind,
            context,
            exc,
        )
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001
                logger.exception("el on_error de quien llama levantó una excepción")

    def _request(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """GET con la taxonomía tipada. **Nunca deja escapar una `httpx.*`.**

        Que ninguna excepción de la librería cruce el borde del módulo es
        deliberado: quien llama no debería tener que importar `httpx` para
        escribir su `except`, ni verse obligado a cambiarlo el día que este SDK
        cambie de cliente HTTP.
        """
        url = f"{self._base_url}{path}"
        try:
            return self._http.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            # 🔴 Tiene que ir ANTES de TransportError: `TimeoutException` es
            # subclase suya, y al revés todo timeout se reportaría como
            # «unreachable» — dos causas distintas con la misma etiqueta.
            raise DescribeTimeout(f"GET {path} superó los {self._timeout}s") from exc
        except httpx.TransportError as exc:
            raise DescribeUnreachable(f"GET {path} inalcanzable: {exc}") from exc

    @staticmethod
    def _json(response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise DescribeUnparseable(f"GET {path} no devolvió JSON") from exc

    def _get_json(
        self, path: str, *, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Una ruta GRATIS. 4xx/5xx → `DescribeHTTPError`. R4."""
        response = self._request(path, params=params)
        if response.status_code >= 400:
            raise DescribeHTTPError(
                f"GET {path} → HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return self._json(response, path)

    # ------------------------------------------------------------------
    # Rutas GRATIS
    # ------------------------------------------------------------------

    def wallet(self, address: str) -> Optional[WalletReputation]:
        """`GET /wallets/{wallet}/chains` — **GRATIS**. La puerta.

        Es el primer movimiento del camino feliz y no es una preferencia de
        diseño: el propio 402 de la ruta paga lo dice en su `free_preview` —
        *«Si no hay reputación ahí, este cobro no devuelve nada.»*

        La dirección viaja **verbatim**, sin `lower()`: una EVM la normaliza el
        servidor, pero un id Solana es base58 case-SENSITIVE y bajarlo a
        minúsculas nombra otra clave, en silencio y con un 200.

        Returns:
            `WalletReputation` — el índice contestó. Puede no tener identidades
            (`has_identity is False`) o tenerlas sin calificar
            (`global_score is None`); las dos son RESPUESTAS.

            `None` — **no hubo respuesta**: transporte, HTTP no-2xx, o un 404.
            Nunca significa «no tiene reputación». Sale sólo con
            `fail_open=True` y siempre con su observación (`on_error` + WARNING).

        Raises:
            `DescribeError` si `fail_open=False`.
        """
        path = f"/wallets/{quote(str(address), safe='')}/chains"
        try:
            return parse_wallet_reputation(self._get_json(path))
        except DescribeError as exc:
            # R4: un 404 NO es una excepción del dominio — es «no tengo eso».
            # Se degrada a `None` incluso con fail_open=False, porque negarse a
            # contestar sobre un sujeto ausente sería tratar la ausencia como
            # una falla.
            # 🔴 Y esta excepción es SÓLO de acá: `leaderboard()` y `health()` no
            # la copian porque no tienen sujeto. Un 404 ahí no dice «no tengo esa
            # wallet» sino «esa ruta no existe» — o sea, un `base_url` mal
            # puesto. Degradarlo en silencio con fail_open=False escondería un
            # error de configuración detrás del valor que significa «no pude
            # leer», que es la confusión que R1 persigue en otro plano.
            if isinstance(exc, DescribeHTTPError) and exc.status_code == 404:
                self._observe(exc, path)
                return None
            if not self._fail_open:
                raise
            self._observe(exc, path)
            return None

    def leaderboard(self) -> Optional[List[LeaderboardRow]]:
        """`GET /leaderboard` — **GRATIS**, la primera página entera.

        🔴 **No acepta ni un query param.** Medido el 2026-08-30:
        `GET /leaderboard?limit=3` → **HTTP 422**, cuerpo
        `{"error": "leaderboard_takes_no_params", "params": ["limit"],
        "paged_route": "GET /leaderboard/page"}`. La paginación es otra ruta y
        es paga ($0,01). Por eso este método no tiene argumentos: una firma con
        `limit=` invitaría a un 422 garantizado.

        ⚠️ Y el orden **no es por promedio, es por la media bayesiana**
        (`shrunk_score`). Reordenar por `final_score` da otra lista, y parece un
        bug del servicio.

        Returns:
            `list[LeaderboardRow]` — el índice contestó. Puede venir vacía si el
            índice de verdad no tiene filas: eso es una RESPUESTA.

            `None` — **no hubo respuesta**. 🔴 Nunca `[]` por un fallo: una lista
            vacía afirmaría que el índice está vacío, que es una afirmación falsa
            sobre el mundo. Sale sólo con `fail_open=True` y siempre con su
            observación (`on_error` + WARNING).

        Raises:
            `DescribeError` si `fail_open=False`.
        """
        try:
            return parse_leaderboard(self._get_json("/leaderboard"))
        except DescribeError as exc:
            if not self._fail_open:
                raise
            self._observe(exc, "/leaderboard")
            return None

    def health(self) -> Optional[IndexHealth]:
        """`GET /health` — **GRATIS**. La autoridad sobre totales y políticas.

        Ninguna cifra del índice se tipea a mano: se lee de acá, viva. Y de acá
        salen también los parámetros calibrables (`reading_policy`,
        `confidence_thresholds`) que el servicio publica **justamente** para que
        ningún consumidor los copie.

        Es el endpoint gratis más lento (~1,6 s medidos por EM) y está sin
        cachear a propósito — nunca lo sondees con un timeout de pocos segundos.

        Returns:
            `IndexHealth` — el índice contestó, incluso si contestó
            `status != "ok"`: un índice que se declara degradado está
            RESPONDIENDO, y esa es justo la respuesta que se le fue a pedir.

            `None` — **no hubo respuesta**. Nunca un objeto vacío: preguntar por
            el estado del índice y recibir ceros sería el peor de los dos mundos,
            porque «0 agentes» y «no sé cuántos agentes» no son el mismo hecho.
            Sale sólo con `fail_open=True` y siempre observado.

        Raises:
            `DescribeError` si `fail_open=False`.
        """
        try:
            return parse_health(self._get_json("/health"))
        except DescribeError as exc:
            if not self._fail_open:
                raise
            self._observe(exc, "/health")
            return None

    def badge_url(self, address: str) -> str:
        """La URL del badge SVG. **Sin red** — sólo arma el string.

        Está acá para que quede a mano en el mismo objeto, pero no toca el
        cliente HTTP: se puede llamar sin conexión, en un render, en un loop.
        """
        return _badge_url(address, base_url=self._base_url)

    # ------------------------------------------------------------------
    # Rutas MEDIDAS (x402)
    # ------------------------------------------------------------------

    @staticmethod
    def _receipt(response: httpx.Response) -> PaymentReceipt:
        """Las cabeceras de liquidación, que hasta hoy ningún cliente leía.

        `X-Payment-Receipt` es el hash de la transacción de settlement (público,
        sirve para conciliar) y `X-Payment-Reused: true` dice que se reusó un
        recibo en vez de cobrar de nuevo. Verificado en el servicio:
        `paywall.py:1059-1062` los escribe, `api.py:2226` los expone por CORS.
        """
        return PaymentReceipt(
            transaction_hash=response.headers.get("X-Payment-Receipt"),
            reused=str(response.headers.get("X-Payment-Reused", "")).lower() == "true",
            pricing_version=response.headers.get("X-Pricing-Version"),
        )

    def _paid(
        self,
        path: str,
        parse: Callable[[Any, PaymentReceipt], _Parsed],
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> _Parsed:
        """El baile del 402, en el orden que manda la guía publicada.

        1. Pedir **sin header** — preguntar es gratis y es el primer movimiento
           previsto por el servicio.
        2. Si vuelve 402: leer el challenge, **verificar el destinatario** contra
           la tesorería pinneada, firmar con el payer.
        3. Repetir **la misma request** con `X-PAYMENT`.

        No hay reintento después del segundo intento, y es deliberado: el nonce
        se consume en el settlement, así que reenviar la misma credencial no
        vuelve a pagar — un 4xx después de pagar es casi siempre una credencial
        gastada o firmada por otro monto, y el remedio es pedir un challenge
        NUEVO, no repetir el viejo. Un `retries=3` acá quemaría credenciales.

        🔴 **El parseo entra acá, no en el método público.** Es lo que hace que
        exista UNA sola frontera «desde acá hay plata de por medio» en todo el
        archivo. Si el parseo viviera afuera, un `DescribeUnparseable` sobre el
        cuerpo YA PAGADO saldría sin `payment_sent` — indistinguible de un
        cuerpo roto que no costó nada. La marca no puede depender de que quien
        agregue la tercera ruta paga se acuerde de ponerla.

        🔴 **Nada de esto lo traga el fail-open, valga lo que valga.** Ver la
        cabecera del módulo: la línea es si hubo dinero de por medio.
        """
        # ── Tramo PRE-PAGO: no se firmó nada, no se gastó nada ───────────────
        response = self._request(path, params=params)
        if response.status_code != 402:
            if response.status_code >= 400:
                raise DescribeHTTPError(
                    f"GET {path} → HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            # Un 200 sin pagar: el servicio decidió no cobrar (o hay un caché
            # delante). No hubo credencial, así que no hay nada que marcar.
            return parse(self._json(response, path), self._receipt(response))

        try:
            challenge = response.json()
        except ValueError as exc:
            raise DescribeUnparseable(f"el 402 de {path} no es JSON") from exc

        if self._payer is None:
            raise PaymentRequiredError(
                f"GET {path} es una ruta medida y este cliente no tiene `payer`. "
                "Construilo con `DescribeClient(payer=...)` — o usá la puerta "
                "gratis, que para una wallet es `wallet()`.",
                challenge=challenge,
            )

        header = build_payment_header(
            self._payer,
            challenge,
            network=self._pay_network,
            treasury=self._treasury,
        )

        # ══ FRONTERA. Arriba no se gastó un centavo; abajo la autorización ══
        # EIP-3009 ya está FIRMADA. Todo lo que falle de acá para abajo sale
        # marcado con `payment_sent=True` — un `DescribeTimeout` pelado no
        # distingue «se cayó antes de pagar» de «se cayó después», y esa
        # distinción es la que decide si al llamador le toca reconciliar.
        #
        # El monto se lee del challenge con la MISMA expresión que
        # `payment._amount_usd`, y se lee DESPUÉS de que `build_payment_header`
        # volvió: si `price_usd` no cerraba con `accepts[].amount`, esa función
        # ya habría levantado `DoNotPayError` sin firmar. O sea que acá el valor
        # es el que se firmó, no una aproximación. String y no float: es plata.
        firmado = challenge.get("price_usd", challenge.get("amount"))
        monto = str(firmado) if firmado is not None else None
        recibo: Optional[str] = None
        try:
            paid = self._request(path, params=params, headers={"X-PAYMENT": header})
            recibo = paid.headers.get("X-Payment-Receipt")
            if paid.status_code >= 400:
                raise DescribeHTTPError(
                    f"GET {path} → HTTP {paid.status_code} DESPUÉS de pagar. "
                    "La credencial ya se consumió: pedí un challenge nuevo, no "
                    "reenvíes esta.",
                    status_code=paid.status_code,
                )
            return parse(self._json(paid, path), self._receipt(paid))
        except DescribeError as exc:
            mark_payment_sent(
                exc,
                amount_usd=monto,
                network=self._pay_network,
                resource=path,
                transaction_hash=recibo,
            )
            raise

    def wallet_breakdown(self, address: str, *, snapshot: bool = False) -> Breakdown:
        """`GET /reputation/wallet/{wallet}` — **$0,01** ($0,05 con `snapshot`).

        La descomposición: quiénes calificaron, cuántas veces, con qué fecha y
        en qué transacción. El número global ya está **gratis** en `wallet()`;
        lo que se cobra es el desglose.

        Args:
            snapshot: persiste la respuesta y devuelve la fila. Es la **única
                ruta que escribe** y cuesta más. Lo que se compra no es el
                número sino el compromiso con él: un recibo durable con
                `inputs_digest` que se puede citar después.

        🔴 **No es nullable y NO la traga el fail-open — ni con
        `fail_open=True` explícito.** No es una preferencia del llamador sino
        una propiedad del método: entre firmar el sobre y recibir la respuesta
        el USDC ya se movió, y un `None` ahí es una credencial gastada sin
        recibo. Un flag de disponibilidad no puede comprar el derecho a tragar
        un recibo. (R5 corregida, 2026-08-30 — ver la cabecera del módulo.)

        Raises:
            `PaymentRequiredError` si no hay `payer` (trae el challenge entero,
            con su `price_usd` y su `free_preview`).
            `DoNotPayError` si el 402 pide pagar a otra dirección.
            `DescribeError` ante cualquier fallo de servicio. Si cayó DESPUÉS de
            firmar, sale con `exc.payment_sent is True` y `exc.payment` con el
            monto, la red y el `X-Payment-Receipt` si llegó a haber uno.
        """
        path = f"/reputation/wallet/{quote(str(address), safe='')}"
        params = {"snapshot": "true"} if snapshot else None
        return self._paid(path, parse_breakdown, params=params)

    def agent(self, network: str, agent_id: str) -> AgentReputation:
        """`GET /reputation/agent/{network}/{agent_id}` — **$0,02**.

        `network` tiene que ser uno de los `chains[].network` de `health()`; un
        id sólo es único **por cadena**.

        `agent_id` es un **string, no un número**: los registros EVM acuñan
        enteros pero en Solana el id es la dirección del asset Metaplex Core en
        base58 — case-sensitive, y pasarlo por `int()` lo destruye.

        🔴 **No es nullable y NO la traga el fail-open**, por lo mismo que
        `wallet_breakdown()`: hay dinero de por medio. Ver la cabecera del
        módulo y, para el fallo posterior a la firma, `exc.payment_sent`.
        """
        path = (
            f"/reputation/agent/{quote(str(network), safe='')}"
            f"/{quote(str(agent_id), safe='')}"
        )
        return self._paid(path, parse_agent_reputation)
