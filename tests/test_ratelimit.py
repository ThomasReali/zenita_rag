"""Tests for the per-IP sliding-window rate limiter and its API enforcement."""
from unittest.mock import MagicMock

from src.nextpulse.ratelimit import SlidingWindowLimiter


class TestSlidingWindowLimiter:
    def test_allows_up_to_quota(self):
        lim = SlidingWindowLimiter()
        results = [lim.allow("ip1", max_requests=3, window_seconds=60) for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_keys_are_independent(self):
        lim = SlidingWindowLimiter()
        assert lim.allow("ip1", 1, 60) is True
        assert lim.allow("ip1", 1, 60) is False
        assert lim.allow("ip2", 1, 60) is True   # different IP → own bucket

    def test_window_slides(self):
        lim = SlidingWindowLimiter()
        # A tiny window: after it elapses, old hits drop out and new ones are allowed.
        assert lim.allow("ip1", 1, 0.02) is True
        assert lim.allow("ip1", 1, 0.02) is False
        import time
        time.sleep(0.05)
        assert lim.allow("ip1", 1, 0.02) is True

    def test_reset_clears_state(self):
        lim = SlidingWindowLimiter()
        lim.allow("ip1", 1, 60)
        lim.reset()
        assert lim.allow("ip1", 1, 60) is True


class _FakeVS:
    def get_stats(self):
        return {"count": 10, "collection": "x"}

    def count_sources(self):
        return 3


class _FakeRAG:
    def __init__(self):
        self.vector_store = _FakeVS()
        self.model = "fake/model"
        self.pseudonymizer = MagicMock()

    def query(self, question, chat_history=None, k=None, role=None):
        return {
            "query": question, "standalone_query": question, "response": "ok",
            "sources": [], "context": [], "model": self.model,
            "grounded": True, "ambiguous": False, "top_score": 0.9,
            "role": role, "confidence": "green",
        }


class TestAPIRateLimit:
    def test_query_returns_429_over_quota(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.nextpulse import api

        monkeypatch.setattr(api, "RAGChain", _FakeRAG)
        monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)
        # Re-enable the limiter (conftest disables it) with a low threshold.
        monkeypatch.setattr("src.nextpulse.config.RATE_LIMIT_ENABLED", True)
        monkeypatch.setattr("src.nextpulse.config.RATE_LIMIT_PER_MINUTE", 3)
        monkeypatch.setattr("src.nextpulse.config.RATE_LIMIT_WINDOW_SECONDS", 60)
        api._rate_limiter.reset()

        with TestClient(api.app) as client:
            codes = [
                client.post("/api/query", json={"question": "ciao"}).status_code
                for _ in range(5)
            ]
        assert codes.count(200) == 3
        assert codes.count(429) == 2

    def test_disabled_limiter_never_blocks(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.nextpulse import api

        monkeypatch.setattr(api, "RAGChain", _FakeRAG)
        monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)
        monkeypatch.setattr("src.nextpulse.config.RATE_LIMIT_ENABLED", False)
        api._rate_limiter.reset()

        with TestClient(api.app) as client:
            codes = [
                client.post("/api/query", json={"question": "ciao"}).status_code
                for _ in range(10)
            ]
        assert all(c == 200 for c in codes)
