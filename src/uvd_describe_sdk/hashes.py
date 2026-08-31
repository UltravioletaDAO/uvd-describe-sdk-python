"""SHAPE validation of the hash fields — "el 200 sin tx", from KarmaKadabra.

Pure: no network, no clock, no SQL. A function from `str` to `bool` and nothing
else.

════════════════════════════════════════════════════════════════════════════
WHERE THIS COMES FROM, AND WHAT NOT HAVING IT COST
════════════════════════════════════════════════════════════════════════════
Contributed by **KarmaKadabra**, MeshRelay's `#agents` channel, **2026-08-30**.
Their sentence, verbatim:

    *«Un 200 que no hizo la cosa es peor que un 503, porque el cliente lo toma
    por bueno: si nosotros no chequeáramos el tx, habríamos contado 14 ratings
    que no existen. Nosotros lo cazamos con un sello que exige forma de hash —
    pero el contrato debería decirlo, no cada cliente descubrirlo.»*

    [translation] "A 200 that did not do the thing is worse than a 503, because
    the client takes it for good: if we did not check the tx, we would have
    counted 14 ratings that do not exist. We catch it with a stamp that demands
    hash shape — but the contract should say so, not every client discover it."

That last half is the reason this lives in the SDK and not in every consumer: the
check belongs to the CONTRACT. Verified before writing a line: neither this SDK
nor its TypeScript twin validated the shape of any hash field.

════════════════════════════════════════════════════════════════════════════
🔴 WHY A `^0x[0-9a-f]{64}$` IS NOT ENOUGH, AND IT IS MEASURED
════════════════════════════════════════════════════════════════════════════
The obvious first version of this file — an EVM-hash regex — would have flagged
**every Solana rating in the index** as malformed. Measured live against
`GET /feed?network=…` on **2026-08-30**:

    avalanche  0x785f14ba…a015   66 chars   `0x` + 64 hex
    celo       0x5f7c6e78…f813   66 chars   `0x` + 64 hex
    solana     2va3P3Q6…CTXjJ    88 chars   base58, NO `0x`
    solana     kvqERVGQ…8JLy     87 chars   base58, NO `0x`

A validator that only knew the EVM shape would have screamed about perfectly good
data, and an alarm that screams about good data is worse than none: it gets
learned into being ignored, and the day it fires for the real bug nobody looks.
That is why what is validated here is **the union** of the legitimate shapes, not
the intersection.

The four shapes the index emits today, each with its source:

1. **EVM hash** — `0x` + 64 hex (66 chars). The service writes it that way:
   `describe-net/describenet/chain/decode.py:253` does `"0x" + …hex()` over a
   bytes32. It is the shape of `tx_hash`, `revoked_tx` and `feedback_hash` on the
   10 EVM chains.
2. **Solana signature** — base58 (Bitcoin alphabet, no `0`/`O`/`I`/`l`), 86–88
   chars: 64 bytes in base58 give 87–88, and 86 is possible with leading zero
   bytes. Both high lengths measured live (above). On Solana `feedback_hash` is
   **NULL on purpose** — `solana_indexer.py:27`'s docstring says so — so a gap
   there is correct, not a shortcoming.
3. **Bare digest** — 64 hex **without** `0x`. That is `Snapshot.inputs_digest`,
   which comes out of `hashlib.sha256(...).hexdigest()` in `aggregate.py:1920`.
   Demanding the `0x` prefix would have flagged **every citable snapshot** as
   malformed.
4. **The `pending` sentinel** — only in the `X-Payment-Receipt` header. The live
   OpenAPI declares it itself: *"the on-chain settlement transaction hash … or
   `pending` if settlement has not reported one"*. It is a LEGITIMATE value, and
   that is why `looks_like_settlement_receipt()` exists separately: treating it as
   malformed would fire an alarm on every payment whose settlement has not
   reported yet, i.e. on the happy path.

════════════════════════════════════════════════════════════════════════════
WHAT QUESTION THIS MODULE ANSWERS — AND WHICH ONE IT DOES NOT
════════════════════════════════════════════════════════════════════════════
It answers **"is this shaped like an on-chain identifier?"**. It does not answer
"does this transaction exist?" nor "does it say what the index says?": that is
verified in an explorer, which is exactly the point of the field (the service's
schema says so: *"go verify it in an explorer, which is the point"*). A shape
validator cannot prove existence and does not pretend to.

What it does catch, which is what bit KK: the empty string, `"null"`,
`"undefined"`, `"0x"`, `"0x0"`, a truncated hash, an error message stuffed into
the field. All of that passes an `if tx_hash:` today without anything squeaking.

🔴 **It deliberately does NOT validate per chain.** A `feedback_hash` shaped like
base58 is accepted even though Solana emits none today. Being strict per field
would trade a cheap false negative (letting through a rare but valid shape) for an
expensive false positive (screaming about good data the day the service extends a
field), and that contradicts the additive tolerance `models.py` defends: type what
is known and preserve what is not.
"""

from __future__ import annotations

import re

#: `0x` + 64 hex. Insensible a mayúsculas: el índice sirve minúsculas, pero un
#: hash con checksum EIP-55 mezclado sigue siendo el mismo hash y marcarlo sería
#: un falso positivo.
_EVM_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")

#: 64 hex SIN prefijo — `hashlib.sha256(...).hexdigest()`, o sea `inputs_digest`.
_BARE_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")

#: base58 del alfabeto Bitcoin (sin `0`, `O`, `I`, `l`), 86–88 chars: una firma
#: de transacción de Solana. Medidas en vivo 87 y 88 el 2026-08-30.
_BASE58_SIGNATURE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{86,88}$")

#: The only non-hash value the `X-Payment-Receipt` header declares legitimate. The
#: live OpenAPI says so, it is not an assumption of ours. See §4 of the header.
SETTLEMENT_PENDING = "pending"


def looks_like_onchain_id(value: str) -> bool:
    """Is `value` shaped like an on-chain identifier this index emits?

    The UNION of the three measured shapes (EVM hash, Solana signature, bare
    digest), never the intersection: see the module header for why an EVM-hash
    regex alone would have flagged every Solana rating as malformed.

    It does not prove the transaction exists — that is the explorer's job.
    """
    return bool(
        _EVM_HASH.match(value)
        or _BASE58_SIGNATURE.match(value)
        or _BARE_DIGEST.match(value)
    )


def looks_like_settlement_receipt(value: str) -> bool:
    """Same as `looks_like_onchain_id`, plus the `pending` sentinel.

    For `X-Payment-Receipt` only. `pending` is not garbage: it is the service
    saying "I charged and settlement has not reported the hash to me yet", and it
    is in their OpenAPI. Marking it malformed would turn the happy path of every
    freshly settled payment into an alarm.
    """
    return value == SETTLEMENT_PENDING or looks_like_onchain_id(value)
