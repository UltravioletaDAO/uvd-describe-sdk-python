"""`uvd_describe_sdk.names` — the one name resolver of the stack.

    pip install "uvd-describe-sdk[names]"

    from uvd_describe_sdk.names import NameResolver
    names = NameResolver(rpc={"eip155:1": ETH_RPC, "eip155:8453": BASE_RPC})

    names.resolve_sync("ultravioletadao.eth").address   # "0xe4dc…e545"
    names.reverse_sync("0xe4dc…e545").normalized        # "0xultravioleta.eth"
    names.resolve_sync("0xultravioletadao.eth").error   # "not_found" — no exception

════════════════════════════════════════════════════════════════════════════
WHY IT EXISTS: THREE PARTIAL COPIES, EACH BROKEN SOMEWHERE ELSE
════════════════════════════════════════════════════════════════════════════
Measured 2026-09-24 (execution-market @ 9f218020, karma-hello @ 3f98927a,
uvdweb @ a12c22e9):

* Execution Market (`mcp_server/integrations/ens/client.py`): ENS on L1, with
  the one thing done right that the others missed — reverse confirmed forward
  (:193-203) — and ENSIP-5 text records (:240-265). But its namehash only
  lower-cases (:147-158, not ENSIP-15), its cache has no bound and keeps
  negatives as long as positives (:114-129), and it has no Basenames.
* karma-hello (`infrastructure/domain_resolver.py`): the widest net — `.eth`,
  `.base.eth`, `.avax`, detection by suffix (:142-165), a hard timeout
  (:225-232), an RPC per chain (`config.yaml:2886-2898`). But Basenames were
  broken (:251-298: it looked for L1's ENS registry INSIDE Base, where it does
  not exist) and `.avax` went through Avvy's HTTP API (:337-349), unverified, and
  that API answered 503.
* uvdweb (`src/components/WalletConnect.js:143-159`): the ENSIP-12 avatar via
  ethers `getAvatar` — with no forward check on the name it shows.

This module keeps the best of each and fixes what each had broken. The work
continues in the consumers: each one deletes its copy in its next batch.

════════════════════════════════════════════════════════════════════════════
WHAT IT RESOLVES, AND HOW (each module's docstring has the measurements)
════════════════════════════════════════════════════════════════════════════
  ens          `.eth` + subnames, wildcard + CCIP-Read (ENSIP-10, EIP-3668),
               expiry checked                                      `_ens.py`
  basenames    `*.base.eth` through L1 + CCIP; expiry and primary
               name (ENSIP-19) read on Base                        `_ens.py`
  ens-dns      DNS names imported into ENS (DNSSEC, offchain)     `_ens.py`
  unstoppable  UNS on Polygon / Base, then L1 (UD's ProxyReaders) `_uns.py`
  avvy         `.avax` ON-CHAIN (Poseidon computed by Avvy's own
               contract), expiry checked                          `_avvy.py`
  sns          `.sol` DETECTED, answered `unsupported_system`: SNS is mid
               migration to a new registry (see `SNS_UNSUPPORTED_DETAIL`).
               Half an implementation that stops working on ~2026-10-15 would
               be worse than a clear "not yet".

Normalization is ENSIP-15 for the ENS family (`ens-normalize`, the reference
implementation) and each system's own rule for the others (`_normalize.py`).
Every reverse lookup is confirmed forward before a name is returned; a failed
confirmation is `reverse_mismatch` and the claimed name is NOT returned.

════════════════════════════════════════════════════════════════════════════
THE WEIGHT — MEASURED, BECAUSE describe-net PUTS THIS IN A LAMBDA
════════════════════════════════════════════════════════════════════════════
2026-09-24, Linux x86_64 wheels, Python 3.12, `uv pip install --target`, sizes
in MiB (`du -sk`):

                                          packages   unpacked   zipped
    base SDK (httpx only)                     7        2.4        0.6
    + ens-normalize + pycryptodome           10       14.8        4.7   (chosen)
    + web3>=7,<8                             44       54.7       14.9

    On top of describe-net's Lambda (`requirements-lambda.txt`, 55 packages,
    80.5 MiB unpacked, 22.1 MiB zipped):
    + this extra                             +2       +5.7       +1.9
    + web3>=7,<8                            +14      +17.6       +4.7

`pycryptodome` is already in that Lambda (through `eth-account`), so the extra
costs it only `ens-normalize` and `pyunormalize`. And web3 would still not have
done Basenames' ENSIP-19 primary names, UNS or Avvy.
"""

from __future__ import annotations

try:
    import ens_normalize as _ens_normalize  # noqa: F401
    from Crypto.Hash import keccak as _keccak  # noqa: F401
except ImportError as _missing:  # pragma: no cover - exercised by hand, see the README
    raise ImportError(
        "uvd_describe_sdk.names needs the `names` extra: pip install 'uvd-describe-sdk[names]'"
    ) from _missing

from ..name_models import (
    KNOWN_NAME_SYSTEMS,
    NAME_ERROR_CODES,
    NameErrorCode,
    NameFamily,
    NameNotVerifiedError,
    NameRecord,
    NameResolution,
    NameSystem,
    require_onchain_address,
)
from ._cache import DEFAULT_MAX_ENTRIES, DEFAULT_NEGATIVE_TTL_S, DEFAULT_TTL_S, NameCache
from ._normalize import UNS_TLDS_MEASURED_AT
from ._proto import AVALANCHE, BASE, ETHEREUM, POLYGON
from ._resolver import (
    DEFAULT_IPFS_GATEWAY,
    DEFAULT_SYSTEMS,
    DEFAULT_TIMEOUT_S,
    SNS_UNSUPPORTED_DETAIL,
    InvalidNameError,
    NameResolver,
)

#: The CAIP-2 keys `rpc=` takes, one per chain a naming system lives on.
ETHEREUM_CHAIN = ETHEREUM
BASE_CHAIN = BASE
POLYGON_CHAIN = POLYGON
AVALANCHE_CHAIN = AVALANCHE

__all__ = [
    "NameResolver",
    "NameCache",
    "InvalidNameError",
    # the result contract (also exported by `uvd_describe_sdk` itself)
    "NameResolution",
    "NameRecord",
    "NameErrorCode",
    "NameSystem",
    "NameFamily",
    "NAME_ERROR_CODES",
    "KNOWN_NAME_SYSTEMS",
    "require_onchain_address",
    "NameNotVerifiedError",
    # configuration, defined once
    "DEFAULT_SYSTEMS",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_IPFS_GATEWAY",
    "DEFAULT_TTL_S",
    "DEFAULT_NEGATIVE_TTL_S",
    "DEFAULT_MAX_ENTRIES",
    "SNS_UNSUPPORTED_DETAIL",
    "UNS_TLDS_MEASURED_AT",
    "ETHEREUM_CHAIN",
    "BASE_CHAIN",
    "POLYGON_CHAIN",
    "AVALANCHE_CHAIN",
]
