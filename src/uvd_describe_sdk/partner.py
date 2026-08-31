"""The PARTNER rail — getting into the metered routes without spending a cent.

════════════════════════════════════════════════════════════════════════════
WHAT IT IS, AND WHY IT IS NOT A TOKEN
════════════════════════════════════════════════════════════════════════════
describe has no accounts and no API keys — "the payment is the authentication" —
and yet the house's services (Execution Market, KarmaKadabra, MeshRelay) delegated
their reputation reads here and query hundreds of times a day. The service solved
that on 2026-08-28 with a mechanism that **does not require describe to custody
anybody else's secret**: an allowlist of PUBLIC ADDRESSES, with each partner
signing its requests with a dedicated wallet.

    `describe-net/describenet/partner.py` — the gate, on the other side.

It is better than a token because of a property no token has: the allowlist can be
committed, logged and published in a `get-function-configuration` without leaking
anything, because **a breach of describe compromises no partner**. There is no
credential of yours over there to steal. Only your address, which is public.

This module is the client half of that mechanism: it signs the request with the
`uvd-x402-sdk` primitive and returns the two RFC 9421 headers.

════════════════════════════════════════════════════════════════════════════
🔴 THERE IS NO PRIVATE KEY HERE, AND THERE NEVER WILL BE
════════════════════════════════════════════════════════════════════════════
**This module does not read an env var, does not accept a key by parameter, does
not store one and does not see one.** It receives an injected SIGNING OBJECT
(`PartnerSigner`) and asks it two things: `get_address()` and `sign_message()`.
The key stays on the signer's side — an env-var adapter, a KMS, a remote signer, a
Ledger.

    🔴 NEVER write a private key into a file, not "temporarily", not "just to
    test". There are bots sweeping GitHub for `0x` + 64 hex that drain in minutes:
    the house has already lost TWO wallets that way (INC-2026-03-30).

And use a **dedicated wallet with no funds**: all it does is sign. That is the
property that makes the worst case cheap — a leaked signature works against the
SAME method and the SAME URL, and only for 300 seconds (the gate's
`MAX_VALIDITY_SEC`). It is not a permanent credential and it cannot move money.

    # The key comes from the CONSUMER's environment, never from this SDK.
    from uvd_x402_sdk.wallet import EnvKeyAdapter   # reads WALLET_PRIVATE_KEY
    from uvd_describe_sdk import DescribeClient

    with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as d:
        b = d.wallet_breakdown("0x97cd…0996")   # $0.01 for a third party, $0 here

════════════════════════════════════════════════════════════════════════════
UPSTREAM-FIRST: THE PRIMITIVE WAS ALREADY THERE, AND IT WAS MEASURED FIRST
════════════════════════════════════════════════════════════════════════════
`uvd-x402-sdk` **0.70.0 already signs ERC-8128** — `sign_request` from
`uvd_x402_sdk.erc8128`, with EM's fleet's golden vectors pinned inside the
package. So RFC 9421 is NOT implemented here, no signature base is assembled and
EIP-191 is not touched: it is delegated, just as `payment.py` delegates EIP-3009.

Measured 2026-08-30 against the service's REAL gate (importing
`describenet.partner` and its own `VerifyPolicy`, with an ephemeral in-memory
key):

    verify_request ok = True · recovered wallet == the one that signed
    PartnerGate.check with a LISTED wallet      -> 'mi-partner'
    PartnerGate.check with an UNLISTED wallet   -> None   (charges; the
                                                           discriminating case)
    signed against `localhost:8088`             -> None   (wrong authority)
    signed WITHOUT query, requested WITH query  -> None   ← see below

And the payments SDK's defaults **already match** what the gate demands, which is
not luck but the same ecosystem: `DEFAULT_CHAIN_ID = 8453` (Base),
`DEFAULT_VALIDITY_SEC = 300`, `alg="eip191"`, lowercase keyid. The `chain_id` is
passed EXPLICITLY anyway (see `PARTNER_CHAIN_ID`).

The loop was closed end to end the same day: a real `DescribeClient`, with a real
`eth_account` signature, against the service's real `PartnerGate` — the only thing
simulated was the transport. It is the evidence this repo's suite CANNOT give,
because it runs without cryptography on purpose:

    [A] wallet ON the allowlist -> the gate answers partner='meshrelay'
        wallet_breakdown -> Breakdown(final_score=86.653045), ONE single
        request, and the payer was never touched
    [B] wallet OFF it (the real state of KK and mesh today) -> the gate charges
        -> PartnerRejectedError · payment_sent=False · wallet=0xC259…861c
           · price_usd='0.01'  ← what the broken rail was going to cost

════════════════════════════════════════════════════════════════════════════
🔴 WHAT IS SENT IS WHAT IS SIGNED, BYTE FOR BYTE — QUERY INCLUDED
════════════════════════════════════════════════════════════════════════════
The signature base covers `@method`, `@authority`, `@path` and — **only when the
URL has a query** — `@query`. The fifth line of the measurement above is the
failure mode: a signature made over the URL without `?snapshot=true` and sent with
`?snapshot=true` **does not verify**, and the partner falls to the 402 without
understanding why.

That is why the client signs the URL `httpx` already built (`build_request`) and
not one it rebuilds by hand: the one signed and the one that goes out are the same
string **by construction**, not by care. `test_partner_riel_gratis.py` pins it by
comparing the `"@path"` / `"@query"` lines of the signed base against the URL the
transport saw leave.

And the `authority` comes from the URL, i.e. from your `base_url`. Consequence:
**if you point the client at any host other than `api.describe.net`, the rail does
not work** — the gate rebuilds the base against its pinned authority and
deliberately never against the request's Host (deriving the authority from a
header the client controls is how somebody else's verifier got broken). It is not
a bug of this SDK: it is fail-closed, and you find out loudly through
`PartnerRejectedError`.

════════════════════════════════════════════════════════════════════════════
CHARGE-BY-DEFAULT IS THE SERVER'S, AND THAT IS WHAT MAKES IT A GUARANTEE
════════════════════════════════════════════════════════════════════════════
Nothing that happens in this file can exempt anybody. The allowlist lives in the
service; an absent, empty or invalid-JSON env ⇒ EMPTY allowlist ⇒ 402 for
everyone. This module only produces two headers; the one who decides is the other
side. Put the other way round: **there is no way for a bug in this SDK to give the
product away**, and there is one for it to make you spend USDC silently. Against
that one stands `PartnerRejectedError` (see `client.py::_paid`).
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

try:  # pragma: no cover - depende de la versión de Python
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable

from .errors import PartnerSigningError

#: The chain of the ERC-8128 keyid (`erc8128:<chain>:0x…`). **8453, Base**, and
#: the number is not chosen here: it is pinned by the service's gate
#: (`describenet/partner.py::CHAIN_ID`), which fixes ONE on purpose because "any
#: chain" would be needlessly lax for an allowlist of three of our own services.
#: It is passed EXPLICITLY to `sign_request` even though the payments SDK's
#: default matches today (measured 2026-08-30: `DEFAULT_CHAIN_ID = 8453`):
#: inheriting somebody else's default for a value the server compares is signing
#: against whatever another repo decides tomorrow.
PARTNER_CHAIN_ID = 8453

#: The domain the gate rebuilds the base against. It is NOT used for signing —
#: the `authority` comes from the URL that is sent, see the header — and it is
#: published only so an error can say which authority you signed against and which
#: one you were going to be verified against. Mirror of
#: `describenet/partner.py::AUTHORITY`.
PARTNER_AUTHORITY = "api.describe.net"


@runtime_checkable
class PartnerSigner(Protocol):
    """The only thing this SDK asks of whoever signs. **The key never leaves.**

    It is, on purpose, the same pair of methods as `uvd-x402-sdk`'s
    `WalletAdapter`: any adapter of theirs satisfies it **structurally**, without
    inheriting anything of ours, and your own signer (KMS, HSM, remote signer)
    fits with two methods and without importing a line of this package.

        class MyRemoteSigner:
            def get_address(self) -> str: ...        # the PUBLIC address
            def sign_message(self, message: str) -> str: ...   # EIP-191, hex

    `sign_message` receives the RFC 9421 signature base (plain text) and returns
    the `personal_sign` signature in hex. That the interface is two methods is
    what makes this repo's tests run **without a single key and without
    cryptography**: the double returns fixed hex.
    """

    def get_address(self) -> str:
        """The signer's EVM address. It is PUBLIC: it goes on the allowlist."""
        ...

    def sign_message(self, message: str) -> str:
        """EIP-191 (`personal_sign`) signature of the message, in hex."""
        ...


class PartnerSignature:
    """The signed headers **and** the address they were signed with.

    The address travels back for an operational reason, not a decorative one: when
    the rail breaks, 99 % of the time it is "that wallet is not on the allowlist",
    and the remedy requires knowing WHICH wallet signed. Taking it from here and
    not from a second `get_address()` matters when the signer is remote: that
    second call can fail exactly when you are assembling the error message.
    """

    __slots__ = ("headers", "wallet")

    def __init__(self, headers: Dict[str, str], wallet: str) -> None:
        self.headers = headers
        self.wallet = wallet


def sign_partner_headers(
    signer: PartnerSigner,
    *,
    method: str,
    url: str,
    chain_id: int = PARTNER_CHAIN_ID,
    now: Optional[Callable[[], int]] = None,
) -> PartnerSignature:
    """Sign a request for the partner rail. It delegates EVERYTHING to the
    payments SDK.

    Args:
        signer: the injected signing object. **This SDK never builds one**, does
            not read it from an env var and does not touch its key.
        method: the HTTP method, exactly as it will go out.
        url: the COMPLETE URL that will be sent, with its query if it has one. See
            the header: signing anything else produces a 402 nobody understands.
        now: injectable clock (epoch in seconds), for tests.

    Raises:
        `PartnerSigningError` on any failure — the extra not installed, a signer
        that raises, a KMS that is down, a signature that is not hex. **A
        half-built dictionary is never returned**: carrying on without a signature
        is exactly the path that ends up spending USDC silently.
    """
    try:
        # El import va acá adentro y no arriba, igual que en el gate del
        # servicio: este módulo tiene que poder importarse —y su Protocol
        # usarse para tipar un firmante— sin el SDK de pagos presente. El
        # camino gratis no arrastra nada que firme.
        from uvd_x402_sdk.erc8128 import sign_request
    except ImportError as exc:
        # El mensaje nombra la causa REAL. Es la lección que `payment.py` ya
        # se comió con `chain_name_for`: un error que manda al lugar
        # equivocado es peor que ninguno, porque el que lo lee ejecuta la
        # receta, sigue en rojo y no busca más.
        raise PartnerSigningError(
            "the partner rail needs `uvd-x402-sdk` (it is the one that knows how "
            "to sign ERC-8128) and it is not installed: "
            "`pip install uvd-describe-sdk[partner]`. NOTHING was requested and "
            "NOTHING was spent.",
            wallet=None,
        ) from exc

    # Se declara afuera del `try` para que el error pueda nombrar la wallet
    # cuando el fallo fue al FIRMAR y no al pedir la dirección: «tu KMS de
    # 0x… no contestó» es accionable, «el firmante falló» no.
    wallet: Optional[str] = None
    try:
        wallet = signer.get_address()
        headers = sign_request(
            signer,
            method=method,
            url=url,
            chain_id=chain_id,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001
        # Se traga TODA excepción del firmante a propósito y se re-levanta como
        # una nuestra: quien llama no debería tener que importar el SDK de
        # pagos —ni el cliente de su KMS— para escribir su `except`. Lo que NO
        # se hace nunca es seguir sin firma.
        raise PartnerSigningError(
            f"the partner rail's signer failed ({type(exc).__name__}: {exc}). "
            "NOTHING was requested and NOTHING was spent: fix the signer, or build "
            "the client WITHOUT `partner=` if you really do want to pay.",
            wallet=wallet,
        ) from exc

    return PartnerSignature(headers=dict(headers), wallet=str(wallet))
