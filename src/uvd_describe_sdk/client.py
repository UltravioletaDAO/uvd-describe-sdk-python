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
QUÉ MÉTODO ES «NULLABLE» — se sigue el contrato núcleo AL PIE DE LA LETRA
════════════════════════════════════════════════════════════════════════════
La tabla del contrato v0.1 marca `wallet(address) -> WalletReputation | null` y
**sólo esa**: `leaderboard() -> LeaderboardRow[]`, `health() -> IndexHealth`,
`walletBreakdown -> Breakdown`, `agent -> AgentReputation`, sin `| null`.

Así está implementado, y la razón por la que se lee coherente: `wallet()` es la
que se dibuja al lado de un nombre en un perfil —el caso de Saul, KK y EM— y
ahí un hueco es un render degradado aceptable. `leaderboard()` y `health()` son
lecturas operativas: quien pregunta por el índice entero o por su estado quiere
saber que falló, no recibir una lista vacía que parece un índice vacío. Y una
ruta paga con un fallo tragado sería una credencial gastada sin recibo.

⚠️ **Esto está REPORTADO como ambigüedad del contrato, no resuelto por acá:**
R5 dice «ante fallo de red devuelve `null`» sin acotar a qué método, y la tabla
de tipos sí acota. Se implementó la tabla —es la afirmación más específica— y
la pregunta va a Saul. Si el veredicto cambia, cambia en los tres frentes a la
vez; no acá solo.

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
from typing import Any, Callable, Dict, List, Optional
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
            fail_open: default `True`. Ver la cabecera del módulo — y leé qué
                método es nullable antes de asumir que aplica a todos.
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
            if isinstance(exc, DescribeHTTPError) and exc.status_code == 404:
                self._observe(exc, path)
                return None
            if not self._fail_open:
                raise
            self._observe(exc, path)
            return None

    def leaderboard(self) -> List[LeaderboardRow]:
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

        No es nullable (tabla de tipos del contrato): un fallo levanta. Ver la
        cabecera del módulo.
        """
        payload = self._get_json("/leaderboard")
        return parse_leaderboard(payload)

    def health(self) -> IndexHealth:
        """`GET /health` — **GRATIS**. La autoridad sobre totales y políticas.

        Ninguna cifra del índice se tipea a mano: se lee de acá, viva. Y de acá
        salen también los parámetros calibrables (`reading_policy`,
        `confidence_thresholds`) que el servicio publica **justamente** para que
        ningún consumidor los copie.

        Es el endpoint gratis más lento (~1,6 s medidos por EM) y está sin
        cachear a propósito — nunca lo sondees con un timeout de pocos segundos.

        No es nullable: un fallo levanta. Preguntar por el estado del índice y
        recibir un objeto vacío sería el peor de los dos mundos.
        """
        return parse_health(self._get_json("/health"))

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

    def _get_paid(
        self, path: str, *, params: Optional[Dict[str, Any]] = None
    ) -> httpx.Response:
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
        """
        response = self._request(path, params=params)
        if response.status_code != 402:
            if response.status_code >= 400:
                raise DescribeHTTPError(
                    f"GET {path} → HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            return response

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
        paid = self._request(path, params=params, headers={"X-PAYMENT": header})
        if paid.status_code >= 400:
            raise DescribeHTTPError(
                f"GET {path} → HTTP {paid.status_code} DESPUÉS de pagar. "
                "La credencial ya se consumió: pedí un challenge nuevo, no "
                "reenvíes esta.",
                status_code=paid.status_code,
            )
        return paid

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

        No es nullable ni la traga el fail-open: hay plata de por medio y un
        fallo tragado sería una credencial gastada sin recibo.

        Raises:
            `PaymentRequiredError` si no hay `payer` (trae el challenge entero,
            con su `price_usd` y su `free_preview`).
            `DoNotPayError` si el 402 pide pagar a otra dirección.
        """
        path = f"/reputation/wallet/{quote(str(address), safe='')}"
        params = {"snapshot": "true"} if snapshot else None
        response = self._get_paid(path, params=params)
        return parse_breakdown(self._json(response, path), self._receipt(response))

    def agent(self, network: str, agent_id: str) -> AgentReputation:
        """`GET /reputation/agent/{network}/{agent_id}` — **$0,02**.

        `network` tiene que ser uno de los `chains[].network` de `health()`; un
        id sólo es único **por cadena**.

        `agent_id` es un **string, no un número**: los registros EVM acuñan
        enteros pero en Solana el id es la dirección del asset Metaplex Core en
        base58 — case-sensitive, y pasarlo por `int()` lo destruye.
        """
        path = (
            f"/reputation/agent/{quote(str(network), safe='')}"
            f"/{quote(str(agent_id), safe='')}"
        )
        response = self._get_paid(path)
        return parse_agent_reputation(self._json(response, path), self._receipt(response))
