# uvd-describe-sdk (Python)

Python client of **describe**'s ERC-8004 reputation index — `api.describe.net`.

```bash
pip install uvd-describe-sdk            # the free path: one single dependency (httpx)
pip install "uvd-describe-sdk[x402]"    # + pay the metered routes
pip install "uvd-describe-sdk[partner]" # + the partner rail (signs, does not pay)
```

```python
from uvd_describe_sdk import DescribeClient, format_score

with DescribeClient(product="my-app") as describe:
    rep = describe.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    if rep is None:
        print("the index did not answer")        # ← NOT "no reputation"
    elif not rep.has_identity:
        print("not registered")
    elif rep.global_score is None:
        print("registered, not yet rated")
    else:
        print(format_score(rep.global_score), "·", rep.policy_version)
```

---

## The three things to know before using it

### 1. `None` is never zero, and never "has no reputation"

There are **three distinct facts** and the type keeps them distinct:

| Situation | How it looks |
|---|---|
| The index could not be read | `rep is None` |
| Wallet not registered | `rep.has_identity is False` |
| Registered and unrated | `rep.global_score is None` |

A `0` in a score would claim *"they were rated terribly"* about somebody nobody
rated. Measured in production: a prior of 50 painted a *silver* badge on executors
with no history, and the written conclusion was **"the 50 is worse than a gap"**.

### 2. No method returns a bare number

Every result carries `policy_version`, `caveats[]` and its source. If you want the
number alone you take it off the object by hand — **and that is on purpose**: the
product's thesis is that *a score without its raters is a rumour*, and the gesture
of taking it out leaves written in your code that you decided to throw the context
away.

A contract test walks `__all__` and fails if a function appears that returns
`float`. It is verified by mutation: a `get_score(x: float) -> float` was injected,
it went red naming it, and it was removed.

### 3. Branch on `caveats[].code`, never on `caveats[].text`

The service's schema declares it: *"Codes are permanent; text is not."* All eight
are exported so you do not type them:

```python
from uvd_describe_sdk import CaveatCode, is_known

if CaveatCode.BURN_ADDRESS in rep.caveat_codes:
    ...  # nobody controls this wallet: anyone can rate it
```

`Caveat.code` is a `str`, **not an `Enum`**. A closed enum would mean a new code
from the service breaks or disappears — and discarding a caveat is discarding the
warning. `is_known(code)` says whether it is one of the eight known ones; an
unknown one still arrives whole and has to be shown.

---

## The surface

| Method | Cost | Route |
|---|---|---|
| `wallet(address)` → `WalletReputation \| None` | **free** | `GET /wallets/{w}/chains` |
| `leaderboard()` → `list[LeaderboardRow] \| None` | **free** | `GET /leaderboard` |
| `health()` → `IndexHealth \| None` | **free** | `GET /health` |
| `badge_url(address)` → `str` | **no network** | builds the URL, does not request it |
| `wallet_breakdown(address)` → `Breakdown` | $0.01 ($0.05 with `snapshot=True`) | `GET /reputation/wallet/{w}` |
| `agent(network, agent_id)` → `AgentReputation` | $0.02 | `GET /reputation/agent/{n}/{id}` |

**The three free ones are nullable; the two metered ones never are.** That is not
an accident of the table: it is the fallback rule, below.

**And if describe allowlisted your wallet, the two metered ones cost you $0**
without ceasing to be "metered" for everything else. That is the partner rail,
below.

**Free first, and not out of courtesy.** The 402 itself says so in its
`free_preview`: *"if there is no reputation there, this charge returns nothing"*.
`wallet()` is the door; the metered breakdown is asked for afterwards.

---

## The fallback (R5) — the easiest thing to get wrong

Saul asked for it verbatim on 2026-08-28: *"pon un fallback si es que describe está
caído"* ("put a fallback in if describe is down"). The default is
`fail_open=True`.

But a naive fail-open **breaks rule 1**: if "the index is down" and "this wallet
has no reputation" returned the same value, the fallback would have manufactured
exactly the confusion rule 1 exists to prevent. And it is not hypothetical — it
cost KarmaKadabra a wrong report on 2026-08-28, in the gate that decides who to
trade with.

It is solved with **two mechanisms, not with a comment**:

1. **The distinction lives in the type.** A wallet the index really could read
   comes back as an object, even with not a single rating. `None` means one single
   thing: *there was no answer*.
2. **No `None` leaves in silence.** It always goes through the observer and always
   logs at WARNING. There is no silent mode.

```python
def to_my_metrics(err):
    metrics.incr("describe.down", tags={"kind": err.kind})

DescribeClient(product="my-app", on_error=to_my_metrics)
```

**What the fail-open does NOT cover:** `PaymentRequiredError` and `DoNotPayError`.
It is for the *availability of the index*, not for your configuration nor for a
diversion of funds.

### 🔴 What it covers and what it does not — the line is whether there was money in flight

| Route | Cost | On a service failure |
|---|---|---|
| `wallet()` · `leaderboard()` · `health()` | free | `None`, always observed. **Never `[]`.** |
| `wallet_breakdown()` · `agent()` | $0.01 / $0.02 | **THEY RAISE. Always.** |

The metered ones raise **even with an explicit `fail_open=True`**, and the reason
is money, not symmetry: between signing the x402 envelope and receiving the answer
there is a window in which the USDC has already moved. Returning `None` there hides
from you that you spent — it is a spent credential with no receipt, and nothing
distinguishes *"I paid and it fell over"* from *"there was nothing to fetch"*. A
loud failure after paying is recoverable (you retry, you log, you claim); a silent
`None` is not. It is not a preference of yours: it is a property of the method. An
availability flag cannot buy the right to swallow a receipt.

And the free ones do, all **three** — not just `wallet()`: a loud failure on
something free forces you to write your own `try/except` for something the SDK
already knows how to do, which is precisely the duplication this SDK came to erase.

`None` and **never** `[]`: an empty list claims that *the index is empty*, which is
a false claim about the world. `None` says *I could not ask*.

### If a metered route fails, did you spend?

```python
try:
    br = describe.wallet_breakdown("0x97cd…0996")
except DescribeError as err:
    if err.payment_sent:
        # The EIP-3009 authorization was already signed and dispatched: the USDC
        # MAY have moved. Reconciling is your job.
        reconcile(err.payment)   # amount_usd, network, resource, transaction_hash
    else:
        # It fell before signing. No credential left, you spent nothing.
        retry()
```

**Branch on the attribute, never on the text** — same as `err.kind` and as
`caveats[].code`. The message says it too, because whoever reads a traceback in a
log at 3 AM does not have the object at hand; but the text is to read and the
attribute is to decide.

⚠️ **Known limit, and it is written down because it matters:**
`payment_sent=True` proves the credential **left**, not that settlement happened.
The latter is only proved if `payment["transaction_hash"]` comes back filled — that
is, if the server managed to answer with its `X-Payment-Receipt`. When the
transport dies there is no way, from the client, to know whether the facilitator
settled: that would require asking it or the chain, and this SDK is a reader of the
index, not of settlement. `payment_sent=False`, on the other hand, **is** a strong
claim: nothing was signed.

> ✅ **The contract's ambiguity was resolved on 2026-08-30.** R5 said "on failure
> return `null`" without narrowing it and the type table narrowed it to `wallet()`;
> this SDK had followed the table and the TypeScript twin had followed the rule,
> ending up with **fail-open on the metered routes** — `null` after a
> post-settlement timeout. The corrected rule above is canon and both SDKs
> implement it identically. What survived from the old version is the observation
> that an empty list reads as an empty index: that is why the contract says "never
> `[]`".

### `err.recovery` — what to do INSTEAD, and why it is sometimes empty

Contributed by **Execution Market** (`#agents`, 2026-08-30), who that day typed ten
codes into their own 502 and published it *"para que lo codifiquen de su lado"*
("so you can code it on your side"). Their argument is what justifies the field:

> *"SIETE de los diez son TERMINALES (`retryable:false`) … contra
> `AUTHORIZATION_EXPIRED` reintentar es quemar llamadas contra una ventana cerrada
> hace 317 HORAS."*
>
> [translation] "SEVEN of the ten are TERMINAL (`retryable:false`) … against
> `AUTHORIZATION_EXPIRED`, retrying is burning calls against a window that closed
> **317 HOURS** ago."

The **pattern** was absorbed, not their table: their codes belong to their API
(escrow, release to the worker, payout wallets) and this SDK wraps describe's. So
**our** `kind`s were walked and decided one by one.

```python
except DescribeError as err:
    log.warning("describe: %s", err)
    if err.recovery:
        log.warning("what to do: %s", err.recovery)   # it is READ
    if err.kind == "payment_required":                # it is BRANCHED on
        ...
```

| `kind` | Is there a recovery path? |
|---|---|
| `payment_required` | Yes — **the free door**: `wallet()` gives the global score without paying or signing, and the 402 names the free one for *that* subject in `challenge['free_preview']`. |
| `partner_rejected` | Yes — describe does the allowlisting: retrying does not produce it. |
| `partner_signing` | Yes — and the fact nobody works out alone: **a broken rail blocks nothing that is free**. |
| `do_not_pay` | Yes — nothing was signed; it forks on `expected` / `offered`. |
| `http_error` | Yes — branch on `status_code`: the bucket merges three causes. |
| `unreachable` · `unparseable` | Yes — look at `base_url` and at the intermediary before the index. |
| `malformed_hash` | Yes — the raw value is in `.raw`, and **do not discard the response**. |
| `timeout` | 🔴 **No, and it is pinned by a test.** |

**That `timeout` is empty is the important part**, not an oversight: it is the most
common failure and even so there is no *other* thing to do. Raising the timeout
collides with the provider's 29 s API Gateway (the default is already 30) and
"retry" would be a boolean written out in prose. **Inventing a recovery that does
not work is worse than not having the field**, because it sends you off to do
something useless with confidence.

It is **read**; to decide there is `kind`. It is the same pair as `caveats[].code`
/ `caveats[].text`, and that is why `recovery` is text and not an enum: the enum
already exists and is called `kind`. The text is a **constant** we wrote — it never
interpolates the message of somebody else's exception, so it cannot leak an RPC
URL with its API key or a DSN. There is a test with a fake secret that goes red if
it ever did.

> **TypeScript parity.** The twin ships the same field with the same name and the
> same policy. The texts are not byte-identical, and the reason is measured rather
> than sloppy: this SDK has ONE `http_error` bucket where the twin has three kinds
> (`http_5xx`, `http_4xx`, `rate_limited`), so one text here has to say what three
> say over there. Every text that *can* match does, and none of them names a
> language-specific spelling that the other side does not share.

---

## Paying (R6) — one single toll booth

This SDK **never signs, custodies or derives a key**. The 402 is resolved by
`uvd-x402-sdk`, and here we only verify who is being paid, pick the network and
delegate.

```python
import os
from uvd_x402_sdk import X402Client
from uvd_describe_sdk import DescribeClient, TREASURY_EVM

payer = X402Client(recipient_address=TREASURY_EVM)
payer.connect_with_private_key(os.environ["MY_KEY"], chain="base")  # never in a file

with DescribeClient(payer=payer, pay_network="base", product="my-app") as describe:
    br = describe.wallet_breakdown("0x97cd…0996")
    print(br.final_score, br.caveat_codes, br.receipt.transaction_hash)
```

**The check that cannot be turned off:** if the 402 names a `payTo` that is not the
pinned treasury, it is `DoNotPayError` — **not a retry**. Retrying there turns a
diversion of funds into a diversion of funds with retries.

And it is verified **before** signing. The test that pins it uses a payer that
blows up if called: it was verified by mutation that moving the check after the
signature turns that test red.

**`result.receipt`** exposes `X-Payment-Receipt` and `X-Payment-Reused` — the
headers the paywall has always emitted and that **no client was reading**. It is the
difference between "I paid" and "I can prove I paid".

---

## The partner rail — into the metered routes without spending a cent

If describe allowlisted your wallet, the metered routes stop charging you. **It is
not a token: it is a signature.**

```python
from uvd_x402_sdk.wallet import EnvKeyAdapter    # reads WALLET_PRIVATE_KEY
from uvd_describe_sdk import DescribeClient

with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as describe:
    br = describe.wallet_breakdown("0x97cd…0996")   # $0.01 for a third party, $0 here
```

### Why a signature and not an API key

describe **custodies no secret of yours**. Its allowlist is made of PUBLIC
ADDRESSES — they can be committed, logged and published without leaking anything —
and you sign each request with a dedicated wallet. **A breach of describe does not
compromise your access**, because no credential of yours lives over there.

And charge-by-default is structural, on the server side: an absent, empty or
invalid-JSON env ⇒ empty allowlist ⇒ 402 for everyone. No broken configuration ever
means "let everything through".

### 🔴 This SDK still does not touch your key

`partner=` receives an **object that signs**, not a key: two methods,
`get_address()` and `sign_message()`. It is the same pair as `uvd-x402-sdk`'s
`WalletAdapter`, so its `EnvKeyAdapter` (the key in **your** environment), a KMS, an
HSM or a Ledger all fit, without inheriting anything from this package.

Use a **dedicated wallet with no funds**: all it does is sign. That is what makes
the worst case cheap — a leaked signature works against the same method and the
same URL, and only for 300 seconds. It is not a permanent credential and it cannot
move money.

> **Never** write a private key into a file, not even "temporarily". There are bots
> sweeping GitHub for `0x` + 64 hex that drain in minutes.

### If the rail goes down, the client RAISES — it does not pay

| What happened | What you get | Did you spend? |
|---|---|---|
| the signer breaks (KMS down, extra not installed) | `PartnerSigningError`, **before the first request** | no, and it is a strong claim |
| describe answers 402 despite the signature | `PartnerRejectedError` (inherits from `PaymentRequiredError`) | no: **the `payer` is not used even if it is there** |

That is the entire decision of the mode. A partner with `payer=` and the rail down
has one obvious, silent path — pay — and there the bug is never seen: the answer
arrives anyway, the code works, and the USDC invoice shows up weeks later. Both
exceptions come out with **`payment_sent is False`**: you find out you lost the free
rail **without having spent** the USDC the rail was saving you. If you really do
want to pay, build the client **without** `partner=`.

The four causes of a `PartnerRejectedError`, all fail-closed on the service's side:
the wallet is not on the allowlist · you signed against another host (your
`base_url` is not `api.describe.net`) · the clock drifted more than 300 s · the
signature did not cover the URL that went out. The exception carries `wallet` — the
public address you signed with, which is what to quote to describe.

### Two details that cost dearly

- **What is sent is what is signed, byte for byte, query included.** The base covers
  `@query` only when the URL has one, so signing a hand-rebuilt URL and sending a
  different one gives a 402 nobody understands. The SDK signs the URL `httpx`
  already built: they are the same string **by construction**.
- **Only the metered routes are signed.** It is measured on the other side: the
  paywall decides "free" *before* looking at the partner, so a signature on
  `/health` changes nothing — and with a remote signer it would cost a round trip to
  the KMS per read. Consumption attribution, which is the other thing a partner
  owes, comes from the User-Agent: pass `product=`.

**The rail does not move rule R5.** That `wallet_breakdown()` comes out free for you
does not turn it into a free route: it still raises on any failure, explicit
`fail_open=True` included. The criterion was never the price you paid but whether
there was money in flight.

---

## Showing a score (R8)

```python
format_score(86.653045)  # '86.65'
format_score(83.0)       # '83'   ← the witness case, not '83.00' nor '83.0'
format_score(None)       # '—'    ← NEVER '0'
```

Two decimals, trailing zeros trimmed. It came out of a measurement over 47 real
scores: 0 decimals merges 23 pairs of *different* agents into the same string, 1
decimal merges 4, 2 decimals merges 1. The JS twin is
`String(parseFloat(x.toFixed(2)))` and a test compares the two.

---

## The badge — the little piece that gets copied and pasted

```python
from uvd_describe_sdk import badge_url, badge_img_tag

badge_url("0x97cd…0996")      # https://api.describe.net/badge/0x97cd….svg
badge_img_tag("0x97cd…0996")  # <img src="…" alt="…" height="20" loading="lazy">
```

**Zero network**: it only builds the string. The fetch is done by the browser of
whoever is looking at the page, and the edge serves the badge with
`stale-if-error=604800` — meaning it keeps painting the last known value even with
the origin down, without a line of code from whoever embeds it.

⚠️ **A badge does not replace a read.** It is an image: it does not carry
`caveats[]`, it does not distinguish `[]` from `null` and you cannot branch on it.
To *decide* you use `wallet()`; the badge is to *show*.

---

## Configuration

```python
DescribeClient(
    base_url="https://api.describe.net",
    timeout=30.0,          # R7 — see below
    product="my-app",      # → User-Agent. Pass it.
    fail_open=True,
    on_error=None,         # called with the swallowed exception
    jitter=0.4,            # dispersion before every GET. ON. `0` turns it off
    payer=None,            # only for the metered routes
    pay_network="base",
    treasury=TREASURY_EVM,
    partner=None,          # the rail's signer — an OBJECT, never a key
    transport=None,        # httpx.MockTransport, for tests
)
```

**The 30 s timeout is reasoned, not picked**: the provider Lambda's cold start
measured 15,2 s, their API Gateway cuts at 29 s, and 30 is *deliberately different*
from the facilitator's 45 s so the two clocks never expire in the same second
(INC-2026-08-19).

**Pass `product`.** The rate limit is **shared** across every consumer and there is
no per-partner bucket: without attribution in the User-Agent, nobody can know who
spent it. An anonymous request against a shared limit is free-riding.

🔴 **The number is not typed into this SDK, and this line is the reason**: until
today four surfaces here said "20 rps", inherited from the old documentation. The
limit was raised on **2026-08-28** and none of them found out. The live authority is
the **`RateLimit-Policy`** header the API sends on every response — measured
2026-08-30 it reads `50;w=1;burst=40`. Read it from the response, not from a README.

### 🔴 `jitter` ships ON (0,4 s) — the trade-off, written down

Contributed by **KarmaKadabra** (2026-08-30), who run 27 agents: *"27 agentes
despiertan al MISMO tiempo por EventBridge y pegan simultáneo contra su límite de
rps COMPARTIDO con los otros consumidores. Sin jitter, un enjambre es un DDoS
educado."* — [in English] "27 agents wake at the SAME time on EventBridge and hit
their rps limit — SHARED with the other consumers — simultaneously. Without jitter,
a swarm is a polite DDoS." The SDK sleeps `uniform(0, jitter)` before every GET.

Both sides are real: a library that sleeps without being asked is surprising, and
whoever writes a one-off script pays 0.2 s of median for a problem they do not have.
What breaks the tie is **who pays for the mistake**. The cost of having it on is
paid by whoever chose the default; the cost of having it off is paid by **third
parties** — the limit is shared, so an undispersed swarm eats the budget of
MeshRelay and Execution Market, who chose nothing. A default whose damage lands on
somebody who did not choose it is not a default, it is a trap.

Turning it off is explicit and stays written in your code:
`DescribeClient(jitter=0)`.

**It disperses, it does not yield.** Jitter is not backoff: it disperses a herd that
has not asked for anything yet, whereas backoff yields to a service that has already
said no. And it is **never** applied to the stretch of the 402 after the signature —
sleeping with a signed EIP-3009 authorization in hand burns settlement window in
exchange for zero dispersion.

> **TypeScript parity.** The twin takes `jitterMs=400`, in **milliseconds**, because
> its timeout is `timeoutMs` while this one's `timeout` is in seconds. Same value,
> same policy, different unit — each language keeps its own rather than one of them
> carrying a lying name.

---

## Counting raters: `resolve_distinct_raters()`

Contributed by **MeshRelay** (2026-08-30). Both obvious ways of deriving it are
wrong, in **opposite** directions, and that is why the helper exists:

| Way | What happens | Measured case |
|---|---|---|
| **Summing** per chain | double-counts whoever rated on two networks | karma-hello: **9** global, **11** summed |
| **Maximum** per chain | underestimates | 3 on `base` + 4 different ones on `avalanche` = **7** real, the maximum says **4** |

```python
rep.resolve_distinct_raters()   # the global figure if it came: THE answer
                                # if it did not: the maximum, a LOWER BOUND
                                # None if there is neither global nor chains (R1)
```

🔴 **The maximum serves ONLY as a lower bound / fallback. Never as the answer.**
Corroborated on 2026-08-30 against the live index: on the **3 of 3** multi-chain
wallets of the leaderboard, both traps fire.

---

## Hash fields: shape validated, never in silence

Contributed by **KarmaKadabra** (2026-08-30), out of the *"el 200 sin tx"* finding:
*"Un 200 que no hizo la cosa es peor que un 503, porque el cliente lo toma por
bueno: si nosotros no chequeáramos el tx, habríamos contado 14 ratings que no
existen."* — [in English] "A 200 that did not do the thing is worse than a 503,
because the client takes it for good: if we did not check the tx, we would have
counted 14 ratings that do not exist."

`tx_hash`, `feedback_hash`, `revoked_tx`, `inputs_digest` and the
`X-Payment-Receipt` are validated **by shape**. A value not shaped like an on-chain
identifier:

1. leaves the typed field `None` (so nobody builds an explorer link out of garbage)
   — the raw value stays in `.raw`;
2. enters the model's `malformed_hashes`;
3. **is announced through `on_error` + WARNING**, the same channel as the fail-open;
4. and **does not raise**: the rest of the response arrives whole. Breaking an
   already-paid breakdown over an accessory field would be worse than the bug.

🔴 **Absent and malformed are NOT the same thing** (R1, one level further down):

```python
r.tx_hash is None and not r.malformed_hashes      # IT DID NOT COME (normal: the
                                                  # backfill has not got there yet)
r.tx_hash is None and "tx_hash" in r.malformed_hashes   # GARBAGE CAME
```

**The UNION of the live shapes is validated, not the intersection** — measured
2026-08-30: EVM is `0x` + 64 hex (66 chars), **Solana is base58 of 87–88 chars**,
`inputs_digest` is a **bare** sha256 without `0x`, and `X-Payment-Receipt` may be
worth the literal `pending`. An EVM-hash regex would have flagged every Solana
rating as malformed, and an alarm that fires on the happy path gets learned into
being ignored.

---

## What this SDK does NOT do

- It writes to no chain and issues no ratings. It is a **reader**.
- **It custodies no keys and implements no cryptography.** ⚠️ Corrected on
  2026-08-30, and the correction is left written because the old line — "it does not
  sign" — is no longer exact: with the partner rail the SDK **does produce an
  ERC-8128 signature**, but it is made by `uvd-x402-sdk` with a signing object the
  consumer injects. What never changed, and is what the sentence meant, is that **no
  key lives here**: not in a default, not in an env var, not in a parameter. It also
  does not implement EIP-3009, RFC 9421 or EIP-191.
- It does not cache. That is a decision, not an oversight: the right TTL depends on
  what the read is for (mesh uses 12 min for a channel; a profile wants the value
  hot) and a cache inside the SDK with the wrong default is worse than none.
  Freshness travels in `refreshed_at` so the caller decides.
- It has no async API. See risks.

---

## Compatibility and status

- Python **3.9+**. The type check runs against 3.10 because mypy ≥ 2.0 refuses to
  analyse 3.9; compatibility with 3.9 is guaranteed by CI running the suite there,
  which is execution evidence.
- **Nothing in this package is published yet.** Not on PyPI, not on GitHub.
- The name `uvd-describe-sdk`: **hypothesis to be ratified**. Saul never named it.

### What the payments SDK is missing (upstream, to report over there)

- **`uvd-x402-sdk` does not publish `py.typed`** (measured in 0.70.0, 2026-08-30):
  mypy treats it as untyped and every typed consumer loses its entire signature. The
  hole is declared here in a mypy override, not patched — it gets fixed upstream.
- **And what it was NOT missing**, measured before writing a line of the partner
  rail: `uvd-x402-sdk` 0.70.0 **already signs ERC-8128**
  (`uvd_x402_sdk.erc8128.sign_request`, with EM's fleet's golden vectors inside the
  package), with `DEFAULT_CHAIN_ID = 8453` and `DEFAULT_VALIDITY_SEC = 300` — that
  is, exactly what the service's gate demands. There was nothing to push upstream:
  the *upstream-first* rule was met by measuring and finding the primitive already
  built, which is the best of its outcomes. It is noted anyway because next time the
  question gets answered by reading this instead of measuring again.

### Risks and open questions

- **This SDK is sync and Execution Market's client is `async`.** To adopt it, EM
  would have to wrap it in a thread. It is the most concrete adoption question left;
  a thin-transport `aio.py` (reusing these parsers, without duplicating policy) would
  be the way out, and it has not been written yet.
- ~~**The "free rail" for our own products** (EM / mesh / KK) that Saul asked for on
  2026-08-14 is not solved and was not invented here.~~ **RESOLVED on 2026-08-30** —
  see §"The partner rail" above. It is left struck through rather than deleted
  because the correction teaches something: the old line said *"the service has no
  accounts and no API keys, so there is no obvious way to tell them apart"* and drew
  from that "do not invent a partner header". The premise was right and the
  conclusion was not: the way existed and it was not an invented header but **a
  signature with the wallet**, which is the same identity primitive the metered face
  already used — they diverge only in policy (allowlist vs payment), not in
  mechanism. What saved this repo from inventing something worse was the "do not
  decide it alone", and what solved it was that the service built it first: this SDK
  only speaks it. **Who goes on the allowlist is still Saul's decision**, and that did
  not change: there is no list here.
- ~~The scope of the fail-open~~ **resolved on 2026-08-30** (above): the free ones
  degrade, the metered ones raise. Left struck through rather than deleted: whoever
  remembers the question deserves to find the answer where they left it.
- **A settlement cannot be confirmed from the client.** `payment_sent` says the
  credential left; only an `X-Payment-Receipt` proves it settled, and a dead
  transport brings none. Closing that hole requires asking the facilitator or the
  chain, and that is another dependency and another product.
- The SDK was tested against the live API only on its **free routes**. The metered
  ones are exercised with a mocked payer; not a cent of USDC was ever spent. **That
  is why the post-signature failure is tested with a fake transport and not with a
  real payment**: we know the SDK marks the exception, we did not measure a real
  interrupted settlement.

---

## Trampas de migración (medidas por los equipos)

Mirror of the TypeScript twin's section. None of these are hypothetical: every
entry was hit — or dodged in writing — by a real consumer (mesh's adapter spec
lives in `meshrelayserv/describenet.js@04f2ecf`; Execution Market's is
INC-2026-08-26).

1. **`format_score` returns a STRING; mesh's legacy `formatScore` returned a
   NUMBER.** Same rule, same value, different type: adopting the SDK's by name
   would have silently turned `"score": 87` into `"score": "87"` in every JSON
   surface of theirs (measured by mesh, `describenet.js@04f2ecf`). If your
   formatter feeds JSON, you want the number — `round(score, 2)`; the TypeScript
   twin ships it as `roundScore`, this package deliberately ships only the display
   string.

2. **The constructor takes `product=`, and an unknown key is a `TypeError` at the
   line of the typo.** Keyword-only, no `**kwargs`: `DescribeClient(userAgent=…)`
   — the camelCase reflex from the TS twin — blows up instead of silently
   shipping an unattributed client against the SHARED rate limit.
   **Where the twins differ:** TypeScript only accepts `product`; Python accepts
   `product` **and** `user_agent`, and `user_agent` wins when both are passed
   (`client.py:390`). Migrating TS→py you lose nothing; migrating py→TS a custom
   `user_agent` has no seat and the UA is rebuilt from `product`.

3. **Do not re-parse what a method already parsed.** `wallet()` returns a
   `WalletReputation`, not a dict; feeding it back into `parse_wallet_reputation`
   raises `DescribeUnparseable` (measured 2026-08-31, and pinned by
   `tests/test_parser_equivocado.py` so it stays loud). The raw body, if you need
   it, is on `.raw`.

4. **The wrong parser now FAILS LOUD.** The free body
   (`global_score`, no `final_score`) fed to `parse_breakdown` — or the metered
   body fed to `parse_wallet_reputation` — used to succeed silently with the
   score evaporated into `None`, which R1 teaches you to read as "not yet
   rated". Since 0.2.0 both directions raise `DescribeUnparseable` naming the
   parser you wanted.

5. **`pending` no longer travels inside `transaction_hash`.** Until 0.1.0 a 200
   could carry `receipt.transaction_hash == "pending"` — a placeholder sitting in
   the seat of the proof, the exact family of Execution Market's INC-2026-08-26.
   Since 0.2.0 it arrives as `transaction_hash=None` +
   `settlement_pending=True` (and the sentinel is exported as
   `SETTLEMENT_PENDING`, as the TS twin always did). If you were string-matching
   `"pending"`, branch on the flag instead.

---

## Development

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest        # 215 tests, ~15 s, NO NETWORK
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy src/uvd_describe_sdk
.venv/Scripts/python examples/smoke_gratis.py   # this one DOES hit the live API
```

The whole suite runs without network: the seam is the constructor's `transport=`
(`httpx.MockTransport`). The fixture payloads **are not invented** — they are literal
captures of `api.describe.net` from 2026-08-30. An invented fixture tests against
the idea of whoever wrote it; a captured one tests against what the service sends,
and that difference has already been paid for once in this ecosystem.

MIT · [describe.net](https://describe.net) · [docs](https://docs.describe.net)
