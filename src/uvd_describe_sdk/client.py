"""`DescribeClient` — the synchronous client. R4, R5 and R7 live here.

Synchronous on purpose: the three measured consumers of this API in the ecosystem
either are synchronous or can be without pain, and a duplicated async API is the
shortest path to the two branches diverging. (The one real exception is declared
as a risk in the README: Execution Market's client is `async` and to adopt this
it would have to wrap it in a thread. That is an open question, not an
oversight.)

════════════════════════════════════════════════════════════════════════════
🔴 R5 — THE FAIL-OPEN, AND WHY IT IS THE EASIEST THING TO GET WRONG
════════════════════════════════════════════════════════════════════════════
Saul asked for it verbatim on 2026-08-28: *«pon un fallback si es que describe
está caído»* — [in English] "put a fallback in if describe is down". The default
is `fail_open=True`.

But a naive fail-open **breaks R1**. Look the trap in the face:

    wallet() returns None  ──> is it "describe is down"?
                          └──> or "this wallet has no reputation"?

If both things returned the same value and nothing else, the fail-open would have
manufactured exactly the confusion R1 exists to prevent — and it is not
hypothetical: it cost KarmaKadabra a wrong report on 2026-08-28, in the gate that
decides who to trade with.

**How it is solved here, with two mechanisms and not with a comment:**

1. **The distinction lives in the TYPE, not in the value.** A wallet the index
   really could read comes back as a `WalletReputation` — even with not a single
   rating, even if it is not registered. `None` means **one single thing**: *there
   was no answer*. Never *there is no reputation*.

       result is None                      → it could not be read
       result.has_identity is False        → not registered
       result.global_score is None         → registered, unrated

2. **No `None` leaves unobserved.** Every path that returns `None` goes through
   `_observe()` first, which calls `on_error` and logs at WARNING. A silent
   fail-open turns "describe is down" into "this wallet has no reputation" **in
   the logs**, which is where you investigate. That is why the default is not "do
   nothing": it is to log.
   `tests/test_r5_fail_open.py` pins it, and its discriminating test mounts the
   GOOD state — a real wallet with no ratings — to demand an object and ZERO
   observations: without it, "no ratings ⇒ return None" would pass green.

**What the fail-open never covers:** `PaymentRequiredError` and `DoNotPayError`.
The fail-open is for the AVAILABILITY of the index, not for the caller's
configuration nor for a diversion of funds.

════════════════════════════════════════════════════════════════════════════
WHICH METHOD IS "NULLABLE" — THE LINE IS WHETHER THERE WAS MONEY IN FLIGHT
════════════════════════════════════════════════════════════════════════════
**R5 corrected, 2026-08-30. It is core-contract canon and both SDKs — Python and
TypeScript — implement it THE SAME.**

    FREE ROUTES       wallet() · leaderboard() · health()
                      On a SERVICE failure (timeout, unreachable, 5xx,
                      unreadable body) with `fail_open=True` they return `None`,
                      ALWAYS observed (`on_error` + WARNING).
                      🔴 Never `[]`. An empty list reads as "the index is empty",
                      which is a FALSE claim about the world. `None` reads as "I
                      could not ask".

    METERED ROUTES    wallet_breakdown() · agent()
                      THEY ALWAYS RAISE. Even with an explicit `fail_open=True`.

**Why the metered ones do not, and the reason is money and not symmetry:**
between signing the x402 envelope and receiving the answer there is a window in
which the USDC has already moved. Returning `None` there hides from the caller
that they spent — it is a spent credential with no receipt, and nothing
distinguishes "I paid and it fell over" from "there was nothing to fetch". A loud
failure after paying is recoverable (you retry, you log, you claim); a silent
`None` is not. So it is not a caller preference but a **property of the method**:
an availability flag cannot buy the right to swallow a receipt. And so that
"loud" is also informative, the exception from the paid stretch comes out marked
with `payment_sent=True` (see `errors.py`).

**Why the free ones do, and not just `wallet()`:** `leaderboard()` and `health()`
are free. A loud failure there forces every consumer to write their own
`try/except` for something the SDK already knows how to do — which is precisely
the duplication this SDK came to erase.

🔴 **PARTNER MODE DOES NOT MOVE THIS LINE ONE MILLIMETRE** (2026-08-30). That
`wallet_breakdown()` comes out free for you over the rail does not turn it into a
free route: it still RAISES on any failure, explicit `fail_open=True` included.
The criterion was never the price you paid but **whether there was money in
flight**, and with `payer=` configured a broken rail means there is — which is why
a broken rail also raises (`PartnerRejectedError`) instead of letting you pay. A
partner is someone who spends $0 for as long as the rail holds, not someone with
a different contract: the two metered routes are in the same place of the table
before and after this change, and `test_partner_riel_gratis.py` asserts it by
running the same R5 cases with a partner configured.

────────────────────────────────────────────────────────────────────────────
WHAT THIS BLOCK SAID UNTIL 2026-08-30, AND WHAT SURVIVED OF IT
────────────────────────────────────────────────────────────────────────────
It is left written, not deleted: it is the house convention, and here it is
applied to this file's own reasoning.

The old version said the v0.1 contract's type table marked `| null` **only** on
`wallet()`, that this was followed "to the letter", and defended it like this:
`wallet()` gets drawn next to a name on a profile and there a gap is an acceptable
degraded render, whereas *"`leaderboard()` and `health()` are operational reads:
whoever asks about the whole index or about its state wants to know that it
failed, not to receive an empty list that looks like an empty index"*. And it
marked the scope of R5 as a REPORTED and unresolved ambiguity.

**What survived, and it is not little:** the second half of that sentence. "An
empty list looks like an empty index" was correct, it was addressed, and that is
why the corrected contract says explicitly **NEVER `[]`**. `None` is not an empty
list and cannot be confused with an empty index: the distinction still lives in
the TYPE, just as in `wallet()`.

**What fell:** the first half — "operational reads" was not the criterion. It
named an intuition about who is asking, not a measurable consequence of getting
it wrong. The real criterion is **whether there was money in flight**, and that
also disproves the premise: following "the table to the letter" left
`walletBreakdown` and `agent` without `| null` by accident of the table and not by
principle — the TypeScript twin read the other half of the contract (rule R5,
which did not narrow it) and ended up doing **fail-open on the metered routes**,
with `walletBreakdown()` returning `null` after a post-settlement timeout. That is
the bug this correction exists to make impossible in both languages: two
reasonable readings of the same contract, and one of them cost money.

**And the ambiguity is no longer open:** the scope of R5 was resolved on
2026-08-30 with the rule above. There is no open question for Saul here.

════════════════════════════════════════════════════════════════════════════
R7 — a 30 s timeout, and the number is reasoned (not picked)
════════════════════════════════════════════════════════════════════════════
It is the only one of the three consumers that reached its timeout with a
measurement behind it (`execution-market/.../client.py:19-23`, INC-2026-08-19):

  * the provider Lambda's cold start measured **15,2 s**;
  * their API Gateway cuts at **29 s** — asking for more would be asking thin air;
  * and 30 is **deliberately different** from the facilitator's 45 s, "so the two
    clocks never race": two identical timeouts expire in the same second and
    there is no way to know which one failed.

KarmaKadabra uses 25 s with the same cold-start measurement and left written that
their old default of 12 s "turned every cold start into an unreadable". 30 wins
because it covers the cold start with margin and ties with nothing.

════════════════════════════════════════════════════════════════════════════
🔴 JITTER SHIPS ON — AND HERE IS THE TRADE-OFF, NOT A PREFERENCE
════════════════════════════════════════════════════════════════════════════
Contributed by **KarmaKadabra** (`#agents`, **2026-08-30**), who run a fleet of 27
agents. Their measurement, verbatim:

    *«27 agentes despiertan al MISMO tiempo por EventBridge y pegan simultáneo
    contra su límite de rps COMPARTIDO con los otros consumidores. Sin jitter, un
    enjambre es un DDoS educado.»*

    [translation] "27 agents wake at the SAME time on EventBridge and hit their
    rps limit — SHARED with the other consumers — simultaneously. Without jitter,
    a swarm is a polite DDoS."

Their implementation is `random.uniform(0, 0.4)` before every read of the index
(`karmakadabra/lib/reputation_scan.py:120-123`). The default here **is that same
0.4**, and the number is inherited with its provenance instead of inventing a new
one nobody measured.

**Why on and not opt-in, which was the real decision:** both sides are real and
were weighed.

    FOR off   A library that sleeps without being asked is surprising. Whoever
              writes a one-off script pays 0.2 s of median for a problem they do
              not have: jitter does nothing for a single caller, it only helps
              when there are many.

    FOR on    Whoever writes that one-off script **does not find out the problem
              exists** until they have a fleet, and by then they have already hit
              somebody else's limit.

What breaks the tie is not which one costs more but **who pays**. The cost of
having it on is paid by whoever chose the default, and it is 0.2 s of median. The
cost of having it off is paid by **third parties**: the limit is shared with the
other two consumers of the ecosystem, so an undispersed swarm eats the budget of
MeshRelay and Execution Market, who chose nothing. A default whose damage lands on
somebody who did not choose it is not a default, it is a trap — and it is the same
criterion R5 uses to decide what the fail-open swallows.

Turning it off is one explicit line and it stays written in the code of whoever
writes it:

    DescribeClient(product="my-script", jitter=0)

**It only disperses, it never yields.** The jitter goes before the request and
NOTHING ELSE: it is not a backoff. They are two different things and confusing
them costs money — jitter disperses a herd that has not asked for anything yet;
backoff yields to a service that has already said no. This SDK does not retry (see
`_paid`), so there is no backoff to write. And there is one place where jitter is
**explicitly forbidden**: the second stretch of the 402 dance, the one after the
signature. Sleeping between signing an EIP-3009 authorization and dispatching it
burns settlement window (`maxTimeoutSeconds: 120` in the challenge) in exchange for
ZERO dispersion — the herd already dispersed in the first stretch. See
`_request(disperse=...)`.

🔴 **The randomness is NOT cryptographic, and that is deliberate**: this is
dispersion, not a secret. `secrets` here would be cargo cult — slower and buying
nothing.

About the rate-limit number: it is not typed into this SDK. The live authority is
the **`RateLimit-Policy`** header the API sends on EVERY response. Measured today
(2026-08-30) it reads `50;w=1;burst=40`; KK quoted 20 because the old
documentation said so and the limit was raised on 2026-08-28. That drift is
exactly the reason for pointing at the header instead of copying the number into a
fourth file.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar
from urllib.parse import quote

import httpx

from .badge import badge_url as _badge_url
from .errors import (
    DescribeError,
    DescribeHTTPError,
    DescribeMalformedHash,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
    PartnerRejectedError,
    PaymentRequiredError,
    mark_payment_sent,
)
from .hashes import (
    SETTLEMENT_PENDING,
    looks_like_onchain_id,
    looks_like_settlement_receipt,
)
from .models import (
    AgentReputation,
    Breakdown,
    IndexHealth,
    LeaderboardRow,
    PaymentReceipt,
    WalletReputation,
    malformed_hash_report,
    parse_agent_reputation,
    parse_breakdown,
    parse_health,
    parse_leaderboard,
    parse_wallet_reputation,
)
from .partner import PartnerSignature, PartnerSigner, sign_partner_headers
from .payment import TREASURY_EVM, Payer, build_payment_header
from .version import default_user_agent

DEFAULT_BASE_URL = "https://api.describe.net"

#: R7. See the module header: 15,2 s of measured cold start, a 29 s API Gateway
#: ceiling, and different from the facilitator's 45 s on purpose.
DEFAULT_TIMEOUT_S = 30.0

#: The network payment goes over when the caller does not say otherwise. `base`
#: is the first of `supportedChains` in the live challenge (2026-08-30) and the
#: cheapest of the six. There is no way to guess where the caller holds funds: an
#: explicit default is chosen and documented, instead of trying the six in turn —
#: trying would spend credentials.
DEFAULT_PAY_NETWORK = "base"

#: Ceiling of the dispersion sleep, in SECONDS. It is the value KarmaKadabra
#: measures and runs in production with 27 agents (`reputation_scan.py:123`),
#: inherited as-is. See the "JITTER SHIPS ON" block in the module header for the
#: full trade-off; `jitter=0` turns it off.
DEFAULT_JITTER_S = 0.4

logger = logging.getLogger("uvd_describe_sdk")

#: 🔴 Generador PROPIO, no el global de `random`, y no es un detalle de estilo.
#: Una flota corre N veces la misma imagen, y basta con que el proceso llame a
#: `random.seed(0)` —para hacer reproducible cualquier otra cosa— para que los 27
#: agentes duerman EXACTAMENTE lo mismo: dispersión cero, o sea el bug que el
#: jitter existe para evitar, reintroducido por una línea que ni lo menciona. Una
#: instancia propia sin semilla se siembra del SO y es inmune a eso.
#: NO es criptográfica a propósito: es dispersión, no un secreto.
_RNG = random.Random()


def _jitter_seconds(jitter: float) -> float:
    """How long to sleep before a request. Pure, and testable without sleeping.

    `<= 0` disables it — that is the explicit opt-out — and with that a negative
    value passed by mistake cannot turn into a `sleep` that raises either.
    """
    if jitter <= 0:
        return 0.0
    return _RNG.uniform(0.0, jitter)

#: Called with the exception being swallowed. See `_observe`.
ErrorObserver = Callable[[DescribeError], None]

#: El modelo que devuelve una ruta paga. Existe para que `_paid` sea una sola
#: función y no dos copias: donde vive la marca de «esto falló DESPUÉS de
#: pagar» no puede haber dos versiones que se desincronicen.
_Parsed = TypeVar("_Parsed")


class DescribeClient:
    """Synchronous describe client. Configured by constructor, zero globals.

    Everything is passed through the constructor and nothing is read from an env
    var inside: that is what makes the module testable without an environment and
    embeddable in any process. (EM's reference calls this their "SDK-extractability
    contract", and it is why their client could be lifted into this repo as-is.)

        with DescribeClient(product="karmakadabra") as describe:
            rep = describe.wallet("0x97cd…0996")
            if rep is None:
                ...  # the index did not answer — NOT "has no reputation"
            elif not rep.has_identity:
                ...  # not registered
            elif rep.global_score is None:
                ...  # registered and not yet rated
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
        jitter: float = DEFAULT_JITTER_S,
        payer: Optional[Payer] = None,
        pay_network: str = DEFAULT_PAY_NETWORK,
        treasury: str = TREASURY_EVM,
        partner: Optional[PartnerSigner] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """
        Args:
            product: who is consuming (`"karmakadabra"`, `"meshrelay"`…). Goes
                into the User-Agent. Pass it: the rate limit is SHARED across
                every consumer and there is no per-partner bucket, so without
                attribution nobody can know who spent it. An anonymous request
                against a shared limit is free-riding. (The live number is sent by
                the API in `RateLimit-Policy` on every response; it is not copied
                in here — see `jitter`.)
            fail_open: default `True`. Covers the **FREE** routes — `wallet()`,
                `leaderboard()`, `health()` — which on a service failure return
                `None` (never `[]`) and always observed. **It does not cover the
                metered ones**: `wallet_breakdown()` and `agent()` raise even if
                `True` is passed here, because a failure swallowed after signing
                is a spent credential with no receipt. See the module header.
            on_error: called with the exception **every time** the fail-open
                swallows one. If it is not passed, it is still logged at WARNING.
                There is no silent mode. The `DescribeMalformedHash` of a hash
                field that arrived with garbage also travels through here —
                without anything being raised.
            jitter: seconds of random dispersion BEFORE every request. Default
                `0.4`, **on**, inherited from KarmaKadabra's measurement with 27
                agents. `jitter=0` turns it off. It is not a backoff and it is not
                applied to the stretch after a payment signature. See the "JITTER
                SHIPS ON" block in the header.
            payer: whoever signs the 402. Only needed for the metered routes. See
                `payment.Payer`.
            treasury: the address payment is accepted to. Configurable for your
                own deployment of the index, **not** to disable the check: if the
                challenge names another one, it is `DO_NOT_PAY`.
            partner: the signer of the **partner rail** (`partner.PartnerSigner`).
                If describe allowlisted your wallet, the metered routes stop
                charging you. 🔴 It is an OBJECT that signs: this SDK does not
                touch your key, does not read it from an env var and does not
                store it. See `partner.py`. With this set, a 402 on a metered
                route is a **rail failure** and `PartnerRejectedError` is raised:
                **the `payer` is not used even if it is there**, because a broken
                rail that pays on its own is an invoice that shows up weeks later.
            transport: for the tests (`httpx.MockTransport`). It is the seam that
                lets the whole suite run **without network**.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._user_agent = user_agent or default_user_agent(product)
        self._fail_open = fail_open
        self._on_error = on_error
        self._jitter = jitter
        self._payer = payer
        self._pay_network = pay_network
        self._treasury = treasury
        self._partner = partner
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
        """The effective UA. Published so a test can assert it instead of
        trusting that it was assembled correctly."""
        return self._user_agent

    @property
    def is_partner(self) -> bool:
        """Will this client sign the metered routes as a partner?

        It says whether a signer is CONFIGURED, not whether describe will exempt
        you — that is decided by the service's allowlist and is only knowable by
        asking. It exists so a startup path can assert "I am going out on the
        rail" instead of finding out from the invoice. It does not call the
        signer: a `get_address()` may be a round trip to a KMS, and a property
        should not cost a request.
        """
        return self._partner is not None

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------

    def _observe(self, exc: DescribeError, context: str) -> None:
        """Every `None` this client returns goes through here first.

        It is the mechanism that makes the fail-open **observable**. If the
        caller's observer blows up, it is logged and we carry on: a broken
        callback cannot turn a planned degradation into a crash.
        """
        logger.warning(
            "describe did not answer (%s at %s): %s — returning None, "
            "which does NOT mean «no reputation»",
            exc.kind,
            context,
            exc,
        )
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001
                logger.exception("the caller's on_error raised an exception")

    def _notify(self, exc: DescribeError) -> None:
        """Announce without the fail-open text. Same channel, different fact.

        `_observe` closes its message with "returning None, which does NOT mean no
        reputation", and here that would be a lie: the answer arrived and is
        returned whole. Sharing the channel (which is what KK asked for: *«el
        contrato debería decirlo»* — "the contract should say so" — and down the
        channel the consumer is already watching) is not sharing the message: a
        notice that mis-describes what happened sends you to investigate the wrong
        place, which is the failure this repo hunts.
        """
        logger.warning("describe: %s", exc)
        if self._on_error is not None:
            try:
                self._on_error(exc)
            except Exception:  # noqa: BLE001
                logger.exception("the caller's on_error raised an exception")

    def _check_hashes(self, resultado: Any, path: str) -> None:
        """Observe the hash fields that arrived with garbage. **It never raises.**

        Contributed by **KarmaKadabra** (`#agents`, 2026-08-30): *«Un 200 que no
        hizo la cosa es peor que un 503, porque el cliente lo toma por bueno: si
        nosotros no chequeáramos el tx, habríamos contado 14 ratings que no
        existen.»* — [in English] "A 200 that did not do the thing is worse than a
        503, because the client takes it for good: if we did not check the tx, we
        would have counted 14 ratings that do not exist."

        The three possible outcomes were weighed and this is the only one that
        does not reproduce the bug or make it worse:

          * **raise** — a rotten `tx_hash` would take down a whole breakdown that
            otherwise arrived fine, and one that was ALREADY PAID FOR. Breaking
            over an accessory field is worse than the bug being hunted.
          * **null it silently** — that is exactly what bit KK: the consumer takes
            it for good and counts ratings that do not exist.
          * **let the value through with a mark** — the default path, which is the
            one nobody reviews, keeps building explorer links out of garbage.
            Marking something you hand over anyway protects nobody.

        So: the typed field is left `None`, the raw one survives in `raw`, and the
        fact goes out through `on_error` + WARNING, which is where the consumer is
        already watching. Nothing is raised.
        """
        fields = malformed_hash_report(resultado)
        if not fields:
            return
        self._notify(
            DescribeMalformedHash(
                f"GET {path} brought {len(fields)} hash field(s) carrying something "
                f"that is NOT shaped like an on-chain identifier: "
                f"{', '.join(fields)}. Those fields were left None (the raw value "
                "is still in `.raw`) and the REST of the response is valid and is "
                "returned whole. 🔴 Do not count them as transactions and do not "
                "build an explorer link out of them: a 200 that did not do the "
                "thing gets taken for good.",
                fields=fields,
            )
        )

    def _request(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        disperse: bool = True,
    ) -> httpx.Response:
        """GET with the typed taxonomy. **It never lets an `httpx.*` escape.**

        That no exception from the library crosses the module boundary is
        deliberate: the caller should not have to import `httpx` to write their
        `except`, nor be forced to change it the day this SDK switches HTTP
        client.

        `disperse=False` skips the jitter. **Exactly one** caller uses it: the
        stretch of the 402 dance after the signature. See the module header.
        """
        if disperse:
            espera = _jitter_seconds(self._jitter)
            if espera > 0:
                time.sleep(espera)
        url = f"{self._base_url}{path}"
        try:
            return self._http.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            # 🔴 Tiene que ir ANTES de TransportError: `TimeoutException` es
            # subclase suya, y al revés todo timeout se reportaría como
            # «unreachable» — dos causas distintas con la misma etiqueta.
            raise DescribeTimeout(f"GET {path} outlived the {self._timeout}s timeout") from exc
        except httpx.TransportError as exc:
            raise DescribeUnreachable(f"GET {path} unreachable: {exc}") from exc

    @staticmethod
    def _json(response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise DescribeUnparseable(f"GET {path} did not return JSON") from exc

    def _get_json(
        self, path: str, *, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """A FREE route. 4xx/5xx → `DescribeHTTPError`. R4."""
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
        """`GET /wallets/{wallet}/chains` — **FREE**. The door.

        It is the first move of the happy path and that is not a design
        preference: the metered route's own 402 says it in its `free_preview` —
        *"if there is no reputation there, this charge returns nothing"*.

        The address travels **verbatim**, with no `lower()`: an EVM one is
        normalised by the server, but a Solana id is case-SENSITIVE base58 and
        lowercasing it names a different key, silently and with a 200.

        Returns:
            `WalletReputation` — the index answered. It may have no identities
            (`has_identity is False`) or have them unrated (`global_score is
            None`); both are ANSWERS.

            `None` — **there was no answer**: transport, a non-2xx HTTP, or a 404.
            It never means "has no reputation". It only comes out with
            `fail_open=True` and always with its observation (`on_error` +
            WARNING).

        Raises:
            `DescribeError` if `fail_open=False`.
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
        """`GET /leaderboard` — **FREE**, the whole first page.

        🔴 **It does not accept a single query param.** Measured 2026-08-30:
        `GET /leaderboard?limit=3` → **HTTP 422**, body
        `{"error": "leaderboard_takes_no_params", "params": ["limit"],
        "paged_route": "GET /leaderboard/page"}`. Paging is another route and it
        is metered ($0.01). That is why this method takes no arguments: a
        signature with `limit=` would invite a guaranteed 422.

        ⚠️ And the ordering **is not by average, it is by the Bayesian mean**
        (`shrunk_score`). Re-sorting by `final_score` gives a different list, and
        it looks like a service bug.

        Returns:
            `list[LeaderboardRow]` — the index answered. It may come back empty if
            the index really has no rows: that is an ANSWER.

            `None` — **there was no answer**. 🔴 Never `[]` because of a failure:
            an empty list would claim the index is empty, which is a false claim
            about the world. It only comes out with `fail_open=True` and always
            with its observation (`on_error` + WARNING).

        Raises:
            `DescribeError` if `fail_open=False`.
        """
        try:
            return parse_leaderboard(self._get_json("/leaderboard"))
        except DescribeError as exc:
            if not self._fail_open:
                raise
            self._observe(exc, "/leaderboard")
            return None

    def health(self) -> Optional[IndexHealth]:
        """`GET /health` — **FREE**. The authority on totals and policies.

        No figure of the index is typed by hand: it is read from here, live. And
        the calibratable parameters (`reading_policy`, `confidence_thresholds`)
        come from here too — the service publishes them **precisely** so that no
        consumer copies them.

        It is the slowest free endpoint (~1,6 s measured by EM) and it is uncached
        on purpose — never poll it with a timeout of a few seconds.

        Returns:
            `IndexHealth` — the index answered, even if it answered
            `status != "ok"`: an index that declares itself degraded is ANSWERING,
            and that is exactly the answer you went to ask for.

            `None` — **there was no answer**. Never an empty object: asking about
            the index's state and receiving zeros would be the worst of both
            worlds, because "0 agents" and "I do not know how many agents" are not
            the same fact. It only comes out with `fail_open=True` and always
            observed.

        Raises:
            `DescribeError` if `fail_open=False`.
        """
        try:
            return parse_health(self._get_json("/health"))
        except DescribeError as exc:
            if not self._fail_open:
                raise
            self._observe(exc, "/health")
            return None

    def badge_url(self, address: str) -> str:
        """The SVG badge URL. **No network** — it only builds the string.

        It is here so it is at hand on the same object, but it does not touch the
        HTTP client: it can be called offline, in a render, in a loop.
        """
        return _badge_url(address, base_url=self._base_url)

    # ------------------------------------------------------------------
    # Rutas MEDIDAS (x402)
    # ------------------------------------------------------------------

    @staticmethod
    def _receipt(response: httpx.Response) -> PaymentReceipt:
        """The settlement headers, which until today no client read.

        `X-Payment-Receipt` is the settlement transaction hash (public, useful for
        reconciling) and `X-Payment-Reused: true` says a receipt was replayed
        instead of charging again. Verified in the service:
        `paywall.py:1059-1062` writes them, `api.py:2226` exposes them over CORS.

        The receipt is shape-checked with its own rule: here `pending` is a
        LEGITIMATE value the OpenAPI declares ("settlement has not reported one")
        and it is not marked. See `hashes.looks_like_settlement_receipt`.

        🔴 **`pending` is legitimate, and it is still not a hash.** It reports in
        `settlement_pending` and leaves `transaction_hash` as `None`, because a
        field named after a transaction must never hold a word instead of one.
        Before this, `receipt.transaction_hash == "pending"` was truthy, so the
        most natural check a consumer writes —`if receipt.transaction_hash:`—
        read a payment with no known hash as a payment with a hash. Whatever
        this SDK puts in that field is what lands in the consumer's column; see
        `PaymentReceipt` for the incident that priced this exact shape.
        """
        crudo = response.headers.get("X-Payment-Receipt")
        malos: List[str] = []
        recibo: Optional[str] = None
        pendiente = False
        if crudo is not None:
            texto = str(crudo)
            if texto == SETTLEMENT_PENDING:
                # Not malformed and not a hash: its own answer.
                pendiente = True
            elif looks_like_settlement_receipt(texto):
                recibo = texto
            else:
                malos.append("transaction_hash")
        return PaymentReceipt(
            transaction_hash=recibo,
            reused=str(response.headers.get("X-Payment-Reused", "")).lower() == "true",
            pricing_version=response.headers.get("X-Pricing-Version"),
            malformed_hashes=tuple(malos),
            settlement_pending=pendiente,
        )

    def _partner_signature(
        self, path: str, params: Optional[Dict[str, Any]]
    ) -> Optional[PartnerSignature]:
        """The rail's signature, or `None` if this client is not a partner.

        🔴 **The URL that gets signed is built by `httpx`, not by us.** That is
        not tidiness: the signature base covers `@path` and — when there is one —
        `@query`, so signing a hand-rebuilt URL and sending a different one
        produces a signature that does not verify, a 402, and no clue why.
        Measured 2026-08-30 against the real gate: signed without `?snapshot=true`
        and requested with it ⇒ `PartnerGate.check` returns `None`, i.e. you get
        charged. Going through `build_request`, the two strings are the same **by
        construction**: same arguments, same normaliser, same output.

        Only the METERED routes are signed, and that is measured on the other
        side: `authorize` returns `Decision(True, REASON_FREE, …)` at
        `describe-net/describenet/paywall.py:772-777`, **before** it looks at
        `partner_id` (:794). So a signature on `/health` changes absolutely
        nothing about the outcome — and it does cost: with a remote signer every
        free read would eat a round trip to the KMS in exchange for nothing.
        (Consumption attribution, which is the other thing a partner owes, does
        not come from the signature but from the User-Agent: pass `product=`.)
        """
        if self._partner is None:
            return None
        url = self._http.build_request(
            "GET", f"{self._base_url}{path}", params=params
        ).url
        return sign_partner_headers(self._partner, method="GET", url=str(url))

    @staticmethod
    def _challenge_o_none(response: httpx.Response) -> Optional[Dict[str, Any]]:
        """The 402 body if it can be read, `None` otherwise. **It never raises.**

        It exists for `PartnerRejectedError`'s message: a 402 with an unreadable
        body is still a broken rail, and reporting it as `DescribeUnparseable`
        would send you to investigate the service's JSON when what has to be
        checked is the allowlist.
        """
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _paid(
        self,
        path: str,
        parse: Callable[[Any, PaymentReceipt], _Parsed],
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> _Parsed:
        """The 402 dance, in the order the published guide mandates.

        0. If this client is a **partner**, the first attempt goes signed with the
           rail (ERC-8128, which moves no money: it only says who you are). With
           the wallet allowlisted, step 1 comes back 200 and there is no 402 in
           the whole story. And if a 402 comes back anyway, **it raises**: see the
           `PartnerRejectedError` branch below.
        1. Ask **with no payment header** — asking is free and it is the first
           move the service expects.
        2. If a 402 comes back: read the challenge, **verify the recipient**
           against the pinned treasury, sign with the payer.
        3. Replay **the identical request** with `X-PAYMENT`.

        There is no retry after the second attempt, and that is deliberate: the
        nonce is consumed at settlement, so resending the same credential does not
        pay again — a 4xx after paying is almost always a spent credential or one
        signed for a different amount, and the remedy is to ask for a NEW
        challenge, not to repeat the old one. A `retries=3` here would burn
        credentials.

        🔴 **Parsing happens in here, not in the public method.** That is what
        makes there be ONE single "from here on there is money in flight" boundary
        in the whole file. If parsing lived outside, a `DescribeUnparseable` over
        an ALREADY PAID body would come out without `payment_sent` —
        indistinguishable from a broken body that cost nothing. The mark cannot
        depend on whoever adds the third metered route remembering to set it.

        🔴 **None of this is swallowed by the fail-open, whatever it is worth.**
        See the module header: the line is whether there was money in flight.
        """
        # ── Tramo PRE-PAGO: no se firmó nada, no se gastó nada ───────────────
        #
        # La firma del RIEL DE PARTNER (que no es una firma de pago: no mueve
        # un centavo, sólo dice quién sos) va en el PRIMER intento. Si describe
        # dio de alta la wallet, este único GET vuelve 200 y no hay 402 en toda
        # la historia. Si el firmante falla, `sign_partner_headers` levanta
        # `PartnerSigningError` acá mismo, antes de que salga una sola request.
        firma = self._partner_signature(path, params)
        response = self._request(
            path, params=params, headers=dict(firma.headers) if firma else None
        )
        if response.status_code != 402:
            if response.status_code >= 400:
                raise DescribeHTTPError(
                    f"GET {path} → HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            # Un 200 sin pagar: el servicio decidió no cobrar (o hay un caché
            # delante). No hubo credencial, así que no hay nada que marcar.
            gratis = parse(self._json(response, path), self._receipt(response))
            self._check_hashes(gratis, path)
            return gratis

        # ══ EL RIEL SE ROMPIÓ, Y ACÁ SE LEVANTA EN VEZ DE PAGAR ══════════════
        # Firmamos como partner y describe igual pide plata. Las cuatro causas
        # están en `PartnerRejectedError` y las cuatro son fail-closed del lado
        # del servicio: la wallet no está en la allowlist, se firmó contra otro
        # host, el reloj se corrió, o la firma no cubría la URL que salió.
        #
        # 🔴 Esta rama va ARRIBA de la del `payer`, y ese orden ES la decisión
        # entera del modo partner. Al revés —dejar caer un partner con `payer`
        # al camino de pago— el bug sería invisible: la respuesta llega igual,
        # el código funciona, y lo único que cambia es una factura de USDC que
        # aparece semanas después. «Levanta, no degrada» significa exactamente
        # esta línea, y `test_partner_riel_gratis.py` la ata con un payer que
        # explota si alguien lo llama.
        if firma is not None:
            raise PartnerRejectedError(
                f"GET {path} answered 402 even though this client signed as a "
                f"partner with wallet {firma.wallet}. NOTHING WAS PAID and nothing "
                "will be paid on its own. Check, in this order: (1) that describe "
                "has allowlisted that wallet; (2) that `base_url` is "
                "https://api.describe.net — you sign against the host you point "
                "at and the gate verifies against its own pinned one; (3) the "
                "signer's clock (the gate's window is 300 s). If you really do "
                "want to pay, build the client WITHOUT `partner=`.",
                wallet=firma.wallet,
                challenge=self._challenge_o_none(response),
            )

        try:
            challenge = response.json()
        except ValueError as exc:
            raise DescribeUnparseable(f"the 402 from {path} is not JSON") from exc

        if self._payer is None:
            raise PaymentRequiredError(
                f"GET {path} is a metered route and this client has no `payer`. "
                "Build it with `DescribeClient(payer=...)` — or use the free door, "
                "which for a wallet is `wallet()`.",
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
        # 🔴 Sólo un hash CITABLE cuenta como prueba de settlement, y esto no es
        # celo: `mark_payment_sent` dice, textual, «HAY RECIBO … el settlement
        # ocurrió, el gasto está confirmado y es citable». Esa frase es FALSA
        # sobre dos valores que llegan por esta misma cabecera:
        #   * `pending`, que el OpenAPI declara legítimo y significa justamente
        #     lo contrario — el settlement TODAVÍA no reportó nada;
        #   * cualquier basura, que es el «200 sin tx» de KK dentro del camino
        #     del dinero, donde miente más caro que en ningún otro lado.
        # Con `None` la excepción usa su otra rama, que es la honesta: «no hay
        # prueba en ninguno de los dos sentidos». Preferir el silencio a una
        # afirmación fuerte que no se puede sostener.
        recibo: Optional[str] = None
        try:
            # 🔴 `disperse=False`: NO se duerme entre firmar la autorización
            # EIP-3009 y despacharla. El jitter dispersa un rebaño, y este
            # rebaño ya se dispersó en el primer tramo; dormir acá sólo quema
            # ventana de settlement (`maxTimeoutSeconds: 120`) con la credencial
            # firmada en la mano. Ver la cabecera del módulo.
            paid = self._request(
                path, params=params, headers={"X-PAYMENT": header}, disperse=False
            )
            cabecera = paid.headers.get("X-Payment-Receipt")
            if cabecera is not None and looks_like_onchain_id(str(cabecera)):
                recibo = str(cabecera)
            if paid.status_code >= 400:
                raise DescribeHTTPError(
                    f"GET {path} → HTTP {paid.status_code} AFTER paying. The "
                    "credential is already consumed: ask for a new challenge, do "
                    "not resend this one.",
                    status_code=paid.status_code,
                )
            pagado = parse(self._json(paid, path), self._receipt(paid))
            # Observar DESPUÉS de parsear y ANTES de devolver: si el observador
            # de quien llama explota, `_notify` ya lo aísla — pero además nada de
            # esto puede impedir que la respuesta PAGADA llegue a sus manos.
            self._check_hashes(pagado, path)
            return pagado
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
        """`GET /reputation/wallet/{wallet}` — **$0.01** ($0.05 with `snapshot`).

        The breakdown: who rated, how many times, on what date and in which
        transaction. The global number is already **free** in `wallet()`; what is
        charged for is the decomposition.

        Args:
            snapshot: persists the answer and returns the row. It is the **only
                route that writes** and it costs more. What you buy is not the
                number but the commitment to it: a durable receipt with an
                `inputs_digest` you can cite later.

        🔴 **It is not nullable and the fail-open does NOT swallow it — not even
        with an explicit `fail_open=True`.** It is not a caller preference but a
        property of the method: between signing the envelope and receiving the
        answer the USDC has already moved, and a `None` there is a spent
        credential with no receipt. An availability flag cannot buy the right to
        swallow a receipt. (R5 corrected, 2026-08-30 — see the module header.)

        Raises:
            `PaymentRequiredError` if there is no `payer` (it carries the whole
            challenge, with its `price_usd` and its `free_preview`).
            `DoNotPayError` if the 402 asks to pay a different address.
            `DescribeError` on any service failure. If it fell AFTER signing, it
            comes out with `exc.payment_sent is True` and `exc.payment` carrying
            the amount, the network and the `X-Payment-Receipt` if there was one.
        """
        path = f"/reputation/wallet/{quote(str(address), safe='')}"
        params = {"snapshot": "true"} if snapshot else None
        return self._paid(path, parse_breakdown, params=params)

    def agent(self, network: str, agent_id: str) -> AgentReputation:
        """`GET /reputation/agent/{network}/{agent_id}` — **$0.02**.

        `network` has to be one of `health()`'s `chains[].network`; an id is only
        unique **per chain**.

        `agent_id` is a **string, not a number**: the EVM registries mint integers
        but on Solana the id is the base58 address of the Metaplex Core asset —
        case-sensitive, and running it through `int()` destroys it.

        🔴 **It is not nullable and the fail-open does NOT swallow it**, for the
        same reason as `wallet_breakdown()`: there is money in flight. See the
        module header and, for the post-signature failure, `exc.payment_sent`.
        """
        path = (
            f"/reputation/agent/{quote(str(network), safe='')}"
            f"/{quote(str(agent_id), safe='')}"
        )
        return self._paid(path, parse_agent_reputation)
