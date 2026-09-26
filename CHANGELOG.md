# Changelog

Published by tag: `vX.Y.Z` triggers `.github/workflows/publish.yml`, which refuses a
tag that does not match `src/uvd_describe_sdk/version.py`. Up to 0.5.0 each
release is recorded in its commit message (`git log`, tags `v0.1.0`…`v0.5.0`);
this file starts with 0.6.0, the first release that asked for one.

## [Unreleased]

Changes merged since the last release accumulate here, each with its label
(`[security]`, `[money]`, `[feature]`, `[internal]`), and ship together.

## 0.7.0 — prepared 2026-09-25; published by the `v0.7.0` tag

`names` is new in this release, and the fixes below were made to it before any
version carrying it was published: no released version behaves the old way.

### [security] SDK-5 — a URL chosen on-chain no longer makes the resolver raise

Found by describe.net's review of its name routes, against `5ed00228`: a CCIP
gateway URL, a redirect `Location`, an NFT metadata URL or an avatar record — each
written by whoever controls a resolver, an NFT contract or a name — could make
`resolve()` / `text()` / `avatar()` (and `reverse()`, which resolves forward)
**raise** instead of answering. A consumer without a guard of its own answered
HTTP 500, chosen by the name's owner. Measured on py3.9.24, 3.12.12 and 3.13.6:

| Input | Escaped as | Now |
|---|---|---|
| gateway `https://[x/{data}` (unclosed bracket) | `ValueError` from `urlsplit` | refused like any forbidden URL: the next gateway is tried; none left → `rpc_unavailable` |
| gateway `https://[zzz]/{data}` (bracketed host, not an IP) | `ValueError` from `urlsplit` | same |
| gateway `https://gw.example/\x01{data}` | `httpx.InvalidURL` | same |
| redirect to `Location: https://[x/` | `ValueError` from `urljoin` | `rpc_unavailable` |
| gateway body of 1,000+ nested `[` | `RecursionError` from `json.loads` | a body without hex `data`: the next gateway, then `rpc_unavailable` |
| NFT metadata (https or `data:`) of 1,000+ nested `[` | `RecursionError` | no URL, `detail` "the NFT metadata … is not JSON" — as any non-JSON metadata |
| NFT avatar whose token id has 5,000 digits | `ValueError` from `int()` | no URL, `detail` "the avatar record is not an ENSIP-12 URI": a token id that is not a uint256 is not an NFT reference |
| NFT metadata URL `https://[x/…` | `ValueError` from `urlsplit` | `rpc_unavailable` for that `avatar()` |

A `Location` httpx itself cannot parse (`//[zzz]/a`, a control character) was
already `rpc_unavailable` (`RemoteProtocolError`); it is now pinned by a test.
Every one is caught by its concrete class — a test fails if any module of
`names/` gains an `except Exception` — so a bug of the SDK itself still raises.
Since round 2 that test is an ALLOW-list of the exception types `names/` may
catch (the deny-list it was let `except ValueError.__base__:` — which IS `except
Exception` — pass green), and `contextlib.suppress` fails it too.

### [security] A host that is an IP in disguise is refused, in hex too

A CCIP gateway or an NFT metadata URL is chosen by whoever controls a contract,
and a consumer on AWS ECS can reach the task-credentials endpoint at
`169.254.170.2`. Measured against `940685ec` (py3.9.24, 3.13.6), these PASSED the
URL guard: `https://0xa9fea902/` (that very address), `https://0x7f000001/`,
`https://127.0.0.0x1/`, `https://0x7f.0x0.0x0.0x1/` — the last label has letters
(`x`, `f`) and the guard only refused a last label without any — and
`https://[::169.254.170.2]/` (IPv4-compatible) and `https://[64:ff9b::a9fe:aa02]/`
(NAT64), which `ipaddress` calls global. Now a last label that is `[0-9]+` or
`0x[0-9a-f]*` is refused, and so is an IPv6 address in `::ffff:0:0/96`, `::/96`
or `64:ff9b::/96` whose embedded IPv4 is not global.

**Known limit, not fixed:** the guard does not resolve DNS. A public host name
whose DNS answers with a private address passes it.

### [security] A dynamic ABI array with overlapping offsets is not a memory bomb

The owner of a name chooses its resolver, and an ENSIP-10 resolver chooses the
`OffchainLookup` revert: its `urls: string[]` could hold N offsets pointing at ONE
long string, and the decoder built N copies. Measured: 1,024 offsets to a 128 KB
string (a ~160 KB revert) → a 129 MiB peak; the refuter measured a 0.95 MB revert
at ≈ 7 GB and 32 s — a `MemoryError` or the OOM killer in a small process. The
elements of a dynamic array can no longer add up to more than the data that holds
them (`AbiError`, "malformed OffchainLookup"). And the hex check of every RPC and
gateway answer, `0x([0-9a-fA-F]{2})*`, kept state per repetition — ~150x the input,
145-184 MiB for 1 MB of hex — and is now a flat class plus a parity check. The
same revert now peaks at 1.5 MiB.

### [security] A name is at most `MAX_NAME_BYTES` (1,024) before ENSIP-15

ENSIP-15 costs more than linearly, and the deadline cannot cut CPU inside a step:
the refuter measured a 900 KB reverse name of combining marks at 67.5 s with
`timeout=1.0`, blocking the caller's event loop in the async flavour. Measured
here (ens-normalize 3.0.10): 4.1 ms at 1,024 bytes, 4.9 s at 150 KB. A name over
1,024 UTF-8 bytes is refused before normalizing: `invalid_name` as the input of
`resolve()` / `text()` / `avatar()`, `reverse_mismatch` ("the reverse record is
too long") as a name a reverse record claims — which also stops a 900,000-character
ASCII name from coming back confirmed.

### [security] An Avvy rainbow-table signal out of range is not a name

A signal of 2**248 or more made `reverse()` raise `OverflowError`, sync and async.
It is now unreadable, like an answer that does not decode: no claim.

### [feature] SDK-6 — `reverse()` that could ask no system is `rpc_unavailable`

A `reverse()` whose systems were ALL skipped for want of an RPC answered
`not_found` (with `verified_onchain=False`), and the cache kept it 60 s: the one
`not_found` that meant «I do not know». It now answers `rpc_unavailable` — never
cached, `detail` naming the skipped systems. `not_found` comes out only when at
least one system was asked and answered. A resolver with no reverse-capable
system enabled (e.g. `systems=("ens-dns",)`) answers `unsupported_system`, decided
without the network.

### [feature] A system skipped for want of an RPC still ranks in `reverse()`

Found by describe.net's refuter against `5ed00228`, and measured here with a
synthetic double before changing anything:

- **A negative is `verified_onchain` only if no system was skipped.** With no RPC
  for Base, `reverse()` answered `not_found` with `verified_onchain=True`
  (`tried` ens, unstoppable, avvy), and a consumer cached it as the truth. It is
  still `not_found`, now with `verified_onchain=False`; `detail` names the skipped
  systems. The same holds for `reverse_mismatch` and `expired`.
- **A lower system does not give the primary name when one above was skipped.**
  With no RPC for `eip155:1` (ENS, Basenames and UNS skipped), an Avvy name came out
  as the primary, `verified_onchain=True` — against the strict order the SDK
  already applied to a system that could not be asked. It is now
  `rpc_unavailable`, the name not shown.
- A system left out of `systems=` is a choice, not an outage: it does not rank.
- **The chains of Unstoppable count too** (round 2). UNS reads a reverse record on
  L1, then Polygon, then Base. With only the L1 RPC, `reverse()` answered
  `not_found` verified and never said Polygon and Base were not asked. A chain
  with no RPC now ranks like a skipped system — `detail` says
  `unstoppable on eip155:137, eip155:8453 (no RPC)`, a negative is not verified,
  and a name found on Base with Polygon unasked is not given — while a name found
  on L1 is, since L1 comes first. The SDK's own `NameCache` still keeps that
  `not_found`, like any answer; `verified_onchain=False` is what tells a
  consumer's cache not to.

### [feature] SDK-7 — a reverted `nameExpires` is `rpc_unavailable`

`resolve()` of a `.eth` or `.base.eth` name through an RPC that answers "execution
reverted" to everything came out `not_found`, `verified_onchain=True` ("the
registrar did not answer"). `nameExpires` cannot revert: in ENS's
`BaseRegistrarImplementation` it is `return expiries[id]`, in Basenames'
`BaseRegistrar` the getter of a public mapping, and the recording of an
unregistered `.eth` name (mainnet, 2026-09-24) got `0`. A revert there is the RPC,
so it is `rpc_unavailable` (never cached). A registrar answer that does not decode
is still `not_found`.

### [feature] `uvd_describe_sdk.names` — the one name resolver of the stack

Name → address and address → name, read on-chain, behind a new extra:
`pip install "uvd-describe-sdk[names]"` (`ens-normalize`, `pycryptodome`). The base
install still depends on `httpx` only.

- **`NameResolver(rpc=..., systems=..., cache=..., timeout=...)`** with `resolve`,
  `reverse`, `text`, `avatar` (async) and their `*_sync` variants — ONE engine: the
  `*_sync` variants run the async one on a private event loop; `normalize` and
  `detect` need no network. `rpc` is keyed by CAIP-2 chain id and the SDK ships no
  RPC URL. `transport=` must be usable by an async client (`httpx.MockTransport`
  is); a sync-only transport is refused at construction, and so are two different
  ones in `transport=` and `async_transport=` (the same object in both is fine).
- **The `*_sync` variants open a client per call — a new TCP (and TLS) connection
  each time, no keep-alive from one call to the next.** Measured against a local
  keep-alive server (2026-09-24): 10 `resolve_sync()` → 10 connections; 10
  `await resolve()` on one resolver → 1. Code that resolves in bulk should use the
  async flavour. A `*_sync` call made from async code (asyncio, or trio and any
  library `sniffio` knows) runs on its own loop in a thread of its own.
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
- The `timeout` bounds the whole call, counted from its start, with ONE
  `asyncio.wait_for` in both flavours: a dripping body, dripping headers, a gzip
  header that never ends, a connect over N addresses and a slow DNS lookup are all
  cut at the budget (tests against a local server). The sync flavour does not use
  `asyncio.run`, which waits for the executor where DNS runs. Environment proxies
  are honoured as in `DescribeClient` (httpx's default); a `transport=` replaces
  them.
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
