"""Tests for the response cache (TTLCache) and its integration into RAGChain."""
import time
from unittest.mock import MagicMock, patch

import pytest

from src.nextpulse.cache import TTLCache


# ── TTLCache unit tests ──────────────────────────────────────────────────────

class TestTTLCache:
    def test_set_get_roundtrip(self):
        c = TTLCache(maxsize=4, ttl_seconds=100)
        c.set("k", {"v": 1})
        assert c.get("k") == {"v": 1}

    def test_miss_returns_none(self):
        c = TTLCache()
        assert c.get("absent") is None

    def test_lru_eviction(self):
        c = TTLCache(maxsize=2, ttl_seconds=100)
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")          # 'a' becomes most-recently-used
        c.set("c", 3)       # evicts the LRU entry, which is now 'b'
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("c") == 3

    def test_ttl_expiry(self):
        c = TTLCache(maxsize=4, ttl_seconds=0.02)
        c.set("k", 1)
        assert c.get("k") == 1
        time.sleep(0.05)
        assert c.get("k") is None  # expired

    def test_stats_track_hits_and_misses(self):
        c = TTLCache()
        c.set("k", 1)
        c.get("k")          # hit
        c.get("nope")       # miss
        s = c.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["size"] == 1


# ── RAGChain integration ─────────────────────────────────────────────────────

_ANSWER = MagicMock()
_ANSWER.choices = [MagicMock(message=MagicMock(content="Risposta dai documenti [1]."))]


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)
    monkeypatch.setattr("src.nextpulse.config.RESPONSE_CACHE_ENABLED", True)
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "cache_test")
    from src.nextpulse.rag_chain import RAGChain
    c = RAGChain()
    c.vector_store.add_documents(
        texts=["Il T-EXCEED V2 è un autovelox omologato."],
        metadatas=[{"source": "texceed.pdf"}],
    )
    return c


class TestRAGChainCache:
    def test_second_identical_query_hits_cache(self, chain):
        """The LLM is called on the first query only; the second is served from cache."""
        with patch.object(chain.client.chat.completions, "create",
                           return_value=_ANSWER) as mock_create:
            first = chain.query("Che cos'è il T-EXCEED?")
            second = chain.query("Che cos'è il T-EXCEED?")

        assert mock_create.call_count == 1          # single source → no judge; 1 generation
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["response"] == first["response"]

    def test_whitespace_and_case_normalized(self, chain):
        with patch.object(chain.client.chat.completions, "create",
                           return_value=_ANSWER) as mock_create:
            chain.query("Che cos'è il T-EXCEED?")
            hit = chain.query("  che   cos'è  il   T-EXCEED?  ")
        assert mock_create.call_count == 1
        assert hit["cached"] is True

    def test_different_role_is_a_distinct_entry(self, chain):
        with patch.object(chain.client.chat.completions, "create",
                           return_value=_ANSWER) as mock_create:
            chain.query("Che cos'è il T-EXCEED?", role="presales")
            chain.query("Che cos'è il T-EXCEED?", role="sales")
        assert mock_create.call_count == 2          # role is part of the key

    def test_cache_can_be_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)
        monkeypatch.setattr("src.nextpulse.config.RESPONSE_CACHE_ENABLED", False)
        monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd2")
        monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "nocache_test")
        from src.nextpulse.rag_chain import RAGChain
        c = RAGChain()
        c.vector_store.add_documents(texts=["Autovelox X."], metadatas=[{"source": "x.pdf"}])
        assert c._cache is None
        with patch.object(c.client.chat.completions, "create",
                          return_value=_ANSWER) as mock_create:
            c.query("Cos'è X?")
            c.query("Cos'è X?")
        assert mock_create.call_count == 2          # no caching → both hit the LLM
