"""Las formas tipadas y sus parsers — donde viven R1, R2 y la tolerancia aditiva.

Sin `httpx`, sin reloj, sin SQL: stdlib puro. Un parser es una función de
`dict` a dataclass congelado y se puede testear sin red.

════════════════════════════════════════════════════════════════════════════
R1 — `null` NUNCA `0`. La invariante que este archivo hace imposible de romper.
════════════════════════════════════════════════════════════════════════════
`_opt_float` devuelve `None` cuando el valor es `None`. **Jamás sustituye 0.0.**
Es la invariante 7 del servicio («Sin ratings el campo es `null`, nunca `0`.
*Sin datos* no es *malo*») bajada a una función de tres líneas.

Y hay TRES hechos distintos que el tipo no puede confundir, todos legítimos y
ninguno un error:

    no registrada        → WalletReputation(identity_count=0, chains=[],
                                            global_score=None)
    registrada sin calificar → WalletReputation(identity_count=1, chains=[…],
                                            global_score=None)
    no se pudo leer      → el método devuelve None (ver `client.py`, R5)

Los dos primeros son OBJETOS. La distinción entre ellos NO está en si el score
es `None` —en los dos lo es— sino en `identity_count` / `chains_with_identity`.
Quien quiera decidir mira ahí. Un `0` en `global_score` afirmaría «lo
calificaron pésimo» sobre alguien a quien nadie calificó nunca; medido en
producción: un prior de 50 pintaba badge *silver* a ejecutores sin historia, y
la conclusión escrita fue «el 50 es peor que un hueco».

════════════════════════════════════════════════════════════════════════════
R2 — ningún resultado es un número pelado
════════════════════════════════════════════════════════════════════════════
Todo dataclass de resultado lleva `policy_version`, `caveats` y la fuente
(`source` / `refreshed_at` / `snapshot`). No hay un `get_score() -> float` en
este SDK y no lo va a haber: la tesis del producto es que **«un score sin sus
calificadores es un rumor»**, y un método que devuelve un `float` la borra.
Quien quiera el número solo lo saca del objeto a mano, y **eso es a propósito**:
el gesto de sacarlo es el que deja escrito en el código de quien llama que
decidió tirar el contexto. `tests/test_r2_r3_contrato.py` lo ata por
introspección — y ese test está **verificado por mutación** (2026-08-30): se
inyectó un `get_score(x: float) -> float` exportado y se puso rojo nombrándolo
(`assert not ['get_score() -> float']`). Un test de contrato que nunca se vio
rojo no prueba que el contrato exista.

════════════════════════════════════════════════════════════════════════════
TOLERANCIA ADITIVA — se tipa lo conocido y se CONSERVA lo desconocido
════════════════════════════════════════════════════════════════════════════
Cada dataclass guarda `raw`: el payload entero tal cual llegó. Un campo que el
servicio agregue mañana **sigue llegando** a quien lo necesite aunque este SDK
no lo conozca.

El precedente es del propio servicio y es exactamente este bug del otro lado
del cable: `Facet.direction` viajaba en la respuesta y FastAPI lo descartaba en
silencio por no estar declarado — HTTP 200, forma correcta, dato ausente
(regla 5 de `F0-describe-sdk.md:206-211`). Un SDK que tipe estricto y tire el
resto reproduce ese bug del lado del cliente, y encima con un `200` verde.

Medido hoy mismo: `WalletChains.distinct_raters` viaja vivo en
`api.describe.net` (2026-08-30) y **no existe** en `types.py` de EM, que es la
implementación de referencia. Sin `raw`, un consumidor que migrara de EM a este
SDK perdería un campo que la API ya sirve.

════════════════════════════════════════════════════════════════════════════
LO QUE SÍ ES UNA EXCEPCIÓN
════════════════════════════════════════════════════════════════════════════
Un parser levanta `DescribeUnparseable` sólo si falta la clave ESENCIAL que la
ruta promete en su schema (`wallet`, `matches`, `status`…). Un `chains: []` es
una forma válida que dice «no hay nada» y no pasa por ahí — R4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import DescribeUnparseable

# ---------------------------------------------------------------------------
# Primitivas de parseo
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> Optional[float]:
    """`None` se queda `None` — **NUNCA** se convierte en `0.0`. R1, invariante 7."""
    if value is None:
        return None
    return float(value)


def _opt_int(value: Any) -> Optional[int]:
    """`None` se queda `None`. Distinto de `_int0`: acá el hueco es un hecho.

    Caso real: `distinct_raters` viene `None` en las filas de agente de
    `/search` porque ahí no se computa. Renderizarlo como `0` afirmaría «N
    reviews de cero calificadores», que es aritméticamente imposible y sin
    embargo se imprimiría sin que nada falle.
    """
    if value is None:
        return None
    return int(value)


def _int0(value: Any) -> int:
    """Contadores que el schema declara requeridos y no-nulos: ausente ⇒ 0.

    Acá el `0` SÍ es correcto y hay que distinguirlo de `_opt_int`: «cuántas
    cadenas tienen identidad» con la respuesta vacía es cero cadenas, un hecho
    contable. Nunca se usa para un SCORE.
    """
    return int(value or 0)


def _require(payload: Any, key: str, what: str) -> Dict[str, Any]:
    if not isinstance(payload, dict) or key not in payload:
        raise DescribeUnparseable(
            f"la respuesta de {what} no trae la forma esencial (falta `{key}`)"
        )
    return payload


# ---------------------------------------------------------------------------
# Caveats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Caveat:
    """Una trampa que ESTOS números dispararon, con su código estable.

    🔴 `code` ES EL CONTRATO; `text` NO. Se ramifica por `code` (ver
    `uvd_describe_sdk.caveats`), nunca por `text`, que es prosa en español y
    puede reescribirse, re-medirse o traducirse sin aviso — lo declara el propio
    schema del servicio.

    `code` se tipa `str` y no un `Enum` a propósito: un código nuevo del
    servicio tiene que llegar entero, no romper ni desaparecer.
    """

    code: str
    text: str = ""


def parse_caveats(raw: Any) -> List[Caveat]:
    """`[{code, text}]` → `[Caveat]`. Una entrada sin `code` se descarta.

    Se descarta y no explota: un caveat malformado es advisory perdido, no una
    razón para tumbar una lectura de reputación que por lo demás llegó bien.
    """
    out: List[Caveat] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("code"):
            out.append(Caveat(code=str(item["code"]), text=str(item.get("text") or "")))
        elif isinstance(item, str) and item:
            # Antes del 2026-08-28 los caveats eran strings pelados. Un índice
            # viejo o un mock que copie el formato antiguo sigue siendo legible.
            out.append(Caveat(code=item, text=""))
    return out


# ---------------------------------------------------------------------------
# GRATIS — GET /wallets/{wallet}/chains
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainReputation:
    """Una fila por cadena de `GET /wallets/{w}/chains`."""

    network: str
    agent_count: int = 0
    agent_ids: Optional[List[str]] = None
    #: `None`, nunca `0` — R1.
    final_score: Optional[float] = None
    total_reviews: int = 0
    distinct_raters: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WalletReputation:
    """`GET /wallets/{w}/chains` — GRATIS, cacheada en el borde (~60 s de TTL).

    Es **la puerta**: dice si esta wallet tiene algo antes de pagar por la
    descomposición. El propio 402 lo declara en `free_preview`: *«Si no hay
    reputación ahí, este cobro no devuelve nada.»*

    `global_score` es el promedio de los promedios por cadena (una cadena, un
    voto), servido desde `chain_rankings_mv`. Puede diferir en decimales del
    lookup pago, y por eso viajan `source` + `refreshed_at` diciéndolo.

    ⚠️ `caveats` acá es un **SUBSET** de los de la ruta paga — hoy sólo
    `burn-address`. Una lista vacía en esta puerta **no promete** que la
    descomposición paga esté limpia (`caveats.FREE_GATE_CAVEAT_CODES`).
    """

    wallet: str
    chains: List[ChainReputation] = field(default_factory=list)
    caveats: List[Caveat] = field(default_factory=list)
    identity_count: int = 0
    chains_with_identity: int = 0
    chains_with_reputation: int = 0
    total_reviews: int = 0
    distinct_raters: Optional[int] = None
    #: `None`, nunca `0` — R1.
    global_score: Optional[float] = None
    policy_version: Optional[str] = None
    source: Optional[str] = None
    #: ISO tal cual lo sirve el índice. `None` = «no registrado», jamás «recién».
    refreshed_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_identity(self) -> bool:
        """¿El índice conoce alguna identidad ERC-8004 de esta wallet?

        Es la mitad de la distinción de R1 que `global_score is None` no puede
        dar sola: `False` = no registrada; `True` con `global_score is None` =
        registrada y todavía sin calificar. Dos hechos, los dos respuestas.
        """
        return self.identity_count > 0

    @property
    def caveat_codes(self) -> List[str]:
        """Sólo los `code`. Atajo para ramificar sin tocar `text`."""
        return [c.code for c in self.caveats]


def parse_wallet_reputation(payload: Any) -> WalletReputation:
    body = _require(payload, "wallet", "GET /wallets/{wallet}/chains")
    try:
        chains = [
            ChainReputation(
                network=str(row["network"]),
                agent_count=_int0(row.get("agent_count")),
                agent_ids=row.get("agent_ids"),
                final_score=_opt_float(row.get("final_score")),
                total_reviews=_int0(row.get("total_reviews")),
                distinct_raters=_int0(row.get("distinct_raters")),
                raw=dict(row),
            )
            for row in (body.get("chains") or [])
            if isinstance(row, dict) and "network" in row
        ]
        return WalletReputation(
            wallet=str(body["wallet"]),
            chains=chains,
            caveats=parse_caveats(body.get("caveats")),
            identity_count=_int0(body.get("identity_count")),
            chains_with_identity=_int0(body.get("chains_with_identity")),
            chains_with_reputation=_int0(body.get("chains_with_reputation")),
            total_reviews=_int0(body.get("total_reviews")),
            distinct_raters=_opt_int(body.get("distinct_raters")),
            global_score=_opt_float(body.get("global_score")),
            policy_version=body.get("policy_version"),
            source=body.get("source"),
            refreshed_at=body.get("refreshed_at"),
            raw=dict(body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(
            f"GET /wallets/{{wallet}}/chains no pasó el parse tipado: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Piezas compartidas por las rutas pagas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceInterval:
    lower: float
    upper: float


@dataclass(frozen=True)
class Confidence:
    """La banda de confianza y su política, **versionada aparte del score**.

    `confidence_policy` no es `policy_version`: no mueve un solo score. El
    servicio las separa a propósito —fusionarlas marcaría cada rating como
    recomputado cada vez que cambia una lista— y `GET /health` es la autoridad
    sobre cuántas políticas hay.
    """

    band: Optional[str] = None
    distinct_raters: int = 0
    interval: Optional[ConfidenceInterval] = None
    thresholds: Dict[str, Any] = field(default_factory=dict)
    advice: Optional[str] = None
    confidence_policy: Optional[str] = None


@dataclass(frozen=True)
class Concentration:
    """Cuán concentrada está la reputación en un solo calificador.

    `top_client_share is None` **no** es «no está concentrado»: es «la señal
    está caída» — y cuando pasa, el servicio dispara el caveat
    `concentration-degraded` justamente para que no se lea como un limpio.
    """

    distinct_raters: int = 0
    top_client_share: Optional[float] = None
    top_client: Optional[str] = None


@dataclass(frozen=True)
class SelfRated:
    """Cuánto se calificó a sí mismo el sujeto. El gap se publica, no se juzga."""

    count: int = 0
    score: Optional[float] = None
    gap: Optional[float] = None


@dataclass(frozen=True)
class Activity:
    first_rating_at: Optional[str] = None
    last_rating_at: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    """El snapshot citable ($0,05 en vez de $0,01): la evidencia con su digest.

    Es lo que convierte una lectura en algo que se puede citar después:
    `inputs_digest` + `policy_version` + `computed_at` dicen sobre qué entradas
    y con qué política se computó ESTE número.
    """

    id: Optional[int] = None
    inputs_digest: Optional[str] = None
    policy_version: Optional[str] = None
    computed_at: Optional[str] = None


@dataclass(frozen=True)
class ChainScore:
    """El score de una cadena dentro de la descomposición paga."""

    score: Optional[float] = None
    review_count: int = 0
    agent_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Facet:
    """Una faceta (`tag1` on-chain) con su score y su dirección declarada.

    🔴 `direction` / `direction_category` / `direction_meaning` son ADVISORY:
    es lo que el EMISOR declara, no algo que el índice verifique. Y `tag1` es
    **texto libre on-chain** — la faceta más larga del índice mide 471
    caracteres (un párrafo sobre jardinería usado como etiqueta). Escapá todo
    lo que venga de la cadena antes de renderizarlo.
    """

    score: Optional[float] = None
    count: int = 0
    distinct_raters: int = 0
    revoked_count: int = 0
    out_of_domain_count: int = 0
    self_rated_count: int = 0
    direction: Optional[str] = None
    direction_category: Optional[str] = None
    direction_meaning: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ownership:
    """Qué parte de la reputación de un agente es HEREDADA de un dueño anterior.

    Un agente ERC-8004 se puede transferir. Sin este bloque, las reseñas del
    dueño viejo se leen como historial del nuevo.
    """

    owner_updated_block: Optional[int] = None
    identity_transferred: Optional[bool] = None
    inherited_review_count: int = 0
    inherited_score: Optional[float] = None
    current_era_review_count: int = 0
    current_era_score: Optional[float] = None
    undetermined_review_count: int = 0
    inherited_share: Optional[float] = None


@dataclass(frozen=True)
class Rating:
    """Una calificación individual: **el grano**, y la razón de que esto se cobre.

    El servicio no vende un número, vende la descomposición: quién calificó
    (`client`), cuántas veces, en qué transacción (`tx_hash`) y quién escribió
    la entrada (`issuer_host`). Es la respuesta física a *«un score sin sus
    calificadores es un rumor»*.
    """

    client: str
    feedback_index: int
    value: int
    value_decimals: int
    normalized_value: Optional[float] = None
    tag1: Optional[str] = None
    tag2: Optional[str] = None
    is_revoked: bool = False
    is_self: bool = False
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    log_index: Optional[int] = None
    feedback_uri: Optional[str] = None
    issuer_host: Optional[str] = None
    issuer: Optional[str] = None
    issuer_org: Optional[str] = None
    feedback_hash: Optional[str] = None
    revoked_tx: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentReceipt:
    """Las cabeceras de liquidación que el paywall emite y **nadie leía**.

    Contrato registrado en `F0-describe-sdk.md:192-205`: el servicio devuelve
    `X-Payment-Receipt` (el hash de la transacción de settlement, público,
    sirve para conciliar) y `X-Payment-Reused` (`true` = se reusó un recibo, no
    se cobró de nuevo) en cada 200 pago — y hasta hoy **ningún cliente los
    leía**. Verificado en el servicio: `paywall.py:1059-1062` los escribe y
    `api.py:2226` los pone en `expose_headers` de CORS.

    Exponerlos es la diferencia entre «pagué» y «puedo probar que pagué».
    """

    transaction_hash: Optional[str] = None
    reused: bool = False
    pricing_version: Optional[str] = None


def _parse_confidence(raw: Any) -> Optional[Confidence]:
    if not isinstance(raw, dict):
        return None
    interval = raw.get("interval")
    return Confidence(
        band=raw.get("band"),
        distinct_raters=_int0(raw.get("distinct_raters")),
        interval=(
            ConfidenceInterval(
                lower=float(interval["lower"]), upper=float(interval["upper"])
            )
            if isinstance(interval, dict)
            and interval.get("lower") is not None
            and interval.get("upper") is not None
            else None
        ),
        thresholds=dict(raw.get("thresholds") or {}),
        advice=raw.get("advice"),
        confidence_policy=raw.get("confidence_policy"),
    )


def _parse_concentration(raw: Any) -> Optional[Concentration]:
    if not isinstance(raw, dict):
        return None
    return Concentration(
        distinct_raters=_int0(raw.get("distinct_raters")),
        top_client_share=_opt_float(raw.get("top_client_share")),
        top_client=raw.get("top_client"),
    )


def _parse_self_rated(raw: Any) -> SelfRated:
    if not isinstance(raw, dict):
        return SelfRated()
    return SelfRated(
        count=_int0(raw.get("count")),
        score=_opt_float(raw.get("score")),
        gap=_opt_float(raw.get("gap")),
    )


def _parse_facets(raw: Any) -> Dict[str, Facet]:
    out: Dict[str, Facet] = {}
    for name, row in (raw or {}).items():
        if not isinstance(row, dict):
            continue
        out[str(name)] = Facet(
            score=_opt_float(row.get("score")),
            count=_int0(row.get("count")),
            distinct_raters=_int0(row.get("distinct_raters")),
            revoked_count=_int0(row.get("revoked_count")),
            out_of_domain_count=_int0(row.get("out_of_domain_count")),
            self_rated_count=_int0(row.get("self_rated_count")),
            direction=row.get("direction"),
            direction_category=row.get("direction_category"),
            direction_meaning=row.get("direction_meaning"),
            raw=dict(row),
        )
    return out


# ---------------------------------------------------------------------------
# PAGA — GET /reputation/wallet/{wallet}   ($0,01; $0,05 con snapshot citable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Breakdown:
    """`GET /reputation/wallet/{w}` — la descomposición de una wallet.

    Seis sentencias sobre el grano —por cadena, autocalificación, facetas,
    actividad, ponderado (dos) y concentración— resueltas en decenas de
    milisegundos. Lo que se cobra es eso, no el número: el número global está
    gratis en `WalletReputation`.

    `final_score` vs `weighted_score`: el primero es equal-weight; el segundo
    aplica `rater_weight_policy`. **Los dos pueden ser `None`** y ninguno es 0.
    """

    wallet: str
    #: `None`, nunca `0` — R1.
    final_score: Optional[float] = None
    #: `None`, nunca `0` — R1.
    weighted_score: Optional[float] = None
    rater_weight_policy: Optional[str] = None
    chain_count: int = 0
    total_reviews: int = 0
    per_chain: Dict[str, ChainScore] = field(default_factory=dict)
    facets: Dict[str, Facet] = field(default_factory=dict)
    self_rated: SelfRated = field(default_factory=SelfRated)
    concentration: Optional[Concentration] = None
    confidence: Optional[Confidence] = None
    activity: Activity = field(default_factory=Activity)
    caveats: List[Caveat] = field(default_factory=list)
    policy_version: Optional[str] = None
    snapshot: Optional[Snapshot] = None
    receipt: Optional[PaymentReceipt] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def caveat_codes(self) -> List[str]:
        return [c.code for c in self.caveats]


def parse_breakdown(payload: Any, receipt: Optional[PaymentReceipt] = None) -> Breakdown:
    body = _require(payload, "wallet", "GET /reputation/wallet/{wallet}")
    try:
        per_chain = {
            str(net): ChainScore(
                score=_opt_float(row.get("score")),
                review_count=_int0(row.get("review_count")),
                agent_ids=list(row.get("agent_ids") or []),
            )
            for net, row in (body.get("per_chain") or {}).items()
            if isinstance(row, dict)
        }
        activity_raw = body.get("activity") or {}
        snapshot_raw = body.get("snapshot")
        return Breakdown(
            wallet=str(body["wallet"]),
            final_score=_opt_float(body.get("final_score")),
            weighted_score=_opt_float(body.get("weighted_score")),
            rater_weight_policy=body.get("rater_weight_policy"),
            chain_count=_int0(body.get("chain_count")),
            total_reviews=_int0(body.get("total_reviews")),
            per_chain=per_chain,
            facets=_parse_facets(body.get("facets")),
            self_rated=_parse_self_rated(body.get("self_rated")),
            concentration=_parse_concentration(body.get("concentration")),
            confidence=_parse_confidence(body.get("confidence")),
            activity=Activity(
                first_rating_at=activity_raw.get("first_rating_at"),
                last_rating_at=activity_raw.get("last_rating_at"),
            ),
            caveats=parse_caveats(body.get("caveats")),
            policy_version=body.get("policy_version"),
            snapshot=(
                Snapshot(
                    id=_opt_int(snapshot_raw.get("id")),
                    inputs_digest=snapshot_raw.get("inputs_digest"),
                    policy_version=snapshot_raw.get("policy_version"),
                    computed_at=snapshot_raw.get("computed_at"),
                )
                if isinstance(snapshot_raw, dict)
                else None
            ),
            receipt=receipt,
            raw=dict(body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(
            f"GET /reputation/wallet/{{wallet}} no pasó el parse tipado: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# PAGA — GET /reputation/agent/{network}/{agent_id}   ($0,02)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentReputation:
    """`GET /reputation/agent/{n}/{id}` — un agente con sus calificaciones.

    ⚠️ `declared_type` **no es un tipo verificado**. Medido el 2026-08-30:
    283.770 de 470.064 agentes (60,4 %) son `unknown`, y el segundo «tipo» más
    común es la URL del schema del EIP — con su variante con typo. ERC-8004 no
    tiene campo de tipo. Nunca se usa como verificación.
    """

    network: str
    agent_id: str
    current_owner: Optional[str] = None
    declared_type: Optional[str] = None
    agent_uri: Optional[str] = None
    indexed_identity: bool = False
    #: `None`, nunca `0` — R1.
    score: Optional[float] = None
    review_count: int = 0
    revoked_count: int = 0
    out_of_domain_count: int = 0
    self_rated: SelfRated = field(default_factory=SelfRated)
    facets: Dict[str, Facet] = field(default_factory=dict)
    concentration: Optional[Concentration] = None
    ownership: Optional[Ownership] = None
    confidence: Optional[Confidence] = None
    caveats: List[Caveat] = field(default_factory=list)
    ratings: List[Rating] = field(default_factory=list)
    policy_version: Optional[str] = None
    receipt: Optional[PaymentReceipt] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def caveat_codes(self) -> List[str]:
        return [c.code for c in self.caveats]


def parse_agent_reputation(
    payload: Any, receipt: Optional[PaymentReceipt] = None
) -> AgentReputation:
    body = _require(payload, "agent_id", "GET /reputation/agent/{network}/{agent_id}")
    try:
        ownership_raw = body.get("ownership")
        ratings = [
            Rating(
                client=str(row.get("client") or ""),
                feedback_index=_int0(row.get("feedback_index")),
                value=_int0(row.get("value")),
                value_decimals=_int0(row.get("value_decimals")),
                normalized_value=_opt_float(row.get("normalized_value")),
                tag1=row.get("tag1"),
                tag2=row.get("tag2"),
                is_revoked=bool(row.get("is_revoked")),
                is_self=bool(row.get("is_self")),
                tx_hash=row.get("tx_hash"),
                block_number=_opt_int(row.get("block_number")),
                log_index=_opt_int(row.get("log_index")),
                feedback_uri=row.get("feedback_uri"),
                issuer_host=row.get("issuer_host"),
                issuer=row.get("issuer"),
                issuer_org=row.get("issuer_org"),
                feedback_hash=row.get("feedback_hash"),
                revoked_tx=row.get("revoked_tx"),
                raw=dict(row),
            )
            for row in (body.get("ratings") or [])
            if isinstance(row, dict)
        ]
        return AgentReputation(
            network=str(body.get("network") or ""),
            agent_id=str(body["agent_id"]),
            current_owner=body.get("current_owner"),
            declared_type=body.get("declared_type"),
            agent_uri=body.get("agent_uri"),
            indexed_identity=bool(body.get("indexed_identity")),
            score=_opt_float(body.get("score")),
            review_count=_int0(body.get("review_count")),
            revoked_count=_int0(body.get("revoked_count")),
            out_of_domain_count=_int0(body.get("out_of_domain_count")),
            self_rated=_parse_self_rated(body.get("self_rated")),
            facets=_parse_facets(body.get("facets")),
            concentration=_parse_concentration(body.get("concentration")),
            ownership=(
                Ownership(
                    owner_updated_block=_opt_int(ownership_raw.get("owner_updated_block")),
                    identity_transferred=ownership_raw.get("identity_transferred"),
                    inherited_review_count=_int0(ownership_raw.get("inherited_review_count")),
                    inherited_score=_opt_float(ownership_raw.get("inherited_score")),
                    current_era_review_count=_int0(
                        ownership_raw.get("current_era_review_count")
                    ),
                    current_era_score=_opt_float(ownership_raw.get("current_era_score")),
                    undetermined_review_count=_int0(
                        ownership_raw.get("undetermined_review_count")
                    ),
                    inherited_share=_opt_float(ownership_raw.get("inherited_share")),
                )
                if isinstance(ownership_raw, dict)
                else None
            ),
            confidence=_parse_confidence(body.get("confidence")),
            caveats=parse_caveats(body.get("caveats")),
            ratings=ratings,
            policy_version=body.get("policy_version"),
            receipt=receipt,
            raw=dict(body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(
            f"GET /reputation/agent no pasó el parse tipado: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# GRATIS — GET /leaderboard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaderboardRow:
    """Una fila del ranking.

    🔴 **El leaderboard NO ordena por promedio: ordena por la media bayesiana.**
    `shrunk_score` y `distinct_raters` viajan en la respuesta justamente para
    que el orden se pueda recomputar a mano. Ordenar por `final_score` da otra
    lista y parece un bug del servicio.
    """

    rank: int
    wallet: str
    #: `None`, nunca `0` — R1.
    final_score: Optional[float] = None
    #: El que MANDA en el orden. También `None`-able.
    shrunk_score: Optional[float] = None
    distinct_raters: int = 0
    chain_count: int = 0
    total_reviews: int = 0
    networks: List[str] = field(default_factory=list)
    declared_types: List[Optional[str]] = field(default_factory=list)
    #: Viaja en CADA fila (R2): un ranking sin su política es un rumor ordenado.
    policy_version: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_leaderboard(
    payload: Any, policy_version: Optional[str] = None
) -> List[LeaderboardRow]:
    """`GET /leaderboard` → filas.

    ⚠️ La ruta devuelve un **array JSON pelado**, no un objeto, así que no trae
    `policy_version` adentro. R2 exige que todo resultado lleve su política, así
    que el cliente la inyecta desde `X-Policy-Version` si el servicio la manda o
    desde el `policy_version` que ya conoce. Medido el 2026-08-30:
    `GET /leaderboard?limit=3` → **HTTP 422 `leaderboard_takes_no_params`**; la
    primera página es gratis y entera, la paginación es `/leaderboard/page`
    ($0,01).
    """
    if not isinstance(payload, list):
        raise DescribeUnparseable(
            "GET /leaderboard no devolvió un array (¿mandaste query params? "
            "la ruta gratis no acepta ninguno y contesta 422)"
        )
    try:
        return [
            LeaderboardRow(
                rank=_int0(row.get("rank")),
                wallet=str(row["wallet"]),
                final_score=_opt_float(row.get("final_score")),
                shrunk_score=_opt_float(row.get("shrunk_score")),
                distinct_raters=_int0(row.get("distinct_raters")),
                chain_count=_int0(row.get("chain_count")),
                total_reviews=_int0(row.get("total_reviews")),
                networks=list(row.get("networks") or []),
                declared_types=list(row.get("declared_types") or []),
                policy_version=row.get("policy_version") or policy_version,
                raw=dict(row),
            )
            for row in payload
            if isinstance(row, dict) and "wallet" in row
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(
            f"GET /leaderboard no pasó el parse tipado: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# GRATIS — GET /health
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainHealth:
    """El estado de indexación de UNA cadena.

    `next_sync_at` es el puntero de frescura: hasta ahí, lo que hay es lo que
    hay. Una cadena que no está en la lista **no está indexada** — índice
    parcial es dato parcial, no un error.
    """

    network: str
    last_scanned_block: Optional[int] = None
    head_at_last_sync: Optional[int] = None
    updated_at: Optional[str] = None
    next_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    backfill_complete: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexHealth:
    """`GET /health` — la autoridad sobre los totales y sobre las políticas.

    🔴 **Ninguna cifra del índice se tipea a mano en ningún lado.** Se lee de
    acá, viva. Regla de la casa e invariante 9 del servicio: *«Toda cifra o se
    lee viva o lleva fecha. `GET /health` es la autoridad sobre los totales.»*

    Y es también de donde salen los parámetros calibrables: `reading_policy`
    (`min_raters`, `campaign_per_rater`, `top_share`…) y
    `confidence_thresholds` viven en el `config.py` del servicio y se publican
    acá **justamente** para que ningún consumidor los vuelva a tipear. Por eso
    este SDK los expone como dicts crudos y no como constantes suyas: una
    constante local sería una copia que se pudre.

    Las políticas están versionadas por separado y `GET /health` es la
    autoridad sobre cuántas hay — esa línea de la documentación ya estuvo mal
    una vez, en el mismo batch que agregó la cuarta.
    """

    status: str
    policy_version: Optional[str] = None
    ordering_policy: Optional[str] = None
    rater_weight_policy: Optional[str] = None
    confidence_policy: Optional[str] = None
    confidence_thresholds: Dict[str, Any] = field(default_factory=dict)
    reading_policy: Dict[str, Any] = field(default_factory=dict)
    build_sha: Optional[str] = None
    agents: Optional[int] = None
    feedback_entries: Optional[int] = None
    indexer_period_seconds: Optional[int] = None
    chains: List[ChainHealth] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def chain(self, network: str) -> Optional[ChainHealth]:
        """El estado de una cadena, o `None` si el índice no la escanea."""
        for row in self.chains:
            if row.network == network:
                return row
        return None


def parse_health(payload: Any) -> IndexHealth:
    body = _require(payload, "status", "GET /health")
    try:
        chains = [
            ChainHealth(
                network=str(row["network"]),
                last_scanned_block=_opt_int(row.get("last_scanned_block")),
                head_at_last_sync=_opt_int(row.get("head_at_last_sync")),
                updated_at=row.get("updated_at"),
                next_sync_at=row.get("next_sync_at"),
                last_error=row.get("last_error"),
                backfill_complete=row.get("backfill_complete"),
                raw=dict(row),
            )
            for row in (body.get("chains") or [])
            if isinstance(row, dict) and "network" in row
        ]
        return IndexHealth(
            status=str(body["status"]),
            policy_version=body.get("policy_version"),
            ordering_policy=body.get("ordering_policy"),
            rater_weight_policy=body.get("rater_weight_policy"),
            confidence_policy=body.get("confidence_policy"),
            confidence_thresholds=dict(body.get("confidence_thresholds") or {}),
            reading_policy=dict(body.get("reading_policy") or {}),
            build_sha=body.get("build_sha"),
            agents=_opt_int(body.get("agents")),
            feedback_entries=_opt_int(body.get("feedback_entries")),
            indexer_period_seconds=_opt_int(body.get("indexer_period_seconds")),
            chains=chains,
            raw=dict(body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(f"GET /health no pasó el parse tipado: {exc}") from exc
