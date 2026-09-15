# Changelog

Published by tag: `vX.Y.Z` triggers `.github/workflows/publish.yml`, which refuses a
tag that does not match `src/uvd_describe_sdk/version.py`. Up to 0.5.0 each
release is recorded in its commit message (`git log`, tags `v0.1.0`…`v0.5.0`);
this file starts with 0.6.0, the first release that asked for one.

## 0.6.0 — unreleased (tag pending, published by c0der after merge)

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
  when nothing was declared. A non-`WalletReputation` is a `TypeError`. The error
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
  this row and adding it to one twin only would break Python/TypeScript parity;
  it is reported in `docs/handoffs/2026-09-15-dn-sdk-caveats-py.md`.
  `CAVEAT_CODES_MEASURED_AT` stays `2026-08-30` for that reason.
