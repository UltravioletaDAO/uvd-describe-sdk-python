"""uvd-describe-sdk — the Python client of describe's ERC-8004 reputation index.

    pip install uvd-describe-sdk

    from uvd_describe_sdk import DescribeClient, format_score

    with DescribeClient(product="my-app") as describe:
        rep = describe.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
        if rep is None:
            print("the index did not answer")        # NOT "no reputation"
        elif not rep.has_identity:
            print("not registered")
        else:
            print(format_score(rep.global_score), "·", rep.policy_version)

════════════════════════════════════════════════════════════════════════════
THE THREE THINGS TO KNOW BEFORE USING IT
════════════════════════════════════════════════════════════════════════════
1. **`None` is never zero, and never "has no reputation".** A method returning
   `None` is saying *there was no answer*. A wallet with no ratings comes back as
   an object with `global_score is None`. They are different facts and the type
   keeps them different. (`models.py` §R1)

2. **No method returns a bare number.** Every result carries `policy_version`,
   `caveats[]` and its source. If you only want the number you take it off the
   object by hand — and that is on purpose: *a score without its raters is a
   rumour*, and taking it out leaves written in your code that you decided to
   throw the context away. (`models.py` §R2)

3. **You branch on `caveats[].code`, never on `caveats[].text`.** The text may be
   rewritten without notice; the code never changes. All eight are exported in
   `CaveatCode` so you do not type them. (`caveats.py` §R3)

════════════════════════════════════════════════════════════════════════════
WHAT THIS SDK DOES NOT DO
════════════════════════════════════════════════════════════════════════════
It signs nothing. It custodies no key. It does not implement EIP-3009. The x402
payment is resolved by `uvd-x402-sdk`, which is an optional **extra**
(`pip install uvd-describe-sdk[x402]`) and is only needed for the two metered
routes. The free path drags in not a single cryptography dependency.

Nor does it write to any chain, or issue ratings: it is a READER.

════════════════════════════════════════════════════════════════════════════
THE PARTNER RAIL — IF DESCRIBE ALLOWLISTED YOUR WALLET, THE METERED ONES ARE $0
════════════════════════════════════════════════════════════════════════════
    from uvd_x402_sdk.wallet import EnvKeyAdapter    # reads WALLET_PRIVATE_KEY
    with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as d:
        b = d.wallet_breakdown("0x97cd…0996")        # $0.01 for a third party

It signs ERC-8128, not a token: describe stores your PUBLIC ADDRESS, never a
secret of yours. 🔴 **This SDK still does not touch your key** — `partner=`
receives an object that signs. And if the rail breaks, the client **raises**
(`PartnerRejectedError`) instead of paying silently. See `partner.py`.
"""

from __future__ import annotations

from .badge import badge_img_tag, badge_url
from .caveats import (
    CAVEAT_CODES_MEASURED_AT,
    FREE_GATE_CAVEAT_CODES,
    KNOWN_CAVEAT_CODES,
    CaveatCode,
    is_known,
)
from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_JITTER_S,
    DEFAULT_PAY_NETWORK,
    DEFAULT_TIMEOUT_S,
    DescribeClient,
    ErrorObserver,
)
from .display import NO_SCORE_PLACEHOLDER, format_score
from .errors import (
    HTTP_5XX_LEGACY_KIND,
    DescribeError,
    DescribeHTTPError,
    DescribeMalformedHash,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
    DoNotPayError,
    PartnerRejectedError,
    PartnerSigningError,
    PaymentRequiredError,
)
from .hashes import SETTLEMENT_PENDING, looks_like_onchain_id, looks_like_settlement_receipt
from .models import (
    Activity,
    AgentReputation,
    Breakdown,
    Caveat,
    ChainHealth,
    ChainReputation,
    ChainScore,
    Concentration,
    Confidence,
    ConfidenceInterval,
    Facet,
    IndexHealth,
    LeaderboardRow,
    Ownership,
    PaymentReceipt,
    Rating,
    SelfRated,
    Snapshot,
    WalletReputation,
    malformed_hash_report,
)
from .partner import (
    PARTNER_AUTHORITY,
    PARTNER_CHAIN_ID,
    PartnerSignature,
    PartnerSigner,
    sign_partner_headers,
)
from .payment import TREASURY_EVM, Payer, build_payment_header, chain_name_for
from .version import USER_AGENT_NAME, __version__, default_user_agent

__all__ = [
    "SETTLEMENT_PENDING",
    # client
    "DescribeClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_PAY_NETWORK",
    # jitter — contributed by KarmaKadabra (27 agents), ON by default
    "DEFAULT_JITTER_S",
    "ErrorObserver",
    # display (R8)
    "format_score",
    "NO_SCORE_PLACEHOLDER",
    # badge — the copy-paste surface, no network
    "badge_url",
    "badge_img_tag",
    # caveats (R3)
    "CaveatCode",
    "KNOWN_CAVEAT_CODES",
    "FREE_GATE_CAVEAT_CODES",
    "CAVEAT_CODES_MEASURED_AT",
    "is_known",
    # errors (R4)
    "DescribeError",
    "DescribeTimeout",
    "DescribeHTTPError",
    "DescribeUnreachable",
    "DescribeUnparseable",
    # 🔴 NEVER raised: it travels through `on_error`. See its docstring.
    "DescribeMalformedHash",
    "PaymentRequiredError",
    "DoNotPayError",
    "PartnerSigningError",
    "PartnerRejectedError",
    "HTTP_5XX_LEGACY_KIND",
    # models
    "WalletReputation",
    "ChainReputation",
    "Breakdown",
    "AgentReputation",
    "LeaderboardRow",
    "IndexHealth",
    "ChainHealth",
    "Caveat",
    "Confidence",
    "ConfidenceInterval",
    "Concentration",
    "SelfRated",
    "Activity",
    "Snapshot",
    "ChainScore",
    "Facet",
    "Ownership",
    "Rating",
    "PaymentReceipt",
    # hash shape validation — contributed by KarmaKadabra
    "looks_like_onchain_id",
    "looks_like_settlement_receipt",
    "malformed_hash_report",
    # payment (R6)
    "Payer",
    "TREASURY_EVM",
    "build_payment_header",
    "chain_name_for",
    # partner rail — ERC-8128 signing, ZERO keys in this SDK
    "PartnerSigner",
    "PartnerSignature",
    "PARTNER_CHAIN_ID",
    "PARTNER_AUTHORITY",
    "sign_partner_headers",
    # version / attribution
    "__version__",
    "USER_AGENT_NAME",
    "default_user_agent",
]
