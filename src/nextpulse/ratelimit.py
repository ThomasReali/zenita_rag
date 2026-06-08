"""In-memory sliding-window rate limiter (per client key, e.g. IP).

Thresholds are passed to :meth:`allow` per call rather than fixed at construction, so a
config change (or a test) takes effect immediately without rebuilding the limiter. Process-
local: fine for a single-node demo; a multi-node deploy would back this with Redis instead.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """Allow up to `max_requests` per key within a rolling `window_seconds`."""

    def __init__(self) -> None:
        self._hits: "defaultdict[str, deque]" = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, max_requests: int, window_seconds: float) -> bool:
        """Record a request for `key`; return False if it exceeds the window quota."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < cutoff:   # drop timestamps outside the window
                dq.popleft()
            if len(dq) >= max_requests:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        """Forget all recorded requests (used by tests)."""
        with self._lock:
            self._hits.clear()
