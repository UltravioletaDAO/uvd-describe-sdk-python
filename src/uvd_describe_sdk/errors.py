"""The failure taxonomy — and the line that separates "I could not read" from
"there is no data".

RULE R4 OF THE CORE CONTRACT, and it is the reason this module exists:
**an exception is raised ONLY for transport or protocol** (timeout, 5xx, invalid
JSON, a body that does not have the promised shape). **Never for "there is no
data".** An unrated wallet is an ANSWER — the service's own A2A door spells it
out: *"That is an answer, not an error"*.

Why it matters, measured: on 2026-08-28 at KarmaKadabra a read failure was
reported as if it were the data ("I could not read" read as "has no reputation")
and it cost a wrong report in the gate that decides who to trade with
(`karmakadabra/lib/reputation_scan.py:110-118`, written because of that
incident). That confusion is exactly what this taxonomy exists to make
impossible.

────────────────────────────────────────────────────────────────────────────
WHERE THESE NAMES COME FROM, AND WHAT CHANGED FROM THE REFERENCE
────────────────────────────────────────────────────────────────────────────
The reference implementation is
`execution-market/mcp_server/integrations/describenet/types.py:37-95` (578 lines
in total with its client, the most complete HTTP reader of the three measured
consumers). Its taxonomy is ABSORBED almost whole:

    DescribeNetError → Timeout | HTTPError | Unreachable | Unparseable
                     | PartialIndex

and above all what is absorbed is the mechanism that makes it useful: **a stable
`kind` attribute to branch on, never the message text** — the same principle the
service applies to `caveats[].code` vs `caveats[].text`.

Two differences, declared here because whoever comes from EM will notice them and
deserves to find the reason instead of a surprise:

1. **`kind = "http_5xx"` is renamed to `"http_error"`.** In EM the bucket is
   called `http_5xx` and its own docstring clarifies that 422 and 429 land there
   too "because every consumer treats any non-2xx the same" (`types.py:56-60`).
   A name you have to disown in its own docstring is a badly chosen name.
   `status_code` still travels, which is what actually gets read.
   👉 EM's `kind` is published as a readable alias in `HTTP_5XX_LEGACY_KIND` for
   anyone migrating an `if err.kind == "http_5xx"`.

2. **`PartialIndex` is NOT ported.** In EM the client "never raises it"
   (`types.py:84-89`): it exists so higher layers can classify partial coverage
   inside the same taxonomy. A partial index is served as a 200 whose `chains[]`
   simply lacks the row — that is, it is DATA, and by R4 data cannot be an
   exception. Porting it here would publish an exception the SDK never raises,
   inviting a dead `except`.

────────────────────────────────────────────────────────────────────────────
WHAT THE FAIL-OPEN DOES **NOT** COVER
────────────────────────────────────────────────────────────────────────────
`PaymentRequiredError` and `DoNotPayError` inherit from `DescribeError` but
`DescribeClient` NEVER swallows them, not even with `fail_open=True`. The
fail-open exists for the AVAILABILITY of the index ("put a fallback in if
describe is down" — Saul, 2026-08-28), not for the caller's configuration.
Swallowing them would turn "you forgot to configure payment" into "this wallet
has no reputation", which is the same lie R1 exists to prevent.

And since 2026-08-30 there is a second axis: **the METERED routes are never
swallowed by the fail-open, whatever the exception and whatever `fail_open` is
worth.** See `client.py`, block "WHICH METHOD IS NULLABLE".

The two of the partner rail — `PartnerSigningError` and `PartnerRejectedError` —
fall in the same bag and for the same reason: they are the caller's
configuration. But they also carry a claim no other one makes, and it is the good
half of the matter: **`payment_sent is False` is a strong truth there**. Both are
raised BEFORE any payment authorization is signed, so whoever receives them knows
they did not spend — they found out they lost the free rail WITHOUT having spent
the USDC the rail was saving them. That is the whole decision of partner mode.

────────────────────────────────────────────────────────────────────────────
🔴 `payment_sent` — THE MARK THAT TELLS "YOU DID NOT SPEND" FROM "MAYBE YOU DID"
────────────────────────────────────────────────────────────────────────────
A bare `DescribeTimeout` does not distinguish two facts that are worth different
money:

    it died BEFORE signing   → no credential left. You spent nothing.
    it died AFTER signing    → the EIP-3009 authorization is already signed and
                               dispatched. The USDC may have moved.

The second case is the one the corrected R5 protects: that is why the metered
routes always raise. But raising is not enough if the exception does not say
which of the two it is — whoever receives it has to know whether reconciling is
their job.

`payment_sent` and `payment` are that mark. `mark_payment_sent()` sets them, and
only `DescribeClient` sets them, in the stretch after the signature. On every
free route they are `False` / `None`, always.

────────────────────────────────────────────────────────────────────────────
🔴 `recovery` — WHAT TO DO INSTEAD, AND WHY EMPTY IS AN ANSWER
────────────────────────────────────────────────────────────────────────────
Contributed by **Execution Market** (`#agents`, **2026-08-30**), who that day
published that their 502 would start carrying `detail.code`, `detail.retryable`
and `detail.recovery` with ten typed codes, and said it verbatim: *"para que lo
codifiquen de su lado"* ("so you can code it on your side"). Their argument is
what justifies this field — quoted in the original Spanish, as it was said:

    *"SIETE de los diez son TERMINALES (retryable:false). Eso es lo que más les
     sirve: hoy su flota no puede distinguir 'reintenta' de 'no insistas', y
     contra AUTHORIZATION_EXPIRED reintentar es quemar llamadas contra una
     ventana cerrada hace 317 HORAS."*

    [translation] "SEVEN of the ten are TERMINAL (retryable:false). That is what
    helps you most: today your fleet cannot tell 'retry' from 'do not insist',
    and against AUTHORIZATION_EXPIRED retrying is burning calls against a window
    that closed 317 HOURS ago."

**What is absorbed is the PATTERN, not their table.** Their ten codes belong to
THEIR API — escrow, release to the worker, payout wallets — and this SDK does not
wrap that API but describe's. A consumer branching on `AUTHORIZATION_EXPIRED`
against `api.describe.net` would be writing dead code, so OUR `kind`s were walked
one by one and decided case by case.

⚠️ **The premise of the contribution was measured and is false for this repo, and
it is left written down because it changes the design.** The request arrived as
"you already have `transient` and `serviceFault`, you are missing the other
half". Measured 2026-08-30 in this tree: `grep -rEni "transient|servicefault"
src/` = **0**, same as `recovery`. So `recovery` does not arrive as the second
half of a pair: it arrives alone. That was NOT fixed by inflating the field —
writing "retry" here would turn `recovery` into a boolean written out in prose,
which is exactly the error EM points at — but by leaving the hole declared and
reported.

**The rule, and it is the important half of the task:**

    If a REAL recovery path exists, it gets written.
    If it does NOT, the field is `None`. **Inventing a recovery that does not
    work is WORSE than not having the field**, because it sends the caller off to
    do something useless with confidence. An empty `recovery` is honest.

And a recovery **names SOMETHING ELSE to do**: another route (the free one that
answers the neighbouring question), another field of the error itself, another
constructor parameter, or the condition on the caller's side that has to be
fixed. "Retry" is not a recovery: it is a boolean.

**FREE TEXT AND NOT AN ENUM, and the argument is that the enum ALREADY EXISTS.**
The house has one pattern for this and it lives in two places: the service's
`caveats[].code` / `caveats[].text`, and this module's `kind` / message. **You
branch on the code, you read the text.** `recovery` is the TEXT half of a pair
whose CODE half is already `kind`: a `RecoveryCode` would be a second
discriminator in 1:1 correspondence with `kind`, that is, a duplicate key that
can only fall out of sync. And the content of a recovery is instructions, which
have no finite vocabulary; what does have one is the taxonomy, and it is already
typed.

**WHERE IT LIVES, AND WHY IT IS NEVER RE-TYPED ANYWHERE:** each class declares
its own **in its body**, as a constant. No other surface — not a
`RECOVERY_BY_KIND` dict, not the test, not the README — writes those texts again:
a second copy is the one that rots. The test pins the ANCHORS of each piece of
advice (that a 402's names `wallet()`, that the rail's names the allowlisting),
not the wording.

🔴 **NO `recovery` INTERPOLATES ANYTHING — it is the SDK version of the service's
`_redact`.** The index repo has the guard on the other side
(`describenet/chain/rpc.py::_redact`, which strips the provider URL from anything
about to be raised or logged, because the API key lives in the path). Here the
equivalent is structural rather than defensive: **we write the text and it is a
class constant**, so it cannot drag along an RPC URL with its key, a DSN, or the
raw message of somebody else's exception. EM warned that same day that an
unclassified error "can carry an RPC URL with its API key inside" and that they
added a test with a fake secret that fails if it leaks; ours is in
`tests/test_recovery.py` and does the same, with the extra guard that `recovery`
has to remain the SAME object as the class attribute (a `property` that
interpolates turns it red).

⚠️ **What this guard does NOT cover, said before anyone assumes it:** the MESSAGE
of `PartnerSigningError` does interpolate the signer's exception
(`f"… ({type(exc).__name__}: {exc})"`, `partner.py:249`), and that is deliberate
— naming the real cause was the lesson of `chain_name_for` — but it means a KMS
client that puts its endpoint in the text publishes it that way. It is the
message, not `recovery`, and it is left annotated rather than papered over.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The `kind` `execution-market` uses for the same bucket. Published so a
#: migration can compare against both without guessing.
HTTP_5XX_LEGACY_KIND = "http_5xx"


class DescribeError(Exception):
    """Base of every transport or protocol failure against describe.

    `kind` is the contract: you branch on it (or on the subclass), **never on the
    message text**. Messages get rewritten; `kind`s do not.

    The same goes for `payment_sent`: branch on the ATTRIBUTE, never on searching
    for "after paying" in the message.
    """

    kind: str = "unreachable"

    #: Was this exception raised AFTER the signed payment credential left the
    #: process?
    #:
    #: 🔴 What it proves and what it does NOT prove, because the difference is
    #: what matters:
    #:
    #:   * `True` proves an EIP-3009 authorization **signed and dispatched** for
    #:     the amount in `payment["amount_usd"]` exists. Settlement MAY have
    #:     happened. Reconciling is the caller's job.
    #:   * `True` does **NOT** prove the USDC moved. That is only proved by a
    #:     present `payment["transaction_hash"]`, and that hash only arrives if
    #:     the server managed to answer with its `X-Payment-Receipt` header.
    #:   * `False` IS a strong claim in the other direction: nothing was signed,
    #:     no credential left, not a cent was spent. It is the value of every free
    #:     route and of the stretch before a metered route's 402.
    payment_sent: bool = False

    #: Detail of that credential when `payment_sent` is `True`; `None` otherwise.
    #: Keys: `amount_usd` (what was signed, as a STRING — it is money),
    #: `network`, `resource` (the route) and `transaction_hash` (the
    #: `X-Payment-Receipt`, or `None` if the server never answered).
    payment: Optional[Dict[str, Any]] = None

    #: WHAT TO DO INSTEAD. Text for a human, stable, **a class constant**.
    #:
    #: 🔴 `None` means *there is no real recovery path for this failure*, and it
    #: is a decision taken rather than an oversight: every subclass declares its
    #: own in its own body — including when it is `None` — and there is a test
    #: that turns red if a new class does not declare one. See the "`recovery`"
    #: block of the header for the why of the free text, the honest empty and the
    #: leak guard.
    #:
    #: It is READ, not branched on: for branching there is `kind`.
    recovery: Optional[str] = None


class DescribeTimeout(DescribeError):
    """The request outlived the client timeout.

    That includes the provider's cold start: their Lambda measured **15,2 s** of
    cold start (INC-2026-08-19, cited in EM's `client.py:19-23`). A short timeout
    turns every cold start into a fake "there is no data" — which is why this
    SDK's default is 30 s (R7) and not 8 s, a value that already broke a real
    integration.

    🔴 **`recovery` is `None` ON PURPOSE, and this is THE case that justifies the
    field being allowed to be empty.** It is the SDK's most common failure and
    even so there is no OTHER thing to do: the same question against the same
    index has no second door. The two exits that look like recoveries are not,
    and both were discarded with their measurement:

      * *"raise the timeout"* — the ceiling is not ours: the provider's API
        Gateway cuts at **29 s** and the default is already 30. Asking for more is
        asking thin air (see R7 in `client.py`).
      * *"retry"* — that is a boolean, not a recovery. Writing it here would be
        exactly the error EM points at. And this SDK does **not** publish that
        boolean today: `transient` does not exist (measured 2026-08-30, see the
        header), so the hole is left declared and reported instead of filled with
        a useless sentence.

    `tests/test_recovery.py` pins this `None`: it is the only thing stopping
    somebody from "completing the table" out of tidiness.
    """

    kind = "timeout"
    recovery = None


class DescribeHTTPError(DescribeError):
    """describe answered with a non-2xx status.

    The 422 (invalid address) and the 429 (shared rate limit — the live budget is
    stated by the `RateLimit-Policy` header, not by a number copied in here) land
    together with the 5xx: every consumer treats them the same — "no usable
    answer" — and what you read to tell them apart is `status_code`, not the
    bucket.

    Its `recovery` sends you to `status_code` and does not apologise for it: **the
    bucket merges three causes that are fixed differently**, so the honest
    recovery is to say where they split rather than give an average piece of
    advice that serves none of them.
    """

    kind = "http_error"

    recovery = (
        "Branch on `status_code`, not on this `kind`: causes that are fixed "
        "differently land here. A 4xx is YOUR request and the body names the "
        "field (a 422 on the address is fixed on the caller's side). A 429 is the "
        "limit SHARED with the other consumers: the live budget travels in the "
        "`RateLimit-Policy` header of that same response, and it is spread with "
        "`jitter=` and attributed with `product=`, never by asking faster. A 5xx "
        "is the service's and there is nothing to fix on this side."
    )

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class DescribeUnreachable(DescribeError):
    """Transport failure before any HTTP status (DNS, connect, reset).

    Its `recovery` pushes you to look at the caller's configuration before the
    index, and that is not a reflex: here **nothing reached describe**, so the
    index is the one thing that cannot yet be blamed. A mistyped host never gets
    as far as a 404 — no server answers it — and comes out exactly here.
    """

    kind = "unreachable"

    recovery = (
        "Nothing reached describe (or its answer did not reach you), so before "
        "blaming the index check your own side: that `base_url` is "
        "https://api.describe.net — a mistyped host does not get as far as a 404, "
        "it gives this — and that your process's egress (proxy, DNS, firewall) "
        "lets you out. With that ruled out, there is nothing left to fix on this "
        "side."
    )


class DescribeUnparseable(DescribeError):
    """The answer arrived but could not be read into the promised shape.

    This is protocol, not data: a `chains: []` is a valid shape that says "there
    is nothing here", and it does **not** come through here. Only a body that is
    not JSON, or one missing the essential key the route promises in its schema,
    does.

    Its `recovery` names the suspect almost nobody checks first: **an
    intermediary**. The service repo already has it written on the other side —
    *"a gateway erroring with an HTML page still returns 200 sometimes"*
    (`describenet/chain/rpc.py`) — and that case looks exactly like this.
    """

    kind = "unparseable"

    recovery = (
        "A body arrived but it is not the one the route promises: suspect an "
        "intermediary before the index — a corporate proxy or a captive portal "
        "answers 200 with an HTML page and it looks identical to this. Check "
        "`base_url` and that there is no gateway in the middle. If the body really "
        "did come from describe, it is a service bug and retrying does not fix it: "
        "report it."
    )


class DescribeMalformedHash(DescribeError):
    """A hash field arrived carrying something that **is not shaped like a hash**.

    Contributed by **KarmaKadabra** (`#agents`, 2026-08-30), out of the finding
    they call *"el 200 sin tx"* ("the 200 with no tx"): *"si nosotros no
    chequeáramos el tx, habríamos contado 14 ratings que no existen"* — [in
    English] "if we did not check the tx, we would have counted 14 ratings that do
    not exist". A 200 that did not do the thing is worse than a 503, because the
    client takes it for good.

    🔴 **THIS EXCEPTION IS NEVER RAISED. Do not write an `except` for it.** It
    travels only as an argument to `on_error`, and it exists as a class for one
    mechanical reason: the observation channel is typed
    `Callable[[DescribeError], None]`, so to reuse the channel the consumer is
    ALREADY watching — which is what KK asked for — the fact has to BE a
    `DescribeError`. Branch on `kind == "malformed_hash"` or on `isinstance`,
    never on a `try/except` that will never fire.

    ⚠️ And there is a tension with this very taxonomy that is declared rather than
    papered over: this module's header explains that `PartialIndex` was **not
    ported** from Execution Market precisely because "publishing an exception the
    SDK never raises invites a dead `except`". The criterion separating the two
    cases is not "is it raised or not" but **what the class is FOR**:
    `PartialIndex` existed so somebody could catch it and nobody was going to
    throw it; this one exists to travel down an already-typed channel, and its
    docstring shouts as much in its first line. If `on_error` ever accepts
    something wider than a `DescribeError`, this class stops being necessary.

    Why the failure does NOT take the read down: the rest of the response may be
    perfectly useful, and breaking an entire reputation breakdown over an
    accessory field would be worse than the bug being hunted. The typed field is
    left `None` — so nobody builds an explorer link out of garbage — and the raw
    value survives in the model's `raw`, which is where it gets investigated.

    🔴 **Absent and malformed are NOT the same thing, and the model keeps them
    apart** (R1 applied one level further down):

        rating.tx_hash is None and `malformed_hashes` empty → IT DID NOT COME
        rating.tx_hash is None and "tx_hash" in malformed   → GARBAGE CAME

    `fields` carries the location of each one, with an index when it is inside a
    list: `["ratings[3].tx_hash", "snapshot.inputs_digest"]`.
    """

    kind = "malformed_hash"

    #: The only `recovery` that speaks to an error that is NEVER raised, and that
    #: is why it is the most concrete of all: the response is already in the
    #: consumer's hands, so there is something to do NOW and something NOT to do.
    recovery = (
        "The typed field was left `None` on purpose and the raw value survives in "
        "the model's `raw`; `fields` says which path each one is at. 🔴 Do not "
        "count it as a transaction and do not build an explorer link out of it. "
        "And do NOT discard the response: the rest arrived fine and you already "
        "have all of it."
    )

    def __init__(self, message: str, fields: Optional[List[str]] = None) -> None:
        super().__init__(message)
        #: Paths of the fields that arrived malformed, in order of appearance.
        self.fields: List[str] = list(fields or [])


class PaymentRequiredError(DescribeError):
    """A metered route answered 402 and this client has nothing to pay with.

    **Not swallowed by the fail-open.** A missing `payer` is the caller's
    configuration, not an index outage: degrading it to `None` would hide a
    programming error behind the same value that means "there is no evidence".

    `challenge` carries the raw 402 exactly as it arrived — `amount`, `token`,
    `recipient`, `accepts[]`, `free_preview`, `pricing`. It is kept whole on
    purpose: the published guide says to take the values from the challenge and
    **never from a cached table** (docs.describe.net, "Paying with x402", step 1),
    so the SDK does not keep a summary of its own.
    """

    kind = "payment_required"

    #: 🔴 The most useful recovery this SDK can offer, and that is why it names
    #: the FREE door and not just the configuration fix: half the charges on this
    #: index did not have to be paid. The 402 itself says so — its `free_preview`
    #: exists precisely so nobody pays blind for an empty wallet — and that is why
    #: the text sends you to read it from the stored `challenge` instead of
    #: repeating a route here that would rot
    #: (`describenet/pricing.py::free_preview`: `{endpoint, gives}`, and the agent
    #: branch points at `/search/{query}`, not at the wallet one).
    recovery = (
        "This is a metered route and this client has nothing to pay with: "
        "configure `payer=`, or `partner=` if describe allowlisted your wallet. "
        "But look first at whether the free door is enough: `wallet()` answers the "
        "global score without paying or signing, and the raw 402 names the free "
        "door for THIS subject in `challenge['free_preview']['endpoint']`."
    )

    def __init__(
        self,
        message: str,
        challenge: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.challenge: Dict[str, Any] = challenge or {}

    @property
    def price_usd(self) -> Optional[str]:
        """The price as a STRING, exactly as the server sent it.

        A string and not a float on purpose: the price is money and a
        `float("0.01")` is no longer 0.01. Whoever is going to sign converts it to
        `Decimal`, never to `float`.
        """
        value = self.challenge.get("price_usd") or self.challenge.get("amount")
        return str(value) if value is not None else None


class PartnerSigningError(DescribeError):
    """The partner rail could not sign. **No request went out.**

    This is the caller's configuration — the extra not installed, a signer that
    raises, a KMS that is down, a signature that is not hex — so the fail-open
    does not swallow it, for the same reason as `PaymentRequiredError`: degrading
    it to `None` would turn "your free rail is broken" into "this wallet has no
    reputation".

    🔴 **`payment_sent` is `False` and that is a strong claim, not a default**:
    the rail's signature happens BEFORE the first request, so when this comes out
    nothing was asked, no 402 was received, no EIP-3009 authorization was signed
    and not a cent moved. It is the good half of "it raises, it does not degrade".

    `wallet` carries the signer's address if it could be read (`None` if the
    failure was precisely in asking for it). It is public by design: it is the one
    that goes on the service's allowlist.
    """

    kind = "partner_signing"

    #: The fact that makes this recovery useful is the one nobody works out alone:
    #: a broken rail **blocks nothing that is free**. The service decides "free"
    #: BEFORE looking at the partner (`describenet/paywall.py:772`, against :794),
    #: so the free routes are never signed and keep working with a dead signer.
    recovery = (
        "The rail never got to sign, so no request went out and there is nothing "
        "to reconcile: fix the signer, or install the extra with "
        "`pip install uvd-describe-sdk[partner]`. In the meantime the FREE routes "
        "keep working — `wallet()`, `leaderboard()` and `health()` are never "
        "signed. And if you really do want to pay, build the client WITHOUT "
        "`partner=`: it does not fall through to paying on its own, on purpose."
    )

    def __init__(self, message: str, wallet: Optional[str] = None) -> None:
        super().__init__(message)
        self.wallet = wallet


class PartnerRejectedError(PaymentRequiredError):
    """You signed as a partner and describe **asked for money anyway**. Nothing
    was paid.

    It inherits from `PaymentRequiredError` on purpose, and the inheritance says
    something true: both cases are "a 402 arrived and this client did not put in a
    cent". A consumer who already wrote `except PaymentRequiredError` catches it
    without changing a line, and inherits `challenge` and `price_usd` — which are
    worth double here, because they say **how much the broken rail was going to
    cost you**.

    🔴 WHY THIS RAISES INSTEAD OF PAYING, which is the whole decision of partner
    mode:

        A partner with `payer=` configured and the rail down has one obvious,
        silent path: pay. And there the bug is never seen — the answer arrives
        anyway, the code works, and the USDC invoice shows up weeks later. It is
        the same family as the service's gate: *a bug here breaks nothing, raises
        no error, and gives away / spends the product.*

    So a 402 with `partner=` configured is a FAILURE and not a billing signal.
    **The `payer` is not used even if it is there**, and the message says so
    loudly. Whoever really wants to pay builds a client without `partner=`: it is
    one line, it is explicit, and it stays written in their code.

    The four causes, all fail-closed on the service's side:
      * the wallet is not in the allowlist (describe does the allowlisting);
      * it was signed against another host (your `base_url` is not
        `api.describe.net`);
      * the signer's clock drifted more than 300 s (the gate's window);
      * the signature did not cover the URL that went out (query included).

    `wallet` is the address it was signed with: it is the first thing to look at
    and what to quote to describe for the allowlisting.
    """

    kind = "partner_rejected"

    #: 🔴 The textbook case of EM's contribution: it is TERMINAL. Retrying against
    #: a wallet that is not in the allowlist is the describe version of their 317
    #: hours against a closed window — describe does the allowlisting and no amount
    #: of requests produces it.
    recovery = (
        "describe does the allowlisting, not you: ask them to add the wallet in "
        "`exc.wallet` to their allowlist — retrying will not produce it. Before "
        "that, rule out the other three causes: `base_url` has to be "
        "https://api.describe.net, the signer's clock has to fit a 300 s window, "
        "and the signature has to cover the query you sent. In the meantime "
        "`wallet()` is still free and unsigned; if you want to pay for real, build "
        "the client WITHOUT `partner=`."
    )

    def __init__(
        self,
        message: str,
        wallet: str,
        challenge: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, challenge)
        self.wallet = wallet


class DoNotPayError(DescribeError):
    """The 402 asks to pay an address that is NOT the pinned treasury.

    **This is `DO_NOT_PAY`, not a retry, and the fail-open does not swallow it.**
    Rule 4 of `F0-describe-sdk.md:192-205` and step 2 of the published guide: *"If
    the challenge names another address, do not pay: either it did not come from
    describe, or the treasury changed and the server did not find out."*

    Retrying here is the worst thing a client can do: it turns a diversion of
    funds into a diversion of funds with retries.
    """

    kind = "do_not_pay"

    #: The bucket merges five sites in `payment.py` that share ONE fact — all five
    #: raise **before** `create_authorization()`, that is, without signing — and
    #: they are told apart by `expected`/`offered`. The recovery states the common
    #: fact first (nothing was spent) and then the fork, because an average piece
    #: of advice here would be the worst of all: "pay some other way" against a
    #: diverted address is the line this error exists in order not to write.
    recovery = (
        "🔴 Nothing was signed and nothing was spent, and this is NOT retried: "
        "retrying a diversion of funds only repeats it. Compare `expected` with "
        "`offered` — if what does not match is the NETWORK, pick one of the ones "
        "describe offers (`pay_network=`) or install the x402 extra; if what does "
        "not match is the ADDRESS, do not pay by any route and tell describe. "
        "`wallet()` still answers the global score for free."
    )

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
    """Mark an exception as post-signature. Only the client calls this.

    It mutates the object instead of wrapping it in a new exception on purpose: an
    `except DescribeTimeout` already written in a consumer's code has to keep
    catching it. Changing the CLASS to add a datum is breaking everybody's
    `except` over a label.

    And the warning is written **into the message too**, not just the attribute,
    because whoever opens a traceback in a log at 3 AM does not have the object at
    hand — they have a line of text. The attribute is for branching; the text is
    for reading. (It is the same pair as the service's `caveats[].code` /
    `caveats[].text`: decide on the code, read the text.)
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
            f" THERE IS A RECEIPT: X-Payment-Receipt={transaction_hash} — "
            "settlement happened, the spend is confirmed and it is citable."
        )
    else:
        prueba = (
            " NO `X-Payment-Receipt` arrived, so there is no proof either way: "
            "this SDK cannot claim it settled nor that it did not."
        )
    base = str(exc.args[0]) if exc.args else str(exc)
    exc.args = (
        f"{base} — 🔴 THE PAYMENT CREDENTIAL WAS ALREADY SIGNED AND DISPATCHED "
        f"({amount_usd or '?'} USD on {network}, {resource}): this is NOT «I could "
        f"not ask», it is «I may have paid and I do not know what I got»."
        f"{prueba} Reconcile before retrying: the nonce is consumed at settlement, "
        "so resending this credential does not pay again and asking for a NEW "
        "challenge charges you once more.",
    )
