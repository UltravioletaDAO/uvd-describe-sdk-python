# Changelog

Published by tag: `vX.Y.Z` triggers `.github/workflows/publish.yml`, which refuses a
tag that does not match `src/uvd_describe_sdk/version.py`. Up to 0.5.0 each
release is recorded in its commit message (`git log`, tags `v0.1.0`…`v0.5.0`);
this file starts with 0.6.0, the first release that asked for one.

## [Unreleased]

Changes merged since the last release accumulate here, each with its label
(`[security]`, `[money]`, `[feature]`, `[internal]`), and ship together.

### [feature] `uvd_describe_sdk.names` — the one name resolver of the stack

Name → address and address → name, read on-chain, behind a new extra:
`pip install "uvd-describe-sdk[names]"` (`ens-normalize`, `pycryptodome`, and
`httpcore>=1.0,<2`, which httpx already installs and the sync deadline imports
directly). The base
install still depends on `httpx` only.

- **`NameResolver(rpc=..., systems=..., cache=..., timeout=...)`** with `resolve`,
  `reverse`, `text`, `avatar` (async) and their `*_sync` variants, built on one set
  of resolution steps; `normalize` and `detect` need no network. `rpc` is keyed by
  CAIP-2 chain id and the SDK ships no RPC URL.
- **Systems**: ENS (`.eth`, subnames, ENSIP-10 wildcards, EIP-3668 CCIP-Read,
  expiry), Basenames (`*.base.eth` through L1 + CCIP; expiry and the ENSIP-19
  primary name on Base), DNS names imported into ENS (DNSSEC / offchain resolver),
  Unstoppable Domains (UD's ProxyReader on Polygon or Base, then L1; TLD table dated
  `UNS_TLDS_MEASURED_AT = "2026-09-24"`) and Avvy (`.avax`, on-chain — no HTTP
  API). `.sol` is detected and answered `unsupported_system`: SNS is migrating
  `.sol` to a new registry (legacy resolution stops at finalized slot 452,825,395,
  about 2026-10-15, and the new path is still disabled upstream).
- **Normalization**: ENSIP-15 for the ENS family (`invalid_name` on error); each
  other system's own rule.
- **Reverse is always confirmed forward**; a failed confirmation (or a claimed name
  that is not in ENSIP-15 normal form) is `reverse_mismatch`, and the claimed name is
  not returned.
- **Records**: ENSIP-5 text records (the consumer passes its own keys) and the
  ENSIP-12 avatar resolved to a final URL, NFT ownership checked.
- **`NameResolution` / `NameRecord`** (exported by the base package, no extra
  needed): `input`, `normalized`, `address` (never the zero address), `family`,
  `system`, `verified_onchain`, `cached`, `error` (`not_found` | `invalid_name` |
  `reverse_mismatch` | `expired` | `unsupported_system` | `rpc_unavailable` — a
  missing name is an answer, never an exception), `tried`, `detail`.
- **`require_onchain_address(result)`** returns the address only when it was read
  on-chain; otherwise `NameNotVerifiedError` (deliberately not a `DescribeError`).
- **`NameCache`**: LRU with `max_entries` (1024), a positive TTL (300 s), a
  negative TTL (60 s), and `rpc_unavailable` is never cached. A hard `timeout`
  (10 s) covers the whole call.
- **`DescribeClient.names.resolve()` / `.reverse()`** against
  `api.describe.net/v1/names/resolve?name=` and `/v1/names/reverse?address=`: the
  contract the service is to serve (`NameResolution.to_dict()`, 200 for every
  resolver outcome). Free routes with R5 semantics; the result always carries
  `verified_onchain=False`.
- CCIP gateway and NFT metadata URLs go through a guard: `https://` only, no
  credentials, no `localhost`, no IP literal outside the global address space.
- The `timeout` bounds the whole call. Async: `asyncio.wait_for`. Sync: the
  resolver's default transport caps every connect, read, write and TLS handshake
  to what is left of the budget (a server dripping its headers is cut), bodies are
  read in chunks against the clock, and no redirect is followed past it. Not
  bounded in sync: DNS resolution, and a `transport=` the consumer passes (only
  the chunk and redirect checks apply to it). The names clients read nothing from
  the environment, proxies included.
- Gateway and NFT metadata requests send `Accept-Encoding: identity`; a body in
  any other encoding is refused (`rpc_unavailable`), and the size cap counts wire
  bytes.
- Each resolver checks every RPC's `eth_chainId` once against its CAIP-2 key: an
  RPC that serves another chain is `rpc_unavailable`, naming the chain it serves.
- `reverse()` answers `rpc_unavailable` (never raises) when a contract that does
  not revert, reverts — an RPC that answers "execution reverted" to everything.
- `UNS_ICANN_COLLISIONS` (graphics, gripe, guide, shiksha, travel; IANA list
  version 2026092400): names under TLDs that are both ICANN and Unstoppable
  answer `unsupported_system` instead of silently picking one namespace.
- `namehash()` and `labelhash()` exported from `uvd_describe_sdk.names`; both
  normalize with ENSIP-15 first and raise `InvalidNameError` otherwise.
- `require_onchain_address()` also refuses the zero address.
- `scripts/grabar_fixtures_names.py` records the fixtures in
  `tests/fixtures/names/` from public RPCs (sequential, ≥ 1.1 s apart, stops at the
  first HTTP 429). No RPC URL is written to a fixture.

## 0.6.1 — 2026-09-15

⚠️ Corrected 2026-09-24, left written: this heading said "unreleased". The tag
`v0.6.1` exists, its publish run succeeded on 2026-09-15 and PyPI lists 0.6.1
uploaded that day. It shipped the 0.6.0 entry below as well: `v0.6.0` was never
tagged nor uploaded.

### Added

- **`CaveatCode.THIN_CHAIN`** (`"thin-chain"`) — `KNOWN_CAVEAT_CODES` goes from 9
  to 10, the whole set describe.net serves (`describenet/caveats.py:177-192`).
  `thin-chain` is served since 2026-09-05: a chain with fewer than
  `reading_policy.min_raters` distinct raters on a multi-chain wallet.
- `FREE_GATE_CAVEAT_CODES` is now `{"burn-address", "thin-chain"}`: the free
  `GET /wallets/{wallet}/chains` door evaluates both.

### Changed

- `CAVEAT_CODES_MEASURED_AT` moves from `2026-08-30` to `2026-09-15`, the date the
  ten codes were read from the service.

### Parity with the TypeScript SDK

Both SDKs add `thin-chain` in the same release and know the same ten caveat codes.

## 0.6.0 — never published (shipped inside 0.6.1)

The upstream-first row `describe-net/docs/BACKLOG.md:19`: describe.net serves both
fields in production since 2026-09-14 (PR #21, deployed `aa1bd75`). This version
types them **before** Execution Market, KarmaKadabra, MeshRelay and karma-hello
adopt them. Everything is additive: nothing 0.5.0 returned changes value or
position — the two new dataclass fields go LAST, after `raw`, so no positional
construction shifts.

### Added

- **`WalletReputation.caveats_not_computed: Optional[List[str]]`** — the caveat
  codes the free `GET /wallets/{wallet}/chains` answer declares it did not
  evaluate. `[]` = declared, nothing left out; `None` = not declared (an API
  older than 2026-09-14, a `fallback_reader` result, or a value that could not be
  read whole). `None` is never collapsed into `[]`.
- **`require_full_caveats(rep) -> WalletReputation`** and
  **`CaveatsNotComputedError`** — the gate the 2026-08-31 position assigned to the
  SDK (`describe-net/docs/BACKLOG.md:221`). Returns `rep` only when the answer
  declared `[]`; raises with `not_computed` = the codes, or `not_computed = None`
  when nothing was declared. Only the empty `list` passes, not any falsy value: a
  hand-built `WalletReputation` whose declaration is not a list (`()`, `""`, `0`,
  `False`, `{}`, `set()`) raises with `not_computed = None`, as in the TypeScript
  SDK. A non-`WalletReputation` is a `TypeError`. The error
  is deliberately **not** a `DescribeError`, so an outage-tolerant
  `except DescribeError` cannot turn a refusal into a pass.
- **`Rating.author_class: Optional[str]`** — `facilitator-authored` or
  `rater-authored`, with `AuthorClass` (constants), `KnownAuthorClass` (the
  `Literal`), `KNOWN_AUTHOR_CLASSES` and `is_known_author_class()`. Unknown classes
  arrive whole; an absent class is `None`, never `rater-authored`.
- **`CaveatCode.FACILITATOR_AUTHORED`** — `KNOWN_CAVEAT_CODES` goes from 8 to 9.
  Agent scope only.
- `examples/smoke_gratis.py` prints `caveats_not_computed` and fails if the live
  free door stops declaring it.

### Not included, on purpose

- **`thin-chain`**, served by describe.net since 2026-09-05, is still missing from
  `KNOWN_CAVEAT_CODES` and `FREE_GATE_CAVEAT_CODES`. It was not in the scope of
  this row and adding it to one twin only would break Python/TypeScript parity,
  so it is a follow-up for both SDKs, to land together.
  `CAVEAT_CODES_MEASURED_AT` stays `2026-08-30` for that reason. Added in 0.6.1.

### Parity with the TypeScript SDK

The same surface, each in its language's casing: `caveats_not_computed` /
`caveatsNotComputed`, `Rating.author_class` / `authorClass`,
`require_full_caveats()` / `requireFullCaveats()`, and `CaveatsNotComputedError`
with `.wallet` and `.not_computed` / `.notComputed`. Both know the same nine caveat
codes. Two edge differences are declared, and both fail closed:

- An unreadable entry inside the served list (`[null]`): here the whole field is
  `None`; TypeScript converts each entry with `String()`. The gate refuses in both.
- The `recovery` text is not byte-identical: each names its own language's
  spellings (`wallet_breakdown()`, `payer=` here; the HTTP route there).
