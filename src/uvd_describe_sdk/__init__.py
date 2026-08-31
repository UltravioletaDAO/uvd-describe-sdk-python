"""uvd-describe-sdk — el cliente Python del índice de reputación ERC-8004 de describe.

    pip install uvd-describe-sdk

    from uvd_describe_sdk import DescribeClient, format_score

    with DescribeClient(product="mi-app") as describe:
        rep = describe.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
        if rep is None:
            print("el índice no contestó")          # NO es «sin reputación»
        elif not rep.has_identity:
            print("no registrada")
        else:
            print(format_score(rep.global_score), "·", rep.policy_version)

════════════════════════════════════════════════════════════════════════════
LAS TRES COSAS QUE HAY QUE SABER ANTES DE USARLO
════════════════════════════════════════════════════════════════════════════
1. **`None` nunca es cero, y nunca es «no tiene reputación».** Un método que
   devuelve `None` está diciendo *no hubo respuesta*. Una wallet sin
   calificaciones vuelve como un objeto con `global_score is None`. Son hechos
   distintos y el tipo los mantiene distintos. (`models.py` §R1)

2. **Ningún método devuelve un número pelado.** Todo resultado trae
   `policy_version`, `caveats[]` y su fuente. Si querés sólo el número lo sacás
   del objeto a mano — y eso es a propósito: *un score sin sus calificadores es
   un rumor*, y sacarlo deja escrito en tu código que decidiste tirar el
   contexto. (`models.py` §R2)

3. **Se ramifica por `caveats[].code`, jamás por `caveats[].text`.** El texto
   puede reescribirse sin aviso; el código no cambia nunca. Los ocho están
   exportados en `CaveatCode` para que no los tipees. (`caveats.py` §R3)

════════════════════════════════════════════════════════════════════════════
LO QUE ESTE SDK NO HACE
════════════════════════════════════════════════════════════════════════════
No firma nada. No custodia una clave. No implementa EIP-3009. El pago x402 lo
resuelve `uvd-x402-sdk`, que es un **extra** opcional
(`pip install uvd-describe-sdk[x402]`) y sólo hace falta para las dos rutas
medidas. El camino gratis no arrastra una sola dependencia de criptografía.

Tampoco escribe en ninguna cadena, ni emite calificaciones: es un LECTOR.

════════════════════════════════════════════════════════════════════════════
EL RIEL DE PARTNER — SI DESCRIBE DIO DE ALTA TU WALLET, LAS MEDIDAS SON $0
════════════════════════════════════════════════════════════════════════════
    from uvd_x402_sdk.wallet import EnvKeyAdapter    # lee WALLET_PRIVATE_KEY
    with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as d:
        b = d.wallet_breakdown("0x97cd…0996")        # $0,01 para un tercero

Firma ERC-8128, no un token: describe guarda tu DIRECCIÓN pública, nunca un
secreto tuyo. 🔴 **Este SDK sigue sin tocar tu clave** — `partner=` recibe un
objeto que firma. Y si el riel se rompe, el cliente **levanta**
(`PartnerRejectedError`) en vez de pagar en silencio. Ver `partner.py`.
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
from .hashes import looks_like_onchain_id, looks_like_settlement_receipt
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
    # cliente
    "DescribeClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_PAY_NETWORK",
    # jitter — aporte de KarmaKadabra (27 agentes), PRENDIDO por default
    "DEFAULT_JITTER_S",
    "ErrorObserver",
    # display (R8)
    "format_score",
    "NO_SCORE_PLACEHOLDER",
    # badge — la superficie copy-paste, sin red
    "badge_url",
    "badge_img_tag",
    # caveats (R3)
    "CaveatCode",
    "KNOWN_CAVEAT_CODES",
    "FREE_GATE_CAVEAT_CODES",
    "CAVEAT_CODES_MEASURED_AT",
    "is_known",
    # errores (R4)
    "DescribeError",
    "DescribeTimeout",
    "DescribeHTTPError",
    "DescribeUnreachable",
    "DescribeUnparseable",
    # 🔴 NUNCA se levanta: viaja por `on_error`. Ver su docstring.
    "DescribeMalformedHash",
    "PaymentRequiredError",
    "DoNotPayError",
    "PartnerSigningError",
    "PartnerRejectedError",
    "HTTP_5XX_LEGACY_KIND",
    # modelos
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
    # validación de forma de los hashes — aporte de KarmaKadabra
    "looks_like_onchain_id",
    "looks_like_settlement_receipt",
    "malformed_hash_report",
    # pago (R6)
    "Payer",
    "TREASURY_EVM",
    "build_payment_header",
    "chain_name_for",
    # riel de partner — firma ERC-8128, CERO claves en este SDK
    "PartnerSigner",
    "PartnerSignature",
    "PARTNER_CHAIN_ID",
    "PARTNER_AUTHORITY",
    "sign_partner_headers",
    # versión / atribución
    "__version__",
    "USER_AGENT_NAME",
    "default_user_agent",
]
