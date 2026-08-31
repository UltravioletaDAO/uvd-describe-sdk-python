"""The typed shapes and their parsers — where R1, R2 and additive tolerance live.

No `httpx`, no clock, no SQL: pure stdlib. A parser is a function from `dict` to a
frozen dataclass and it can be tested without network.

════════════════════════════════════════════════════════════════════════════
R1 — `null` NEVER `0`. The invariant this file makes impossible to break.
════════════════════════════════════════════════════════════════════════════
`_opt_float` returns `None` when the value is `None`. **It NEVER substitutes
0.0.** It is the service's invariant 7 ("With no ratings the field is `null`,
never `0`. *No data* is not *bad*") brought down to a three-line function.

And there are THREE distinct facts the type cannot conflate, all legitimate and
none of them an error:

    not registered        → WalletReputation(identity_count=0, chains=[],
                                             global_score=None)
    registered, unrated   → WalletReputation(identity_count=1, chains=[…],
                                             global_score=None)
    could not be read     → the method returns None (see `client.py`, R5)

The first two are OBJECTS. The distinction between them is NOT whether the score
is `None` — in both it is — but `identity_count` / `chains_with_identity`.
Whoever wants to decide looks there. A `0` in `global_score` would claim "they
were rated terribly" about somebody nobody ever rated; measured in production: a
prior of 50 painted a *silver* badge on executors with no history, and the written
conclusion was "the 50 is worse than a gap".

════════════════════════════════════════════════════════════════════════════
R2 — no result is a bare number
════════════════════════════════════════════════════════════════════════════
Every result dataclass carries `policy_version`, `caveats` and its source
(`source` / `refreshed_at` / `snapshot`). There is no `get_score() -> float` in
this SDK and there is not going to be: the product's thesis is that **"a score
without its raters is a rumour"**, and a method returning a `float` erases it.
Whoever wants the number alone takes it off the object by hand, and **that is on
purpose**: the gesture of taking it out is what leaves written in the caller's
code that they decided to throw the context away. `tests/test_r2_r3_contrato.py`
pins it by introspection — and that test is **verified by mutation**
(2026-08-30): an exported `get_score(x: float) -> float` was injected and it went
red naming it (`assert not ['get_score() -> float']`). A contract test that has
never been seen red does not prove the contract exists.

════════════════════════════════════════════════════════════════════════════
ADDITIVE TOLERANCE — type what is known and PRESERVE what is not
════════════════════════════════════════════════════════════════════════════
Every dataclass keeps `raw`: the whole payload exactly as it arrived. A field the
service adds tomorrow **still reaches** whoever needs it even though this SDK does
not know about it.

The precedent belongs to the service itself and it is exactly this bug on the
other side of the cable: `Facet.direction` travelled in the response and FastAPI
silently discarded it for not being declared — HTTP 200, correct shape, missing
data (rule 5 of `F0-describe-sdk.md:206-211`). An SDK that types strictly and
throws away the rest reproduces that bug on the client side, and with a green
`200` on top.

Measured today: `WalletChains.distinct_raters` travels live on
`api.describe.net` (2026-08-30) and does **not exist** in EM's `types.py`, which
is the reference implementation. Without `raw`, a consumer migrating from EM to
this SDK would lose a field the API already serves.

════════════════════════════════════════════════════════════════════════════
WHAT IS AN EXCEPTION
════════════════════════════════════════════════════════════════════════════
A parser raises `DescribeUnparseable` only if the ESSENTIAL key the route promises
in its schema is missing (`wallet`, `matches`, `status`…). A `chains: []` is a
valid shape that says "there is nothing here" and does not go through there — R4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .errors import DescribeUnparseable
from .hashes import looks_like_onchain_id

# ---------------------------------------------------------------------------
# Primitivas de parseo
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> Optional[float]:
    """`None` stays `None` — it is **NEVER** turned into `0.0`. R1, invariant 7."""
    if value is None:
        return None
    return float(value)


def _opt_int(value: Any) -> Optional[int]:
    """`None` stays `None`. Different from `_int0`: here the gap is a fact.

    Real case: `distinct_raters` comes back `None` on the agent rows of `/search`
    because it is not computed there. Rendering it as `0` would claim "N reviews
    from zero raters", which is arithmetically impossible and would nonetheless be
    printed without anything failing.
    """
    if value is None:
        return None
    return int(value)


def _int0(value: Any) -> int:
    """Counters the schema declares required and non-null: absent ⇒ 0.

    Here the `0` IS correct and has to be told apart from `_opt_int`: "how many
    chains have an identity" with an empty answer is zero chains, a countable
    fact. It is never used for a SCORE.
    """
    return int(value or 0)


def _hash_field(row: Dict[str, Any], key: str, malformed: List[str]) -> Optional[str]:
    """A hash field: returned if it is SHAPED like a hash, otherwise `None` + mark.

    Contributed by **KarmaKadabra** (`#agents`, 2026-08-30), out of the finding
    they call *"el 200 sin tx"*: *"si nosotros no chequeáramos el tx, habríamos
    contado 14 ratings que no existen"* — [in English] "if we did not check the
    tx, we would have counted 14 ratings that do not exist". See `hashes.py` for
    the four measured legitimate shapes and for why an EVM-hash regex alone would
    have flagged the whole of Solana.

    🔴 **ABSENT AND MALFORMED ARE NOT THE SAME THING.** This is R1 one level
    further down, and it is the reason the `malformed` list exists instead of just
    returning `None`:

        it did not come → `None`, and `key` does NOT enter `malformed`. This is
                          normal: the schema itself says `tx_hash` is *"null until
                          the log scan reaches this entry, not null forever"*.
        garbage came    → `None` **and** `key` in `malformed`. That is the one
                          that screams.

    `None` is returned instead of the raw value on purpose: whoever has the field
    is going to put it in an explorer URL, and a link to garbage is the same lying
    200 one layer up. The value exactly as it arrived survives anyway in the
    model's `raw` (additive tolerance), which is where it gets investigated.
    """
    value = row.get(key)
    if value is None:
        return None
    text = str(value)
    if looks_like_onchain_id(text):
        return text
    malformed.append(key)
    return None


def _require(payload: Any, key: str, what: str) -> Dict[str, Any]:
    if not isinstance(payload, dict) or key not in payload:
        raise DescribeUnparseable(
            f"the response from {what} does not carry the essential shape "
            f"(missing `{key}`)"
        )
    return payload


# ---------------------------------------------------------------------------
# Caveats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Caveat:
    """A trap THESE numbers fired, with its stable code.

    🔴 `code` IS THE CONTRACT; `text` IS NOT. You branch on `code` (see
    `uvd_describe_sdk.caveats`), never on `text`, which is Spanish prose and may be
    rewritten, re-measured or translated without notice — the service's own schema
    declares as much.

    `code` is typed `str` and not an `Enum` on purpose: a new code from the
    service has to arrive whole, not break and not disappear.
    """

    code: str
    text: str = ""


def parse_caveats(raw: Any) -> List[Caveat]:
    """`[{code, text}]` → `[Caveat]`. An entry with no `code` is discarded.

    It is discarded and does not blow up: a malformed caveat is advisory lost, not
    a reason to take down a reputation read that otherwise arrived fine.
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
    """One row per chain of `GET /wallets/{w}/chains`."""

    network: str
    agent_count: int = 0
    agent_ids: Optional[List[str]] = None
    #: `None`, never `0` — R1.
    final_score: Optional[float] = None
    total_reviews: int = 0
    distinct_raters: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WalletReputation:
    """`GET /wallets/{w}/chains` — FREE, cached at the edge (~60 s TTL).

    It is **the door**: it says whether this wallet has anything before you pay for
    the breakdown. The 402 itself declares it in `free_preview`: *"if there is no
    reputation there, this charge returns nothing"*.

    `global_score` is the average of the per-chain averages (one chain, one vote),
    served from `chain_rankings_mv`. It may differ in decimals from the metered
    lookup, and that is why `source` + `refreshed_at` travel saying so.

    ⚠️ `caveats` here is a **SUBSET** of the metered route's — today only
    `burn-address`. An empty list at this door **does not promise** that the
    metered breakdown is clean (`caveats.FREE_GATE_CAVEAT_CODES`).
    """

    wallet: str
    chains: List[ChainReputation] = field(default_factory=list)
    caveats: List[Caveat] = field(default_factory=list)
    identity_count: int = 0
    chains_with_identity: int = 0
    chains_with_reputation: int = 0
    total_reviews: int = 0
    distinct_raters: Optional[int] = None
    #: `None`, never `0` — R1.
    global_score: Optional[float] = None
    policy_version: Optional[str] = None
    source: Optional[str] = None
    #: ISO exactly as the index serves it. `None` = "not recorded", never "just
    #: now".
    refreshed_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_identity(self) -> bool:
        """Does the index know any ERC-8004 identity of this wallet?

        It is the half of R1's distinction that `global_score is None` cannot give
        on its own: `False` = not registered; `True` with `global_score is None` =
        registered and not yet rated. Two facts, both of them answers.
        """
        return self.identity_count > 0

    @property
    def caveat_codes(self) -> List[str]:
        """Just the `code`s. A shortcut for branching without touching `text`."""
        return [c.code for c in self.caveats]

    def resolve_distinct_raters(self) -> Optional[int]:
        """How many DISTINCT raters this wallet has. It prefers the global figure.

        Contributed by **MeshRelay** (`#agents`, **2026-08-30**). What is valuable
        about this helper is not its seven lines: it is the TWO traps MeshRelay
        measured before writing it, and which — if they are not written down here
        — make it look trivial and get it used wrongly.

        🔴 **THE NAME IS PART OF THE CONTRIBUTION, and it is not the one the
        original carries.** At MeshRelay the function is called
        `maxDistinctRaters`, and they themselves asked for it NOT to be called
        that here. The request is quoted verbatim because it explains the change
        better than any summary could: *«ese nombre invita a creer que el máximo es
        la respuesta correcta, cuando es el último recurso. El mío está mal
        nombrado y lo arrastro de cuando el nivel wallet no existía»* — [in
        English] "that name invites you to believe the maximum is the right answer,
        when it is the last resort. Mine is badly named and I drag it along from
        when the wallet level did not exist".

        It is the cheapest contribution of all and one of the most useful: somebody
        hands you a function **and warns you not to copy its name**. A `max…()`
        reads as "give me the maximum" — which is exactly what NOT to do — and a
        `resolve…()` reads as "work out which one is the good one", which is what
        it does. If it is ever renamed, let it be knowing why it moved the first
        time.

        (Correction 2026-08-31: this method was born here as `max_distinct_raters`
        and was renamed the same day. The TypeScript twin was born with the good
        name already because MeshRelay's request reached it in time and did not
        reach this side — the asymmetry is in the channel between agents, not in
        the criterion.)

        ════════════════════════════════════════════════════════════════════
        🔴 TRAP 1 — SUMMING THE CHAINS DOUBLE-COUNTS
        ════════════════════════════════════════════════════════════════════
        One rater who rated you on two networks is counted twice. Case measured by
        MeshRelay: the **karma-hello** wallet reads **9** `distinct_raters`
        globally and **11** when the chains are added up.

        ════════════════════════════════════════════════════════════════════
        🔴 TRAP 2 — THE MAXIMUM UNDERESTIMATES
        ════════════════════════════════════════════════════════════════════
        Case measured by MeshRelay: **3** raters on `base` and **4 DIFFERENT** ones
        on `avalanche` are **7** real ones, and the maximum answers **4**.

        So the two obvious ways of deriving it are wrong in opposite directions,
        and **the maximum serves ONLY as a lower bound / fallback when the global
        figure is not there. NEVER as the answer.**

        Corroborated on our side on 2026-08-30 against `api.describe.net`, on the
        **3 of 3** multi-chain wallets of `GET /leaderboard`: both traps fire on
        all of them, without a single exception.

            wallet          global    sum    maximum
            0xcc28cee3…        129    134        113
            0xf9d1d63f…       1542   1555       1513
            0x0d68a153…         40     41         35

        That is why this method **prefers the global figure** (`distinct_raters`,
        which the free route already serves live) and only falls back to the
        maximum when it did not come. There is no helper that sums — and there is
        not going to be: the sum is not a worse approximation, it is a false claim.

        Returns:
            The global figure if the index sent one: **the answer**, exact.

            If it did not come, the per-chain maximum: a **LOWER BOUND**, never the
            real number. `>=` is the only thing that can be asserted with it.

            `None` if there is neither a global figure nor chains — R1: no data is
            not zero. A `0` here would claim "nobody rated it" about a wallet we
            know nothing about.
        """
        if self.distinct_raters is not None:
            return self.distinct_raters
        if not self.chains:
            return None
        return max(c.distinct_raters for c in self.chains)


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
            f"GET /wallets/{{wallet}}/chains did not pass the typed parse: {exc}"
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
    """The confidence band and its policy, **versioned apart from the score**.

    `confidence_policy` is not `policy_version`: it moves not a single score. The
    service separates them on purpose — merging them would mark every rating as
    recomputed each time a list changes — and `GET /health` is the authority on
    how many policies there are.
    """

    band: Optional[str] = None
    distinct_raters: int = 0
    interval: Optional[ConfidenceInterval] = None
    thresholds: Dict[str, Any] = field(default_factory=dict)
    advice: Optional[str] = None
    confidence_policy: Optional[str] = None


@dataclass(frozen=True)
class Concentration:
    """How concentrated the reputation is in a single rater.

    `top_client_share is None` is **not** "it is not concentrated": it is "the
    signal is down" — and when that happens, the service fires the
    `concentration-degraded` caveat precisely so it is not read as a clean bill.
    """

    distinct_raters: int = 0
    top_client_share: Optional[float] = None
    top_client: Optional[str] = None


@dataclass(frozen=True)
class SelfRated:
    """How much the subject rated itself. The gap is published, not judged."""

    count: int = 0
    score: Optional[float] = None
    gap: Optional[float] = None


@dataclass(frozen=True)
class Activity:
    first_rating_at: Optional[str] = None
    last_rating_at: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    """The citable snapshot ($0.05 instead of $0.01): the evidence with its digest.

    It is what turns a read into something you can cite later: `inputs_digest` +
    `policy_version` + `computed_at` say over which inputs and under which policy
    THIS number was computed.
    """

    id: Optional[int] = None
    #: `None` if it did not come **or if something not shaped like a digest came**
    #: — which of the two is said by `malformed_hashes`. It is a BARE sha256
    #: hexdigest (64 hex, no `0x`): `aggregate.py:1920`. See `hashes.py`.
    inputs_digest: Optional[str] = None
    policy_version: Optional[str] = None
    computed_at: Optional[str] = None
    #: The hash fields that arrived malformed. Empty = all fine **or** all absent;
    #: the two are told apart by looking at the field. See `_hash_field`.
    malformed_hashes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainScore:
    """One chain's score inside the metered breakdown."""

    score: Optional[float] = None
    review_count: int = 0
    agent_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Facet:
    """A facet (on-chain `tag1`) with its score and its declared direction.

    🔴 `direction` / `direction_category` / `direction_meaning` are ADVISORY: they
    are what the ISSUER declares, not something the index verifies. And `tag1` is
    **free on-chain text** — the longest facet in the index is 471 characters (a
    paragraph about gardening used as a label). Escape everything that comes from
    the chain before rendering it.
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
    """How much of an agent's reputation is INHERITED from a previous owner.

    An ERC-8004 agent can be transferred. Without this block, the old owner's
    reviews read as the new owner's history.
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
    """An individual rating: **the grain**, and the reason this is charged for.

    The service does not sell a number, it sells the breakdown: who rated
    (`client`), how many times, in which transaction (`tx_hash`) and who wrote the
    entry (`issuer_host`). It is the physical answer to *"a score without its
    raters is a rumour"*.

    🔴 **The three hash fields arrive SHAPE-CHECKED** (`tx_hash`,
    `feedback_hash`, `revoked_tx`) — contributed by KarmaKadabra, 2026-08-30, out
    of the *"el 200 sin tx"* finding: *«habríamos contado 14 ratings que no
    existen»* ("we would have counted 14 ratings that do not exist"). A value not
    shaped like an on-chain identifier is left `None` and its name enters
    `malformed_hashes`. Absent and malformed are NOT the same thing — see
    `_hash_field` and `hashes.py`.
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
    #: The transaction that wrote this rating. `None` if it did not come **or** if
    #: it came malformed; `malformed_hashes` says which of the two. It being `None`
    #: is normal and expected: *"null until the log scan reaches this entry"*.
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    log_index: Optional[int] = None
    feedback_uri: Optional[str] = None
    issuer_host: Optional[str] = None
    issuer: Optional[str] = None
    issuer_org: Optional[str] = None
    #: NULL on purpose on Solana (`solana_indexer.py:27`): a gap here is correct,
    #: not a shortcoming.
    feedback_hash: Optional[str] = None
    revoked_tx: Optional[str] = None
    #: Which of the three above arrived carrying something that is not a hash.
    #: Empty in the normal case. 🔴 Branch on this, not on `tx_hash is None`.
    malformed_hashes: Tuple[str, ...] = ()
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentReceipt:
    """The settlement headers the paywall emits and that **nobody was reading**.

    Contract recorded in `F0-describe-sdk.md:192-205`: the service returns
    `X-Payment-Receipt` (the settlement transaction hash, public, useful for
    reconciling) and `X-Payment-Reused` (`true` = a receipt was replayed, nothing
    was charged again) on every paid 200 — and until today **no client read
    them**. Verified in the service: `paywall.py:1059-1062` writes them and
    `api.py:2226` puts them in the CORS `expose_headers`.

    Exposing them is the difference between "I paid" and "I can prove I paid".

    🔴 `transaction_hash` is shape-checked too, but with its OWN rule
    (`hashes.looks_like_settlement_receipt`): the live OpenAPI declares this header
    may be worth the literal **`pending`** when settlement has not reported its
    hash yet. `pending` is LEGITIMATE and is not marked — treating it as garbage
    would turn the happy path of every freshly settled payment into an alarm, and
    an alarm that fires on the happy path gets learned into being ignored.

    **But legitimate is not the same as a hash, and it must not sit in the field
    named after one.** `pending` now travels in `settlement_pending` and
    `transaction_hash` stays `None`, because the two answers a caller needs are
    different: *is this payment settled?* and *can I name the transaction?* A
    single field cannot hold both, and the shape it took — a word occupying the
    place of a proof — is the one that reads as a proof to everything downstream.

    That failure has a price tag. In Execution Market (INC-2026-08-26) a
    placeholder string, `"timeout-verified-onchain"`, lived in the column that
    held the payment hash. Six executors were recorded as paid and **no money had
    moved**; three separate sites read the field as truthy and agreed. The lesson
    they wrote into their schema is the one applied here: a payment may only be
    recorded when the transaction that made it can be named, and when it cannot,
    the honest value is NULL. This SDK is upstream of every consumer's database —
    whatever it puts in `transaction_hash` is what ends up in that column.
    """

    transaction_hash: Optional[str] = None
    reused: bool = False
    pricing_version: Optional[str] = None
    #: `("transaction_hash",)` if the header brought something that is neither a
    #: hash nor `pending`. Empty in the normal case.
    malformed_hashes: Tuple[str, ...] = ()
    #: The service charged and settlement has not reported its hash **yet**.
    #: True only for the literal `pending` sentinel. Distinct from
    #: `transaction_hash is None` with this flag false, which means the header
    #: was absent or malformed — an absence, not a promise.
    settlement_pending: bool = False


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


def _parse_snapshot(raw: Dict[str, Any]) -> Snapshot:
    """The snapshot, with its `inputs_digest` shape-checked.

    A malformed digest cannot take down an already PAID breakdown — that would be
    charging the caller and handing them back an exception over an accessory
    field. But it cannot go by in silence either: it is precisely what makes
    uncitable a snapshot that was bought in order to be cited.
    """
    malos: List[str] = []
    return Snapshot(
        id=_opt_int(raw.get("id")),
        inputs_digest=_hash_field(raw, "inputs_digest", malos),
        policy_version=raw.get("policy_version"),
        computed_at=raw.get("computed_at"),
        malformed_hashes=tuple(malos),
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
    """`GET /reputation/wallet/{w}` — a wallet's breakdown.

    Six statements over the grain — per chain, self-rating, facets, activity,
    weighted (two) and concentration — resolved in tens of milliseconds. That is
    what is charged for, not the number: the global number is free in
    `WalletReputation`.

    `final_score` vs `weighted_score`: the first is equal-weight; the second
    applies `rater_weight_policy`. **Both can be `None`** and neither is 0.
    """

    wallet: str
    #: `None`, never `0` — R1.
    final_score: Optional[float] = None
    #: `None`, never `0` — R1.
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
                _parse_snapshot(snapshot_raw)
                if isinstance(snapshot_raw, dict)
                else None
            ),
            receipt=receipt,
            raw=dict(body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DescribeUnparseable(
            f"GET /reputation/wallet/{{wallet}} did not pass the typed parse: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# PAGA — GET /reputation/agent/{network}/{agent_id}   ($0,02)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentReputation:
    """`GET /reputation/agent/{n}/{id}` — an agent with its ratings.

    ⚠️ `declared_type` **is not a verified type**. Measured 2026-08-30: 283.770 of
    470.064 agents (60,4 %) are `unknown`, and the second most common "type" is the
    EIP's schema URL — along with its typo'd variant. ERC-8004 has no type field.
    It is never used as verification.
    """

    network: str
    agent_id: str
    current_owner: Optional[str] = None
    declared_type: Optional[str] = None
    agent_uri: Optional[str] = None
    indexed_identity: bool = False
    #: `None`, never `0` — R1.
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


def _parse_rating(row: Dict[str, Any]) -> Rating:
    """One rating, with its THREE hash fields shape-checked.

    All three accumulate into the same list so a rating with two rotten fields is
    reported whole and not one at a time: whoever investigates wants to see a
    row's complete damage, not discover it by drip.
    """
    malos: List[str] = []
    return Rating(
        client=str(row.get("client") or ""),
        feedback_index=_int0(row.get("feedback_index")),
        value=_int0(row.get("value")),
        value_decimals=_int0(row.get("value_decimals")),
        normalized_value=_opt_float(row.get("normalized_value")),
        tag1=row.get("tag1"),
        tag2=row.get("tag2"),
        is_revoked=bool(row.get("is_revoked")),
        is_self=bool(row.get("is_self")),
        tx_hash=_hash_field(row, "tx_hash", malos),
        block_number=_opt_int(row.get("block_number")),
        log_index=_opt_int(row.get("log_index")),
        feedback_uri=row.get("feedback_uri"),
        issuer_host=row.get("issuer_host"),
        issuer=row.get("issuer"),
        issuer_org=row.get("issuer_org"),
        feedback_hash=_hash_field(row, "feedback_hash", malos),
        revoked_tx=_hash_field(row, "revoked_tx", malos),
        malformed_hashes=tuple(malos),
        raw=dict(row),
    )


def parse_agent_reputation(
    payload: Any, receipt: Optional[PaymentReceipt] = None
) -> AgentReputation:
    body = _require(payload, "agent_id", "GET /reputation/agent/{network}/{agent_id}")
    try:
        ownership_raw = body.get("ownership")
        ratings = [
            _parse_rating(row)
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
            f"GET /reputation/agent did not pass the typed parse: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# El informe de hashes malformados — lo que el cliente observa
# ---------------------------------------------------------------------------


def malformed_hash_report(result: Any) -> List[str]:
    """Every malformed hash field of a result, with its location.

    It exists so `DescribeClient` can OBSERVE the fact down the same channel the
    fail-open already uses (`on_error` + WARNING) without `models.py` ceasing to be
    pure: there is no clock, no network and no observer here — it only collects
    what the parsers already marked. The decision of who to tell lives where the
    observer lives, which is the client.

    It returns readable paths, with an index when the field is inside a list:

        ["ratings[3].tx_hash", "snapshot.inputs_digest", "receipt.transaction_hash"]

    An empty list = no field arrived with garbage. It does **not** mean they all
    arrived: an absent hash is legitimate and is not reported here.
    """
    fields: List[str] = []
    for i, rating in enumerate(getattr(result, "ratings", None) or []):
        fields.extend(f"ratings[{i}].{name}" for name in rating.malformed_hashes)
    for attr in ("snapshot", "receipt"):
        parte = getattr(result, attr, None)
        if parte is not None:
            fields.extend(f"{attr}.{name}" for name in parte.malformed_hashes)
    return fields


# ---------------------------------------------------------------------------
# GRATIS — GET /leaderboard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaderboardRow:
    """One row of the ranking.

    🔴 **The leaderboard does NOT order by average: it orders by the Bayesian
    mean.** `shrunk_score` and `distinct_raters` travel in the response precisely
    so the ordering can be recomputed by hand. Sorting by `final_score` gives a
    different list and looks like a service bug.
    """

    rank: int
    wallet: str
    #: `None`, never `0` — R1.
    final_score: Optional[float] = None
    #: The one that RULES the ordering. Also `None`-able.
    shrunk_score: Optional[float] = None
    distinct_raters: int = 0
    chain_count: int = 0
    total_reviews: int = 0
    networks: List[str] = field(default_factory=list)
    declared_types: List[Optional[str]] = field(default_factory=list)
    #: Travels on EVERY row (R2): a ranking without its policy is a sorted rumour.
    policy_version: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_leaderboard(
    payload: Any, policy_version: Optional[str] = None
) -> List[LeaderboardRow]:
    """`GET /leaderboard` → rows.

    ⚠️ The route returns a **bare JSON array**, not an object, so it does not carry
    `policy_version` inside. R2 requires every result to carry its policy, so the
    client injects it from `X-Policy-Version` if the service sends it, or from the
    `policy_version` it already knows. Measured 2026-08-30:
    `GET /leaderboard?limit=3` → **HTTP 422 `leaderboard_takes_no_params`**; the
    first page is free and whole, paging is `/leaderboard/page` ($0.01).
    """
    if not isinstance(payload, list):
        raise DescribeUnparseable(
            "GET /leaderboard did not return an array (did you send query params? "
            "the free route accepts none and answers 422)"
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
            f"GET /leaderboard did not pass the typed parse: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# GRATIS — GET /health
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainHealth:
    """The indexing state of ONE chain.

    `next_sync_at` is the freshness pointer: up to there, what there is is what
    there is. A chain that is not in the list **is not indexed** — a partial index
    is partial data, not an error.
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
    """`GET /health` — the authority on the totals and on the policies.

    🔴 **No figure of the index is typed by hand anywhere.** It is read from here,
    live. House rule and invariant 9 of the service: *"every figure is either read
    live or carries a date. `GET /health` is the authority on the totals."*

    And it is also where the calibratable parameters come from: `reading_policy`
    (`min_raters`, `campaign_per_rater`, `top_share`…) and `confidence_thresholds`
    live in the service's `config.py` and are published here **precisely** so no
    consumer re-types them. That is why this SDK exposes them as raw dicts and not
    as constants of its own: a local constant would be a copy that rots.

    The policies are versioned separately and `GET /health` is the authority on
    how many there are — that line of the documentation was already wrong once, in
    the same batch that added the fourth.
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
        """One chain's state, or `None` if the index does not scan it."""
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
        raise DescribeUnparseable(f"GET /health did not pass the typed parse: {exc}") from exc
