"""Tiny thread-safe TTL + LRU cache for RAG responses.

Why: during a live demo the same questions are asked repeatedly. Caching the full
``QueryResult`` removes the LLM round-trip (latency → ~0) and the token cost, and
sidesteps the OpenRouter free-tier 429s on repeats. It is process-local and in-memory:
no persistence, no PII written to disk (the cached value already left the machine as the
HTTP response would have). Each :class:`~src.nextpulse.rag_chain.RAGChain` owns its own
cache instance, so the company KB and the bandi corpus never share entries.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    """Bounded LRU cache with per-entry time-to-live. Safe for concurrent access
    (FastAPI serves sync endpoints from a thread pool)."""

    def __init__(self, maxsize: int = 256, ttl_seconds: float = 1800.0) -> None:
        self.maxsize = max(1, int(maxsize))
        self.ttl = float(ttl_seconds)
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            ts, value = entry
            if now - ts > self.ttl:
                # Expired — drop it and report a miss.
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)  # mark as most-recently-used
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)  # evict least-recently-used

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._store),
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
            }
