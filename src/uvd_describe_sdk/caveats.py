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
  * `CaveatCode.*` — the eight constants, so strings are not typed by hand.
  * `KNOWN_CAVEAT_CODES` — the frozen set, to ASK whether a code is known. An
    unknown one is not an error: it is a new caveat that still has to be shown.

────────────────────────────────────────────────────────────────────────────
THE EIGHT, AND WHERE THE COMPLETE SET LIVES
────────────────────────────────────────────────────────────────────────────
Copied from docs.describe.net (read 2026-08-30), which says: *"The eight codes are
the whole set, and it is frozen by a test — adding or renaming one is deliberately
red"*. That is: the set lives on the service's side and there is a test over there
that freezes it. Here it is mirrored, with its date, and `test_caveats.py` compares
this mirror against the declared count so a half-done copy goes red.
"""

from __future__ import annotations

from typing import FrozenSet

#: The date this mirror was read from the source. Every figure is either read live
#: or carries a date (house rule) — and a set of codes is a figure.
CAVEAT_CODES_MEASURED_AT = "2026-08-30"


class CaveatCode:
    """The eight constants. A string container, **not** an Enum (see the module).

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
    }
)

#: The subset the FREE door can fire. The published guide warns that on
#: `GET /wallets/{w}/chains` the list is a SUBSET — today only `burn-address` —
#: and that **an empty list there does not promise the metered breakdown is
#: clean**. It is published so nobody reads the preview's silence as a verdict.
FREE_GATE_CAVEAT_CODES: FrozenSet[str] = frozenset({CaveatCode.BURN_ADDRESS})


def is_known(code: str) -> bool:
    """Was this code in the set as of `CAVEAT_CODES_MEASURED_AT`?

    `False` does **not** mean invalid: it means "newer than this SDK". Show it
    anyway; what you cannot do is branch logic on it without knowing which cut it
    names.
    """
    return code in KNOWN_CAVEAT_CODES
