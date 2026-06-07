"""API-level tests for the Gare d'Appalto (bandi) endpoints.

The heavy collaborators (RAGChain/embedder, Qdrant VectorStore, the live scraper)
are replaced with fakes, so these tests run offline and fast while still exercising the
endpoint wiring: SSE streaming, grouping, and the bandi chatbot contract.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.nextpulse.api as api  # noqa: E402


class _FakeVS:
    # `_bandi_vector_store` reuses the main store's client + embedder (single embedded
    # Qdrant lock), so the fake must expose them too.
    client = object()
    embedder = object()

    def count_sources(self):
        return 0

    def get_stats(self):
        return {"count": 0}


class _FakeRAG:
    """Stands in for RAGChain in both the lifespan (main) and bandi chatbot."""

    def __init__(self, *args, **kwargs):
        self.vector_store = _FakeVS()
        self.model = "fake-model"

    def query(self, question, chat_history=None, k=None, role=None):
        return {
            "query": question,
            "standalone_query": question,
            "response": f"Risposta sui bandi a: {question}",
            "context": ["chunk"],
            "sources": ["bando_G06515_doc162832"],
            "model": self.model,
            "grounded": True,
            "ambiguous": False,
            "top_score": 0.91,
            "confidence": "green",
            "pii_masked": 0,
            "latency_ms": 1,
        }


class _FakeScraper:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, progress=None):
        results = [
            {"id": "1", "title": "Bando A", "cig": "C1", "tipologia": "Procedura aperta",
             "stato": "in_corso", "category": "in_corso", "data_pubblicazione": "",
             "data_scadenza": "", "importo": "", "detail_url": "", "documents": [],
             "requirements": ["Il concorrente deve possedere requisito X"], "chunks": 3},
            {"id": "2", "title": "Bando B", "cig": "C2", "tipologia": "Affidamento",
             "stato": "aggiudicata", "category": "aggiudicazione", "data_pubblicazione": "",
             "data_scadenza": "", "importo": "", "detail_url": "", "documents": [],
             "requirements": [], "chunks": 2},
        ]
        if progress:
            progress({"phase": "listing", "total": 2})
            for i, t in enumerate(results, start=1):
                progress({"phase": "tender", "index": i, "total": 2,
                          "tender": {k: t[k] for k in ("id", "title", "cig", "tipologia",
                                     "stato", "category", "data_pubblicazione",
                                     "data_scadenza", "importo", "detail_url")},
                          "documents": t["documents"], "requirements": t["requirements"],
                          "chunks": t["chunks"]})
            progress({"phase": "done", "total": 2, "chunks": 5,
                      "by_category": {"in_corso": 1, "aggiudicazione": 1}})
        return results


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "RAGChain", _FakeRAG)
    monkeypatch.setattr(api, "VectorStore", lambda *a, **k: _FakeVS())
    monkeypatch.setattr(api, "PortaleAppaltiScraper", _FakeScraper)
    monkeypatch.setattr(api.config, "QUERY_LOG_ENABLED", False)
    with TestClient(api.app) as c:
        yield c


def test_bandi_list_empty_grouped(client):
    r = client.get("/api/bandi")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    keys = {c["key"] for c in body["categories"]}
    assert keys == {"in_corso", "aggiudicazione"}


def test_bandi_scrape_streams_sse_and_caches(client):
    r = client.get("/api/bandi/scrape")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[len("data: "):])
              for line in r.text.splitlines() if line.startswith("data: ")]
    phases = [e["phase"] for e in events]
    assert phases[0] == "listing"
    assert "done" in phases
    tender_events = [e for e in events if e["phase"] == "tender"]
    assert len(tender_events) == 2
    assert tender_events[0]["requirements"]  # requirements streamed through

    # After streaming, results are cached and grouped correctly.
    grouped = client.get("/api/bandi").json()
    assert grouped["total"] == 2
    by_key = {c["key"]: c["tenders"] for c in grouped["categories"]}
    assert len(by_key["in_corso"]) == 1
    assert len(by_key["aggiudicazione"]) == 1


def test_bandi_query_returns_grounded_answer(client):
    r = client.post("/api/bandi/query", json={"question": "Quali sono i requisiti?"})
    assert r.status_code == 200
    body = r.json()
    assert body["grounded"] is True
    assert "Risposta sui bandi" in body["response"]
    assert body["sources"]
