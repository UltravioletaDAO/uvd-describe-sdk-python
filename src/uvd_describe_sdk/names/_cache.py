"""`NameCache` — bounded, with two lifetimes, and blind to transport failures.

The two caches this replaces each had one of the three bugs (measured
2026-09-24):

* Execution Market (`client.py:114-129`): a module-level `dict` with no bound —
  a process that sees many distinct names grows without limit — and it stored
  negatives with the same five minutes as positives.
* karma-hello (`domain_resolver.py:167-193`): positives only, one hour, and also
  unbounded.

So: an LRU with `max_entries`; `ttl` for answers that carry an address (or a
record); `negative_ttl`, shorter, for `not_found`, `expired` and
`reverse_mismatch` — a name registered a minute ago should not stay "not found"
for as long as a real answer stays fresh. And `rpc_unavailable` is NEVER stored:
caching "I could not ask" turns a thirty-second RPC blip into minutes of wrong
answers. `invalid_name` and `unsupported_system` are not stored either: they are
decided without the network, so there is nothing to save.

One instance is safe to share between threads, and between the sync and async
variants of one resolver (nothing awaits while holding the lock).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Hashable, Optional, Tuple, Union

from ..name_models import NameErrorCode, NameRecord, NameResolution

Result = Union[NameResolution, NameRecord]

#: Five minutes: Execution Market's measured production value (`_CACHE_TTL =
#: 300`). karma-hello used an hour; an address that moved should not keep
#: receiving payments for an hour.
DEFAULT_TTL_S = 300.0
#: One minute for "not there": long enough to absorb a burst of the same failed
#: search, short enough for a fresh registration to show up.
DEFAULT_NEGATIVE_TTL_S = 60.0
DEFAULT_MAX_ENTRIES = 1024

_NEGATIVE = frozenset(
    {NameErrorCode.NOT_FOUND, NameErrorCode.EXPIRED, NameErrorCode.REVERSE_MISMATCH}
)


class NameCache:
    """An LRU of name results with a positive and a negative lifetime."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl: float = DEFAULT_TTL_S,
        negative_ttl: float = DEFAULT_NEGATIVE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries = max_entries
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self._clock = clock
        self._entries: OrderedDict[Hashable, Tuple[float, Result]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _lifetime(self, result: Result) -> Optional[float]:
        """How long `result` may be kept, or `None` if it must not be stored."""
        if result.error is None:
            return self.ttl
        if result.error in _NEGATIVE:
            return self.negative_ttl
        return None

    def get(self, key: Hashable) -> Optional[Result]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return result

    def put(self, key: Hashable, result: Result) -> None:
        lifetime = self._lifetime(result)
        if lifetime is None or lifetime <= 0:
            return
        with self._lock:
            self._entries[key] = (self._clock() + lifetime, result)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
