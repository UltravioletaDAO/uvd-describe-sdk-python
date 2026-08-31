"""The canonical format for showing a score — R8. One line, and it cost a
measurement.

    f"{round(x, 2):g}"

**Two decimals, trailing zeros trimmed.** `86.65`, `84.7`, `87` — never `82.0`,
never `86.653045`.

────────────────────────────────────────────────────────────────────────────
WHY THIS RULE EXISTS (it is not taste, it is measurement)
────────────────────────────────────────────────────────────────────────────
On 2026-08-29 the three consumers of the ecosystem rendered **the same number
three different ways**: `86.653045`, `86.7`, `86`. One field, three strings. It was
resolved by measuring over **47 distinct real scores** from the index:

    rounding to 0 decimals merges 23 pairs of DIFFERENT agents into one string
    rounding to 1 decimal   merges  4 pairs
    rounding to 2 decimals  merges  1 pair

And the trimming matters as much as the count: two surfaces "agreeing to 1
decimal" still printed `82.0` and `82` for the same agent.

**Witness case, the one that separates the three candidate rules:** the agent with
score `83.0` comes out `83` — where `toFixed(2)`/`:.2f` would print `83.00` and
`toFixed(1)` would print `83.0`. It is the first case to test, and it is verified
on three independent surfaces (docs.describe.net §"Displaying a score").

The JavaScript twin, byte-identical in result:

    String(parseFloat(x.toFixed(2)))

────────────────────────────────────────────────────────────────────────────
WHAT THIS IS **NOT**
────────────────────────────────────────────────────────────────────────────
It is a **display** convention. The API keeps serving the number at full precision
(six decimals), and **what you compute with is the number, never the string**. If
somebody compares scores by comparing strings, they sorted lexicographically and
`"9"` ended up above `"86.65"`.

🔴 **And the trap this module exists to not let through:** `0.0` formats as `"0"`.
A score that does not exist (`None`) formatted as `"0"` would be exactly R1's lie
— "there is no evidence" printed as "they were rated terribly" — and it is a
one-line mistake. That is why `format_score(None)` **does not return a number**: it
returns the placeholder, and the default is an em dash, not a zero.
"""

from __future__ import annotations

from typing import Optional

#: What is shown when there is no score. **Never a digit.** A `"0"` here would
#: turn "no data" into "bad", which is the service's invariant 7 broken on the last
#: line of the path.
NO_SCORE_PLACEHOLDER = "—"


def format_score(score: Optional[float], *, placeholder: str = NO_SCORE_PLACEHOLDER) -> str:
    """Format a score for display. `None` → `placeholder`, never `"0"`.

    >>> format_score(86.653045)
    '86.65'
    >>> format_score(84.7)
    '84.7'
    >>> format_score(87)
    '87'
    >>> format_score(82.0)
    '82'
    >>> format_score(83.0)        # the witness case
    '83'
    >>> format_score(0.0)         # a MEASURED zero really is "0"
    '0'
    >>> format_score(None)        # absence is NOT
    '—'

    Measured note (2026-08-30): `:g` falls into scientific notation above six
    significant digits (`1234567.891` → `'1.23457e+06'`). It is irrelevant for a
    score, which lives in `[0, 100]`, and it is written down so nobody reuses this
    function as a general-purpose formatter.
    """
    if score is None:
        return placeholder
    return f"{round(score, 2):g}"
