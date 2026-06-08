"""Tests for the grounded offer-draft configurator + /api/configure endpoint."""
from unittest.mock import MagicMock, patch

import pytest

from src.nextpulse.configurator import OfferConfigurator, NO_CONTEXT_DRAFT


def _answer(text):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)   # gate open by default
    monkeypatch.setattr("src.nextpulse.config.RESPONSE_CACHE_ENABLED", False)
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "cfg_test")
    from src.nextpulse.rag_chain import RAGChain
    c = RAGChain()
    c.vector_store.add_documents(
        texts=[
            "Il sistema ZTL T-EXCEED gestisce i varchi di accesso con telecamere ANPR.",
            "L'autovelox V-MAX rileva la velocità su più corsie, omologato MIT.",
            "Il rilevatore semaforico R-RED documenta il passaggio con rosso.",
        ],
        metadatas=[{"source": "ztl.pdf"}, {"source": "velocita.pdf"}, {"source": "semaforo.pdf"}],
    )
    return c


class TestConfigure:
    def test_grounded_draft_with_sources(self, chain):
        draft_text = ("**Scenario**: ZTL + velocità [1][2].\n**Soluzioni proposte**: ... [1] [2].\n"
                      "⚠ Bozza non vincolante: prezzi, quantità e conformità finali vanno validati "
                      "dal Bid Manager.")
        with patch.object(chain.client.chat.completions, "create",
                          return_value=_answer(draft_text)) as mock_create:
            res = OfferConfigurator(chain).configure(
                "Comune medio: vuole ZTL e controllo velocità",
                needs=["ZTL varchi accesso", "controllo velocità autovelox"],
            )
        assert res["grounded"] is True
        assert mock_create.call_count == 1
        assert "Bid Manager" in res["draft"]            # non-binding disclaimer present
        assert len(res["sources"]) >= 1
        assert res["top_score"] > 0

    def test_fallback_when_nothing_relevant(self, chain, monkeypatch):
        # Raise the gate so retrieval is judged irrelevant → honest fallback, no LLM call.
        monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.999)
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            res = OfferConfigurator(chain).configure("Tema completamente fuori contesto")
        mock_create.assert_not_called()
        assert res["grounded"] is False
        assert res["draft"] == NO_CONTEXT_DRAFT

    def test_gather_dedups_across_queries(self, chain):
        cfg = OfferConfigurator(chain)
        # The same need repeated must not duplicate evidence chunks.
        docs, metas, scores, top = cfg._gather(["ZTL", "ZTL", "ZTL varchi"], k=5)
        keys = [(m.get("source"), d[:80]) for d, m in zip(docs, metas)]
        assert len(keys) == len(set(keys))             # all distinct


# ── /api/configure endpoint ──────────────────────────────────────────────────

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


class _FakeConfigurator:
    def __init__(self, rag):
        self.rag = rag

    def configure(self, scenario, needs=None, k=None):
        return {"scenario": scenario, "draft": "Bozza... ⚠ valida col Bid Manager.",
                "sources": ["scheda.pdf"], "grounded": True, "top_score": 0.88, "latency_ms": 7}


class TestConfigureEndpoint:
    def test_endpoint_returns_draft(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.nextpulse import api

        monkeypatch.setattr(api, "RAGChain", _FakeRAG)
        monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)
        monkeypatch.setattr("src.nextpulse.configurator.OfferConfigurator", _FakeConfigurator)

        with TestClient(api.app) as client:
            r = client.post("/api/configure",
                            json={"scenario": "Comune medio ZTL + velocità"})
        assert r.status_code == 200
        body = r.json()
        assert body["grounded"] is True
        assert "Bid Manager" in body["draft"]
        assert body["sources"] == ["scheda.pdf"]
