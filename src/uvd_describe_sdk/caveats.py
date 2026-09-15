"""The caveat codes, published as a contract — R3.

**You branch on `code`, NEVER on `text`.** This is not a style preference: the
service's schema declares it in the description of its own `Caveat` type (read
live 2026-08-30 at `api.describe.net/openapi.json`):

    code: "Stable identifier of the trap that fired. Branch on THIS, never on
           `text`. Codes are permanent; text is not."
    text: "Spanish prose meant to be shown to whoever is deciding. May be
           rewritten, re-measured or translated without notice."

And the published guide adds the why: a caveat is **advisory by construction** —
it names a cut, it moves neither a score nor a price, and that is why it does not
bump `policy_version` (docs.describe.net §"caveats[] — the same rules, already
fired").

They are exported here so the consumer **does not type them**. An `if c.code ==
"burn-adress"` with a typo does not fail: it simply never matches, and the case
the code believed it covered is left silently uncovered. That is the class of bug
an imported constant kills.

────────────────────────────────────────────────────────────────────────────
🔴 WHY THIS IS **NOT** AN `Enum`, AND IT IS THE DECISION THAT MATTERS MOST HERE
────────────────────────────────────────────────────────────────────────────
`Caveat.code` is typed `str`, not `CaveatCode`. A closed `Enum` would mean that
the day describe adds a new code, the SDK **breaks or discards it** — and
discarding a caveat is discarding the warning, which is literally the opposite of
what the field exists for.

The precedent is measured and belongs to the service itself: `Facet.direction`
travelled in the response and FastAPI silently discarded it for not being declared
— HTTP 200, correct shape, missing data (rule 5 of
`F0-describe-sdk.md:206-211`). An `Enum` here reproduces that bug on the client
side.

So the contract has two pieces:
  * `CaveatCode.*` — the constants, so strings are not typed by hand.
  * `KNOWN_CAVEAT_CODES` — the frozen set, to ASK whether a code is known. An
    unknown one is not an error: it is a new caveat that still has to be shown.

The same two pieces, for the same reason, type `ratings[].author_class` since
2026-09-15: `AuthorClass.*` and `KNOWN_AUTHOR_CLASSES` (block below).

────────────────────────────────────────────────────────────────────────────
THE EIGHT, AND WHERE THE COMPLETE SET LIVES
────────────────────────────────────────────────────────────────────────────
Copied from docs.describe.net (read 2026-08-30), which says: *"The eight codes are
the whole set, and it is frozen by a test — adding or renaming one is deliberately
red"*. That is: the set lives on the service's side and there is a test over there
that freezes it. Here it is mirrored, with its date, and `test_caveats.py` compares
this mirror against the declared count so a half-done copy goes red.

⚠️ CORRECTION 2026-09-15 — they are NINE here now, and the service serves TEN.
Left next to the old paragraph instead of replacing it, because the difference is
deliberate and whoever counts the service's set will find it:

  * `facilitator-authored` — served since 2026-09-14
    (`describenet/caveats.py:177-192` at describe-net `01f6c4a`). **Mirrored**,
    together with `ratings[].author_class`: it is exactly what the upstream-first
    row asks this SDK for (`describe-net/docs/BACKLOG.md:19`).
  * `thin-chain` — served since 2026-09-05, on the FREE door too. 🔴 **NOT
    mirrored, and not by oversight.** The TypeScript twin is brought to the same
    set the same night with the same scope, and adding a code to ONE twin breaks
    parity in a set both publish. It stays a follow-up for both twins, to land
    together (`CHANGELOG.md`, 0.6.0), instead of being patched in on one side.
    Meanwhile `is_known("thin-chain")` answers `False` — the tolerant answer
    this module was built to give: the caveat still arrives whole and is shown.

That is also why `CAVEAT_CODES_MEASURED_AT` did **not** move: a newer date would
certify a complete mirror, and this one is knowingly one code short.
"""

from __future__ import annotations

from typing import FrozenSet, List, Literal, Optional

from .models import WalletReputation

#: The date this mirror was read WHOLE from the source. Every figure is either read
#: live or carries a date (house rule) — and a set of codes is a figure. Not moved
#: on 2026-09-15 when `facilitator-authored` was added: see the module's correction.
CAVEAT_CODES_MEASURED_AT = "2026-08-30"


class CaveatCode:
    """The nine constants. A string container, **not** an Enum (see the module).

    It is not instantiated: it is a namespace so the import is explicit and
    autocompletion offers them.
    """

    #: There is no score to read. **null, never zero** — the service's invariant 7.
    NO_SCORE = "no-score"

    #: `concentration` came back `null`: the signal is down, not absent. The
    #: difference matters — "I could not measure it" is not "it is not
    #: concentrated".
    CONCENTRATION_DEGRADED = "concentration-degraded"

    #: Exactly one distinct rater.
    SINGLE_RATER = "single-rater"

    #: Below `reading_policy.min_raters` (live on 2026-08-30: 3, read from
    #: `GET /health` — it is never typed here, see `IndexHealth.reading_policy`).
    FEW_RATERS = "few-raters"

    #: At or above `reading_policy.top_share`.
    TOP_CLIENT_SHARE = "top-client-share"

    #: At or above `reading_policy.campaign_per_rater` ratings per rater.
    CAMPAIGN_PER_RATER = "campaign-per-rater"

    #: The subject rated itself. The gap is published, not judged.
    SELF_RATED = "self-rated"

    #: The subject is a known burn address: real on-chain ratings about something
    #: nobody controls. It is the ONLY one that fires today at the free door
    #: `GET /wallets/{w}/chains`.
    BURN_ADDRESS = "burn-address"

    #: Some rows of `ratings[]` carry `author_class: facilitator-authored`: their
    #: `client` is the relayer that wrote them, NOT the counterparty that rated —
    #: so every per-rater count (`concentration.distinct_raters`,
    #: `top_client_share`) merges them into one. Served since 2026-09-14, with no
    #: numbers on purpose (the per-author aggregate was cancelled on 2026-08-29).
    #:
    #: 🔴 AGENT scope only: it is the one cut that reads ROWS, and only `agent()`
    #: lists rows (`describenet/caveats.py:590-631` at `01f6c4a`). The wallet
    #: routes never fire it — and that is why the free door does not list it in
    #: `caveats_not_computed` either: declaring "not computed" there would suggest
    #: the metered wallet breakdown computes it, and it does not.
    FACILITATOR_AUTHORED = "facilitator-authored"

    def __init__(self) -> None:  # pragma: no cover - defensa, no lógica
        raise TypeError("CaveatCode is a namespace of constants, it is not instantiated")


#: The frozen set. It is ASKED, not validated against: a code outside it is a new
#: caveat from the service, and it has to be shown anyway.
KNOWN_CAVEAT_CODES: FrozenSet[str] = frozenset(
    {
        CaveatCode.NO_SCORE,
        CaveatCode.CONCENTRATION_DEGRADED,
        CaveatCode.SINGLE_RATER,
        CaveatCode.FEW_RATERS,
        CaveatCode.TOP_CLIENT_SHARE,
        CaveatCode.CAMPAIGN_PER_RATER,
        CaveatCode.SELF_RATED,
        CaveatCode.BURN_ADDRESS,
        CaveatCode.FACILITATOR_AUTHORED,
    }
)

#: The subset the FREE door can fire. The published guide warns that on
#: `GET /wallets/{w}/chains` the list is a SUBSET — today only `burn-address` —
#: and that **an empty list there does not promise the metered breakdown is
#: clean**. It is published so nobody reads the preview's silence as a verdict.
#:
#: ⚠️ CORRECTION 2026-09-15: "today only `burn-address`" stopped being true on
#: 2026-09-05 — the live schema (`/openapi.json`, `WalletChains.caveats`, read
#: 2026-09-15) names `thin-chain` too. Not changed here, for the parity reason in
#: the module's correction. And since 2026-09-14 the free door no longer relies on
#: this constant to warn: each response DECLARES what it did not compute —
#: `WalletReputation.caveats_not_computed`, read by `require_full_caveats()` below.
FREE_GATE_CAVEAT_CODES: FrozenSet[str] = frozenset({CaveatCode.BURN_ADDRESS})


def is_known(code: str) -> bool:
    """Was this code in the set as of `CAVEAT_CODES_MEASURED_AT`?

    `False` does **not** mean invalid: it means "newer than this SDK". Show it
    anyway; what you cannot do is branch logic on it without knowing which cut it
    names.
    """
    return code in KNOWN_CAVEAT_CODES


# ---------------------------------------------------------------------------
# `ratings[].author_class` — who SIGNED a row, as a class (2026-09-15)
# ---------------------------------------------------------------------------


class AuthorClass:
    """The two author classes each `Rating` carries since 2026-09-14.

    Same design as `CaveatCode`, for the same reason: a namespace of string
    constants, **not an Enum**. The live schema declares the field a closed
    `enum` (`/openapi.json`, `Rating.author_class`, read 2026-09-15) — that is the
    SERVER's promise about today, not permission for the client to break the day
    a third class appears. So `Rating.author_class` is typed `str`: an unknown
    class arrives WHOLE, the rest of the read survives, and
    `is_known_author_class()` says whether this SDK knows what it names. It is
    the Python face of the TypeScript twin's `isKnownCaveatCode` pattern.

    🔴 What `rater-authored` does NOT say, in the service's own words
    (`describenet/rating_roles.py:320-325` at `01f6c4a`): it does not prove the
    rater signed. It says `client` is not a relayer the index KNOWS. Read it as
    "no other author known", never as "verified author".
    """

    #: `client` is a known relayer (today the x402 facilitator's EVM wallet) that
    #: wrote the rating on behalf of the real rater: `client` is NOT the
    #: counterparty, and every such row shares that one `client`.
    FACILITATOR_AUTHORED = "facilitator-authored"

    #: `client` is not a relayer this index knows about. Not proof of who signed.
    RATER_AUTHORED = "rater-authored"

    def __init__(self) -> None:  # pragma: no cover - defensa, no lógica
        raise TypeError("AuthorClass is a namespace of constants, it is not instantiated")


#: The two classes as a type, to annotate a branch you wrote for them — never to
#: validate a value: `Rating.author_class` stays `Optional[str]` (see
#: `AuthorClass`). `tests/test_author_class.py` pins it equal to
#: `KNOWN_AUTHOR_CLASSES`, so the two cannot drift.
KnownAuthorClass = Literal["facilitator-authored", "rater-authored"]

#: The frozen set. ASKED, not validated against — same contract as
#: `KNOWN_CAVEAT_CODES`.
KNOWN_AUTHOR_CLASSES: FrozenSet[str] = frozenset(
    {AuthorClass.FACILITATOR_AUTHORED, AuthorClass.RATER_AUTHORED}
)


def is_known_author_class(value: Optional[str]) -> bool:
    """Is this one of the author classes this SDK knows?

    `False` for a class newer than this SDK **and** for `None` — a row served
    before 2026-09-14, which carries no class at all. Neither is invalid; what you
    cannot do is branch on a class without knowing which author it names.
    """
    return value in KNOWN_AUTHOR_CLASSES


# ---------------------------------------------------------------------------
# `caveats_not_computed` → `require_full_caveats()` — a gate that CAN fail
# ---------------------------------------------------------------------------
#
# The finding is karma-hello's (channel, 2026-08-31), recorded as a decision row
# for Saul in `describe-net/docs/BACKLOG.md:221` (origin/main `01f6c4a`): a quality
# gate built on the FREE route always passes, because the evidence-quality caveats
# are only computed by the metered breakdown — *«un consumidor honesto arma un gate
# que no puede reprobar a nadie»* ("an honest consumer builds a gate that cannot
# fail anybody"). The position answered in the channel, same row: the free/paid
# line is a cost rule, the scope is the signal, and *«el SDK puede ganar
# `require_full_caveats()`»*. Saul closed the row on 2026-09-14 with the free route
# DECLARING what it does not compute, and the service's comment names this very
# helper as the reason the declaration is a LIST of codes and not a count
# (`describenet/caveats.py:491-497`).
#
# 🔴 WHAT `None` DOES — IT RAISES — AND WHY
# ----------------------------------------
# `caveats_not_computed is None` means the answer DID NOT DECLARE: an API from
# before 2026-09-14, or a `WalletReputation` built by a `fallback_reader`. It is
# not `[]`, and the helper refuses it. Three reasons that are one reason:
#
#   1. The 2026-08-31 row is about a gate that goes green without knowing what was
#      not computed. A `None` that passes is that gate, back — and silently, on
#      every old deployment and every fallback answer.
#   2. The service refuses the same collapse on its own side: its MCP tool carries
#      `None` *«cuando la API no lo trae: una API vieja no declaró nada, y `[]`
#      afirmaría que lo calculó todo»* (`describenet/mcp_server.py:896-901`).
#   3. Before 2026-09-14 the free door did not compute those cuts either; it only
#      did not SAY so (`caveat_scope: public-data-subset` was prose). An old API's
#      silence is the old gap, not a clean bill.
#
# Only `[]` passes: an answer that DECLARED it left nothing out.
# ---------------------------------------------------------------------------


class CaveatsNotComputedError(Exception):
    """`require_full_caveats()` refused: this answer does not vouch for every cut.

    🔴 **NOT a `DescribeError`, and that is the decision.** `DescribeError` is the
    R4 taxonomy — transport and protocol, "I could not read" — and consumers wrap
    it in their own fail-open (`except DescribeError: rep = None`). A gate refusal
    caught there degrades into "describe is down", and a gate built to tolerate
    outages lets the subject THROUGH: the same silent green the 2026-08-31 row
    exists to stop. This is not a failure to read. It is a successful read that
    does not support the decision being asked of it.

    `not_computed` tells the two refusals apart — branch on it, never on the text:

        a list → the answer DECLARED these codes as not evaluated. Unverified,
                 not passed.
        None   → the answer declared NOTHING (an API older than 2026-09-14, or a
                 `fallback_reader` result). Not `[]`.

    `recovery` follows `errors.py`: a class constant that interpolates nothing.
    """

    #: The exit is real and concrete, which is why this is not `None`: what the
    #: free answer did not evaluate is exactly what the metered breakdown sells.
    recovery = (
        "What this free answer did not evaluate is what the metered breakdown "
        "sells: `wallet_breakdown()` evaluates the wallet-scope cuts listed in "
        "`not_computed` (pay with `payer=`, or use `partner=` if describe "
        "allowlisted your wallet). If `not_computed` is None the answer declared "
        "nothing — an API older than 2026-09-14 or a `fallback_reader` result "
        "(`source == 'fallback'`) — and that is not a pass either: the breakdown "
        "is still the route that evaluates them."
    )

    def __init__(
        self,
        message: str,
        *,
        wallet: str,
        not_computed: Optional[List[str]],
    ) -> None:
        super().__init__(message)
        #: The wallet whose answer was refused.
        self.wallet = wallet
        #: A COPY of what the answer declared, or `None` if it declared nothing.
        self.not_computed: Optional[List[str]] = (
            list(not_computed) if not_computed is not None else None
        )


def require_full_caveats(result: WalletReputation) -> WalletReputation:
    """Return `result` only if its answer DECLARED it left no caveat code out.

        rep = describe.wallet(address)
        if rep is None:
            ...                            # no answer at all (R5): decide that first
        rep = require_full_caveats(rep)    # raises CaveatsNotComputedError

    Three states, and only one passes:

        caveats_not_computed == []        → returns `result`, the same object.
        caveats_not_computed == [codes]   → raises; `exc.not_computed` = the codes.
        caveats_not_computed is None      → raises; `exc.not_computed` is None.

    🔴 **On the live index this raises for every `wallet()` today** (measured
    2026-09-15: seven codes declared for a wallet with 648 reviews and the same
    seven for one the index does not know). That is the helper working: the free
    door does not evaluate the evidence-quality cuts, and a gate that needs them
    has to buy `wallet_breakdown()`. What changes is that the gate can no longer
    find that out by passing.

    It only takes a `WalletReputation`, the one result that declares what it did
    not compute. Anything else is a `TypeError` naming the mistake, never a pass:
    a `None` is "describe did not answer" (R5), and a metered result evaluates its
    own scope and declares no omissions.
    """
    if not isinstance(result, WalletReputation):
        raise TypeError(
            "require_full_caveats() takes the WalletReputation that wallet() "
            f"returns; got {type(result).__name__}. A None means describe did NOT "
            "answer (R5) — handle that before asking about caveats. A Breakdown or "
            "an AgentReputation comes from a metered route, which evaluates its "
            "own caveat scope and declares no omissions."
        )
    declared = result.caveats_not_computed
    if declared is None:
        raise CaveatsNotComputedError(
            f"the answer for {result.wallet} did not declare which caveat codes "
            "it left out (no `caveats_not_computed`): an API older than 2026-09-14 "
            "or a fallback answer. Undeclared is not `[]`, so this gate does not "
            "pass.",
            wallet=result.wallet,
            not_computed=None,
        )
    if not declared:
        return result
    raise CaveatsNotComputedError(
        f"the answer for {result.wallet} declares {len(declared)} caveat code(s) "
        f"it did not compute: {', '.join(declared)}. Unverified, not passed.",
        wallet=result.wallet,
        not_computed=declared,
    )
