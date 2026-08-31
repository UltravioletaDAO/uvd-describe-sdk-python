"""The one toll booth — R6. This SDK **never** signs, custodies or derives.

════════════════════════════════════════════════════════════════════════════
THE RULE, AND IT IS NOT NEGOTIABLE
════════════════════════════════════════════════════════════════════════════
The 402 is resolved by `uvd-x402-sdk` (PyPI `uvd-x402-sdk`, 0.70.0 as of
2026-08-30). Here **EIP-3009 is not reimplemented, the envelope is not built by
hand, no private key is touched and no env var with a secret is read**. It is the
house's *upstream-first* rule: if the payments SDK is missing something, it goes
up THERE and is consumed afterwards — it is not patched here.

What this module does is exactly three things, and none of them is cryptographic:

  1. **Verify who is about to be paid** against the pinned treasury.
  2. **Pick** which of the `accepts[]` matches the network the caller says they
     hold funds on.
  3. **Delegate** the signing to the payer, echoing the accept back VERBATIM.

════════════════════════════════════════════════════════════════════════════
🔴 THE RECIPIENT CHECK: `DO_NOT_PAY`, NOT A RETRY
════════════════════════════════════════════════════════════════════════════
Step 2 of the published guide (docs.describe.net, "Paying with x402"):

    "The only address this service ever asks to be paid at is
     0xe4dc963c56979E0260fc146b87eE24F18220e545. If the challenge names another
     address, do not pay: either it did not come from describe, or the treasury
     changed and the server did not find out. Raw HTTP does not make this
     comparison for you. Pin the address in your own code."

Retrying there would turn a diversion of funds into a diversion of funds with
retries. That is why `DoNotPayError` is **not swallowed by the fail-open** and
there is no `retries=` that can override it.

The treasury is pinned as a default and configurable by constructor — for your own
deployment of the index, not to "turn the check off". If somebody passes
`treasury=None` nothing is paid: the payer's own `ConfigurationError` is raised or
the check fails. There is never a silent path.

════════════════════════════════════════════════════════════════════════════
WHY THERE IS NO `network name → chain id` TABLE IN THIS REPO
════════════════════════════════════════════════════════════════════════════
The `accepts[].network` arrives in CAIP-2 (`eip155:8453`) and
`create_authorization` wants a name (`"base"`). The translation **is asked of the
`uvd-x402-sdk`'s registry**, which owns it: `get_supported_network_names()` is
walked and matched by `chain_id`. A local table would be a copy that rots, and
copying what the SDK already knows is exactly what upstream-first forbids.

Verified 2026-08-30 against `uvd_x402_sdk.networks.base`: the six chains describe
accepts today — 8453 base, 43114 avalanche, 42161 arbitrum, 10 optimism, 137
polygon, 42220 celo — all six resolve in the registry.

════════════════════════════════════════════════════════════════════════════
THE `payer` IS A PROTOCOL, NOT A HARD DEPENDENCY
════════════════════════════════════════════════════════════════════════════
`uvd-x402-sdk` is an **extra** (`pip install uvd-describe-sdk[x402]`), not a base
dependency. A consumer who only uses the free routes — which is the product's
happy path: the "free-first" rule, and the 402 itself says so: *"if there is no
reputation there, this charge returns nothing"* — does not drag in `eth-account`
or anything that signs.

`X402Client` satisfies `Payer` structurally, without inheriting anything of ours.
And for the tests that counts double: it is mocked with a ten-line object and **no
test touches a key**.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - depende de la versión de Python
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable

from .errors import DoNotPayError

#: describe's treasury, pinned. Read LIVE from the challenge of
#: `GET /reputation/wallet/{w}` on 2026-08-30 (fields `recipient` and
#: `recipients.evm`, and the `payTo` of the six `accepts[]` entries). It matches
#: the service's `paywall.TREASURY_EVM`, which keeps it in sync with
#: `api_docs.PUBLISHED_TREASURY` and with its Terraform (invariant 5 of the repo).
TREASURY_EVM = "0xe4dc963c56979E0260fc146b87eE24F18220e545"

#: USDC tiene 6 decimales en las seis cadenas que describe acepta. Se usa SÓLO
#: para reconciliar el precio en dólares contra las unidades base que mandó el
#: servidor — nunca para construir el monto, que lo calcula el SDK de pagos.
_USDC_DECIMALS = 6


@runtime_checkable
class Payer(Protocol):
    """The only thing this SDK asks of whoever signs.

    `uvd_x402_sdk.X402Client` satisfies it as-is, inheriting nothing:

        from uvd_x402_sdk import X402Client
        payer = X402Client(recipient_address=TREASURY_EVM)
        payer.connect_with_private_key(os.environ["MY_KEY"], chain="base")
        client = DescribeClient(payer=payer, pay_network="base")

    🔴 The key comes from an env var or from a remote signer
    (`connect_with_signer`). **It is NEVER written into a file**, not even "just
    to test": there are bots sweeping GitHub for `0x` + 64 hex and they drain in
    minutes (the house's INC-2026-03-30, two wallets lost that way).
    """

    def create_authorization(
        self,
        pay_to: str,
        amount_usd: Decimal,
        *,
        chain_name: Optional[str] = ...,
        x402_version: int = ...,
        accepted: Optional[Dict[str, Any]] = ...,
        resource: Optional[Any] = ...,
    ) -> str:
        """Returns the base64 value of the `X-PAYMENT` header."""
        ...


def _chain_id_of(caip2: str) -> Optional[int]:
    """`"eip155:8453"` → `8453`. Anything else → `None`.

    The prefix is not assumed: a `solana:...` has no EVM chain id and returning
    something here would send it to sign against the wrong network.
    """
    if not isinstance(caip2, str) or not caip2.startswith("eip155:"):
        return None
    try:
        return int(caip2.split(":", 1)[1])
    except ValueError:
        return None


def _registry_available() -> bool:
    """Is `uvd-x402-sdk` installed — that is, the owner of the network registry?

    It exists as a separate question because of a lesson this very file swallowed
    while being written: without the extra installed, `chain_name_for` returned
    `None` for all six chains and `select_accept` raised **"describe does not offer
    paying in `base`"** — a message that sends you to check the service's challenge
    when the problem is a missing `pip install`.

    It is exactly the failure mode the service repo hunts: *"an entry that sends
    you to the wrong place is worse than none"*, because whoever reads it runs the
    recipe, stays red, and stops looking. So the cause is told apart and named.
    """
    try:
        import uvd_x402_sdk.networks.base  # noqa: F401
    except ImportError:
        return False
    return True


def chain_name_for(caip2: str) -> Optional[str]:
    """CAIP-2 → `uvd-x402-sdk`'s network name, by asking its registry.

    Returns `None` if the payments SDK is not installed or does not know that
    chain. `_registry_available()` exists to tell those two causes apart. No local
    table: see the module header.
    """
    chain_id = _chain_id_of(caip2)
    if chain_id is None:
        return None
    try:
        from uvd_x402_sdk.networks.base import get_network, get_supported_network_names
    except ImportError:
        return None
    for name in get_supported_network_names():
        network = get_network(name)
        if network is not None and getattr(network, "chain_id", None) == chain_id:
            # `str()` y no `name` a secas: el registro del SDK de pagos no trae
            # `py.typed`, asi que mypy ve `Any` y `warn_return_any` lo marca.
            # Es un hueco UPSTREAM (ver README, "Lo que le falta al SDK de
            # pagos"): se convierte aca en vez de silenciar el warning.
            return str(name)
    return None


def _matches(accept: Dict[str, Any], network: str) -> bool:
    """Is this accept the network the caller asked for?

    The name (`"base"`), the CAIP-2 (`"eip155:8453"`) and the chain id (`8453` or
    `"8453"`) are all accepted. The last two **do not need the payments SDK's
    registry**, so whoever does not have it installed can still select — and the
    error, if something is missing, will name what is really missing.
    """
    caip2 = str(accept.get("network") or "")
    if network == caip2:
        return True
    chain_id = _chain_id_of(caip2)
    if chain_id is not None and str(network) == str(chain_id):
        return True
    return chain_name_for(caip2) == network


def select_accept(challenge: Dict[str, Any], network: str) -> Dict[str, Any]:
    """Pick the `accepts[]` entry for the network the caller holds funds on.

    **The entry is returned exactly as it arrived**, neither normalised nor
    rebuilt: `uvd-x402-sdk` echoes it back VERBATIM in the v2 envelope and its own
    code warns about it — *"Reconstructing it instead of echoing is how a payment
    gets rejected by a server that did nothing wrong"* (`client.py:1799-1802`).

    If the requested network is not among the offered ones, the error **lists the
    ones that are**: an "unsupported network" without the list forces you to read
    the challenge by hand.
    """
    accepts = challenge.get("accepts") or []
    offered: List[str] = []
    for accept in accepts:
        if not isinstance(accept, dict):
            continue
        caip2 = str(accept.get("network") or "")
        offered.append(chain_name_for(caip2) or caip2)
        if _matches(accept, network):
            return accept

    falta_el_sdk = (
        "" if _registry_available() else
        " — heads up: `uvd-x402-sdk` is NOT installed, so network names cannot be "
        "resolved and what you see above are the raw CAIP-2 ids. Install the "
        "extra: `pip install uvd-describe-sdk[x402]`"
    )
    raise DoNotPayError(
        f"describe does not offer paying in `{network}`; it offers: "
        f"{', '.join(offered) or '(none)'}{falta_el_sdk}",
        expected=network,
        offered=", ".join(offered),
    )


def assert_recipient(accept: Dict[str, Any], treasury: str) -> None:
    """The step 2 check. It fails ⇒ `DO_NOT_PAY`, never a retry.

    The comparison is case-insensitive because an EVM address is the same with or
    without the EIP-55 checksum — the challenge sends it with uppercase and a
    wallet may hold it lowercased. What is NOT loosened is the address: it compares
    the 20 bytes, not a prefix.
    """
    pay_to = str(accept.get("payTo") or "")
    if not pay_to or pay_to.lower() != treasury.lower():
        raise DoNotPayError(
            "the 402 asks to pay an address that is NOT describe's pinned "
            "treasury. NOTHING IS PAID and this is NOT retried. "
            f"expected={treasury} offered={pay_to or '(empty)'}",
            expected=treasury,
            offered=pay_to,
        )


def _amount_usd(challenge: Dict[str, Any], accept: Dict[str, Any]) -> Decimal:
    """The USD price, taken from the challenge and **reconciled** with base units.

    `Decimal`, never `float`: a `float("0.01")` is no longer 0.01 and this is
    money.

    The reconciliation exists because of a failure mode the published guide names:
    *"A 4xx after paying is almost always a spent credential or one signed for a
    different amount"*. If `price_usd` and `accepts[].amount` do not match, signing
    anyway produces a payment the server rejects **after** the credential has been
    consumed. We would rather refuse first.

    Reconciliation only happens when the declared token is USDC (6 decimals on
    today's six chains). With another token no scale is invented: it signs for
    `price_usd` and it is stated here that this case is not covered.
    """
    raw_price = challenge.get("price_usd", challenge.get("amount"))
    if raw_price is None:
        raise DoNotPayError(
            "the 402 carries neither `price_usd` nor `amount`: there is no price "
            "to sign for",
            expected="price_usd",
            offered="(absent)",
        )
    price = Decimal(str(raw_price))

    token = str(challenge.get("token") or "").upper()
    base_units = accept.get("amount")
    if token == "USDC" and base_units is not None:
        expected_base = int(price * (10**_USDC_DECIMALS))
        if int(base_units) != expected_base:
            raise DoNotPayError(
                f"the 402 does not agree with itself: price_usd={price} is "
                f"{expected_base} base units but `accepts[].amount` says "
                f"{base_units}. Signing for an amount other than the one asked for "
                "spends the credential and the server answers 4xx anyway.",
                expected=str(expected_base),
                offered=str(base_units),
            )
    return price


def build_payment_header(
    payer: Payer,
    challenge: Dict[str, Any],
    *,
    network: str,
    treasury: str = TREASURY_EVM,
) -> str:
    """From the 402 challenge to the `X-PAYMENT` header value. The payer signs.

    Deliberate order: **first verify who is being paid, then sign.** The other way
    round there would exist, if only for an instant, an authorization signed
    towards an unverified address.

    Everything that travels comes out of the challenge — amount, token, `payTo`,
    network, resource — and nothing from a local table: step 1 of the published
    guide, *"Take the values from there, never from a cached table."*
    """
    accept = select_accept(challenge, network)
    assert_recipient(accept, treasury)
    amount = _amount_usd(challenge, accept)

    chain_name = chain_name_for(str(accept.get("network") or ""))
    if chain_name is None:
        # Acá SÍ hace falta el registro: `create_authorization` quiere un nombre
        # y no hay forma de derivarlo de un chain id sin quien lo sepa. El
        # mensaje nombra la causa real en vez de mandar a mirar el challenge.
        detalle = (
            "`uvd-x402-sdk` is not installed: `pip install uvd-describe-sdk[x402]`"
            if not _registry_available()
            else f"its network registry does not know `{accept.get('network')}`"
        )
        raise DoNotPayError(
            f"cannot sign for `{accept.get('network')}` — {detalle}",
            expected=network,
            offered=str(accept.get("network")),
        )

    # `resource` como OBJETO, no como el string pelado del challenge: el
    # facilitator exige url + description + mimeType y un string suelto no
    # matchea ninguna variante de su envelope, fallando con el opaco «data did
    # not match any variant» (documentado en `uvd_x402_sdk/client.py:1846-1852`).
    resource = {
        "url": str(challenge.get("resource") or ""),
        "description": str(challenge.get("description") or ""),
        "mimeType": str(challenge.get("mimeType") or "application/json"),
    }

    return payer.create_authorization(
        pay_to=str(accept["payTo"]),
        amount_usd=amount,
        chain_name=chain_name,
        # v2 porque es lo que describe anuncia: `x402Version: 2`, medido vivo el
        # 2026-08-30. En v2 el accept elegido se echa de vuelta VERBATIM.
        x402_version=int(challenge.get("x402Version") or 2),
        accepted=dict(accept),
        resource=resource,
    )
