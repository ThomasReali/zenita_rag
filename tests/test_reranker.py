"""Tests for the optional cross-encoder re-ranking stage.

No real model is downloaded: a fake cross-encoder (object exposing .predict) is injected,
so these tests stay fast and offline while still exercising ordering + integration.
"""
from unittest.mock import patch

import pytest

from src.nextpulse import reranker


class FakeCE:
    """Stand-in cross-encoder: returns a preset score per (query, doc) pair, keyed by a
    token the test plants in each doc."""
    def __init__(self, score_by_token: dict):
        self.score_by_token = score_by_token

    def predict(self, pairs):
        out = []
        for _q, doc in pairs:
            out.append(next((s for tok, s in self.score_by_token.items() if tok in doc), 0.0))
        return out


# ── rerank() unit tests ──────────────────────────────────────────────────────

class TestRerank:
    def test_reorders_by_cross_encoder_score(self):
        docs = ["alpha doc", "beta doc", "gamma doc"]
        metas = [{"source": "a"}, {"source": "b"}, {"source": "c"}]
        scores = [0.3, 0.2, 0.1]  # original RRF order
        fake = FakeCE({"alpha": 0.1, "beta": 0.9, "gamma": 0.5})
        d, m, s = reranker.rerank("q", docs, metas, scores, top_k=3, model=fake)
        assert [x["source"] for x in m] == ["b", "c", "a"]  # by CE score desc
        # original RRF scores are carried along in the new order (not the CE scores)
        assert s == [0.2, 0.1, 0.3]

    def test_truncates_to_top_k(self):
        docs = ["alpha", "beta", "gamma"]
        metas = [{"source": "a"}, {"source": "b"}, {"source": "c"}]
        scores = [0.3, 0.2, 0.1]
        fake = FakeCE({"alpha": 0.1, "beta": 0.9, "gamma": 0.5})
        d, m, s = reranker.rerank("q", docs, metas, scores, top_k=2, model=fake)
        assert [x["source"] for x in m] == ["b", "c"]
        assert len(d) == 2

    def test_empty_input(self):
        assert reranker.rerank("q", [], [], [], top_k=5, model=FakeCE({})) == ([], [], [])

    def test_graceful_fallback_on_model_error(self):
        class Boom:
            def predict(self, pairs):
                raise RuntimeError("model exploded")
        docs = ["a", "b", "c"]
        metas = [{"source": "a"}, {"source": "b"}, {"source": "c"}]
        scores = [0.3, 0.2, 0.1]
        d, m, s = reranker.rerank("q", docs, metas, scores, top_k=2, model=Boom())
        assert [x["source"] for x in m] == ["a", "b"]  # original order, truncated


# ── retrieve() integration (opt-in) ──────────────────────────────────────────

@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "rerank_test")
    from src.nextpulse.rag_chain import RAGChain
    c = RAGChain()
    c.vector_store.add_documents(
        texts=["alpha autovelox", "beta autovelox", "gamma autovelox"],
        metadatas=[{"source": "a.pdf"}, {"source": "b.pdf"}, {"source": "c.pdf"}],
    )
    return c


class TestRetrieveIntegration:
    def test_retrieve_uses_reranker_when_enabled(self, chain, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.RERANK_ENABLED", True)
        monkeypatch.setattr("src.nextpulse.config.RERANK_CANDIDATES", 20)
        fake = FakeCE({"beta": 0.9, "gamma": 0.5, "alpha": 0.1})
        monkeypatch.setattr(reranker, "get_model", lambda: fake)

        docs, metas, scores, top = chain.retrieve("autovelox", k=3)
        # beta should rank first thanks to the cross-encoder, regardless of hybrid order
        assert metas[0]["source"] == "b.pdf"

    def test_retrieve_unchanged_when_disabled(self, chain, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.RERANK_ENABLED", False)
        called = {"n": 0}
        monkeypatch.setattr(reranker, "get_model",
                            lambda: (_ for _ in ()).throw(AssertionError("must not load")))
        docs, metas, scores, top = chain.retrieve("autovelox", k=3)
        assert len(docs) == 3  # pipeline ran via plain hybrid search, reranker untouched
