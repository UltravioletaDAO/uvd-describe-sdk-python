"""Which system a name belongs to, and its normalized form. No network here.

DETECTION IS BY SUFFIX, AND THE ORDER MATTERS
---------------------------------------------
karma-hello got the idea right (`domain_resolver.py:142-165`: `.base.eth` before
`.eth`) and the consequence wrong: it then looked the Basename up in an ENS
registry deployed on Base, where the L1 registry does not exist, so every
`.base.eth` came back empty (measured 2026-09-24). Here detection only says WHICH
system; how that system is read lives in its own module.

    *.base.eth              → basenames   (before `.eth`, it is a suffix of it)
    *.eth                   → ens
    *.sol                   → sns         (detected, not resolved: see names/__init__)
    *.avax                  → avvy
    a UNS top-level domain  → unstoppable (the table below, with its date)
    any other dotted name   → ens-dns     (a DNS name imported into ENS)

NORMALIZATION IS PER SYSTEM
---------------------------
* ENS family: ENSIP-15, through `ens-normalize` (the reference implementation,
  by the author of the ENSIP). Execution Market lower-cased instead
  (`client.py:147-158`), which is not ENSIP-15: it accepts names ENS refuses and
  maps look-alikes to different nodes. An ENSIP-15 error is `invalid_name`.
* Unstoppable: trim + lower-case, then `^[.a-z0-9-]+$` — exactly
  `prepareAndValidateDomain` of `@unstoppabledomains/resolution` (read 2026-09-24
  at `8f9fb66`).
* Avvy: lower-case, labels of 1-62 characters from `a-z0-9-` — the
  `characterAllowlist` and the 62-character padding of `@avvy/client`
  (`src/utils.js`, read 2026-09-24).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

from ens_normalize import DisallowedSequence, ens_normalize

from ..name_models import NameErrorCode, NameFamily, NameSystem
from ._proto import BASE, POLYGON

#: When the UNS table below was read, whole, from
#: `api.unstoppabledomains.com/resolve/supported_tlds` (the list the official
#: resolution library downloads at runtime). A figure is read live or carries a
#: date; a TLD that UD adds later resolves as `ens-dns` until this is refreshed.
UNS_TLDS_MEASURED_AT = "2026-09-24"

#: UNS top-level domains whose records live on Polygon (and L1).
_UNS_ON_POLYGON: FrozenSet[str] = frozenset(
    {
        "888",
        "agent",
        "ai4",
        "altimist",
        "anime",
        "arculus",
        "austin",
        "awaken",
        "binanceus",
        "bitcoin",
        "bitget",
        "blockchain",
        "brave",
        "calicoin",
        "carbon",
        "cashme",
        "cgai",
        "chipper",
        "clay",
        "coin",
        "collect",
        "crypto",
        "dao",
        "dejay",
        "derad",
        "dfz",
        "digitalfuture",
        "doga",
        "donut",
        "dream",
        "dsci",
        "emir",
        "farms",
        "go",
        "graphics",
        "gripe",
        "grow",
        "guide",
        "hi",
        "hub",
        "imtoken",
        "kingdom",
        "klever",
        "kresus",
        "kryptic",
        "lfg",
        "ltc",
        "manga",
        "marketer",
        "metropolis",
        "miku",
        "ministry",
        "mobix",
        "moon",
        "nft",
        "nibi",
        "og",
        "openx",
        "pack",
        "pastor",
        "pbdx",
        "pilot",
        "pog",
        "polygon",
        "presearch",
        "pudgy",
        "pundi",
        "raiin",
        "realm",
        "secret",
        "shiksha",
        "stepn",
        "super",
        "supernova",
        "tball",
        "tea",
        "travel",
        "tribe",
        "twin",
        "ubu",
        "unstoppable",
        "vanity",
        "wallet",
        "web3",
        "wifi",
        "witg",
        "wrkx",
        "x",
        "xmr",
        "xz1",
        "yellow",
        "zil",
    }
)

#: UNS top-level domains registered on Base (and L1). The official library
#: routes these to a separate service (`UNS_BASE`: L1 + Base).
_UNS_ON_BASE: FrozenSet[str] = frozenset(
    {
        "amped",
        "anyone",
        "ask",
        "ath",
        "bald",
        "basenji",
        "bay",
        "bch",
        "benji",
        "bitscrunch",
        "boomer",
        "bunni",
        "caw",
        "chip",
        "chomp",
        "depin",
        "digibyte",
        "enigma",
        "ethermail",
        "goblin",
        "gotchi",
        "her",
        "horizen",
        "learn",
        "lunar",
        "mooncat",
        "mumu",
        "mycircle",
        "npc",
        "ohm",
        "onchain",
        "pendle",
        "podcast",
        "pokt",
        "privacy",
        "propykeys",
        "quantum",
        "rad",
        "smobler",
        "south",
        "spend",
        "u",
        "udtest",
        "verge",
        "xec",
        "xyo",
        "zano",
    }
)

#: UNS top-level domains registered off the EVM chains the ProxyReaders cover
#: (Solana, Sonic). The official library has no reader for them either.
_UNS_ELSEWHERE: FrozenSet[str] = frozenset(
    {
        "aura",
        "bobi",
        "demos",
        "hegecoin",
        "housecoin",
        "mery",
        "pengu",
        "retardio",
        "swamp",
        "tigershark",
        "troll",
        "undeads",
        "wif",
        "sonic",
    }
)

#: The L2 each resolvable UNS TLD lives on. L1 is always read second.
UNS_L2_BY_TLD: Dict[str, str] = {
    **{tld: POLYGON for tld in _UNS_ON_POLYGON},
    **{tld: BASE for tld in _UNS_ON_BASE},
}

_UNS_NAME = re.compile(r"[.a-z0-9-]+")
_AVVY_LABEL = re.compile(r"[a-z0-9-]{1,62}")

FAMILY_OF: Dict[str, str] = {
    NameSystem.ENS: NameFamily.EVM,
    NameSystem.BASENAMES: NameFamily.EVM,
    NameSystem.ENS_DNS: NameFamily.EVM,
    NameSystem.UNSTOPPABLE: NameFamily.EVM,
    NameSystem.AVVY: NameFamily.EVM,
    NameSystem.SNS: NameFamily.SOLANA,
}


@dataclass(frozen=True)
class Classified:
    """What detection + normalization concluded about an input."""

    system: Optional[str]
    normalized: Optional[str]
    error: Optional[str] = None
    detail: Optional[str] = None


def _suffix_system(low: str) -> Optional[str]:
    """The system by suffix, on a trimmed lower-cased name. `None` = not a name."""
    if "." not in low or low.startswith(".") or low.endswith("."):
        return None
    tld = low.rsplit(".", 1)[1]
    if low.endswith(".base.eth"):
        return NameSystem.BASENAMES
    if tld == "eth":
        return NameSystem.ENS
    if tld == "sol":
        return NameSystem.SNS
    if tld == "avax":
        return NameSystem.AVVY
    if tld in UNS_L2_BY_TLD or tld in _UNS_ELSEWHERE:
        return NameSystem.UNSTOPPABLE
    return NameSystem.ENS_DNS


_NOT_A_NAME = "not a name: it needs a label and a top-level domain"
#: The systems read through the L1 ENS registry (ENSIP-15, ENSIP-10, CCIP).
ENS_FAMILY: FrozenSet[str] = frozenset({NameSystem.ENS, NameSystem.BASENAMES, NameSystem.ENS_DNS})


def classify(raw: str) -> Classified:
    """Detect the system and normalize for it. Pure; never raises for bad input."""
    if not isinstance(raw, str):
        return Classified(None, None, NameErrorCode.INVALID_NAME, "a name is a string")
    low = raw.strip().lower()
    system = _suffix_system(low)
    if system is None or system in ENS_FAMILY:
        return _classify_ens(raw.strip(), system)
    return _classify_other(system, low)


def _classify_other(system: str, low: str) -> Classified:
    """The non-ENS systems: each one's own rule, on the trimmed lower-case name."""
    if system == NameSystem.SNS:
        return Classified(system, low)
    if system == NameSystem.UNSTOPPABLE:
        if not _UNS_NAME.fullmatch(low) or "" in low.split("."):
            return Classified(
                system, None, NameErrorCode.INVALID_NAME, "Unstoppable names are [a-z0-9-] labels"
            )
        return Classified(system, low)
    if system == NameSystem.AVVY:
        if not all(_AVVY_LABEL.fullmatch(label) for label in low.split(".")):
            return Classified(
                system,
                None,
                NameErrorCode.INVALID_NAME,
                "Avvy labels are 1-62 characters of [a-z0-9-]",
            )
        return Classified(system, low)
    return Classified(system, None, NameErrorCode.INVALID_NAME, _NOT_A_NAME)


def _classify_ens(typed: str, guess: Optional[str]) -> Classified:
    """ENSIP-15 decides, on the name as typed (not lower-cased by us: the ENSIP
    defines its own case folding, and it is not `str.lower`).

    The system is decided AGAIN on the normalized name: ENSIP-15 folds
    full-width letters, so `vitalik.ＥＴＨ` only becomes `vitalik.eth` — an `ens`
    name, not a DNS one — after normalization.

    ⚠️ Corrected 2026-09-24, left written: this said a full-width STOP
    (U+FF0E) "becomes a '.' once normalized". Measured with `ens-normalize`
    3.0.10, it does not: it is `DISALLOWED`, and so is the ideographic stop
    (U+3002). ENSIP-15 splits on U+002E only. The letters fold; the stops do not.
    """
    try:
        normalized = ens_normalize(typed)
    except DisallowedSequence as exc:
        if guess is None:
            return Classified(None, None, NameErrorCode.INVALID_NAME, _NOT_A_NAME)
        return Classified(guess, None, NameErrorCode.INVALID_NAME, f"ENSIP-15: {exc.code}")
    again = _suffix_system(normalized)
    if again is None:
        return Classified(None, None, NameErrorCode.INVALID_NAME, _NOT_A_NAME)
    if again not in ENS_FAMILY:
        # `brad．crypto`: normalization revealed another system's suffix.
        return _classify_other(again, normalized)
    return Classified(again, normalized)


def ensip15_is_normalized(name: str) -> bool:
    """A reverse record is shown only if it is ALREADY in normal form (ENSIP-15)."""
    try:
        return bool(ens_normalize(name) == name)
    except DisallowedSequence:
        return False
