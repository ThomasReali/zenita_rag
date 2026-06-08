"""Tests for streaming (SSE): RAGChain.stream_query + /api/query/stream endpoint."""
import json
from unittest.mock import MagicMock, patch

import pytest


def _chunk(text):
    """Fake OpenAI streaming chunk exposing choices[0].delta.content."""
    return MagicMock(choices=[MagicMock(delta=MagicMock(content=text))])


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)
    monkeypatch.setattr("src.nextpulse.config.RESPONSE_CACHE_ENABLED", False)  # isolate streaming
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "stream_test")
    from src.nextpulse.rag_chain import RAGChain
    return RAGChain()


class TestStreamQuery:
    def test_generate_branch_streams_tokens(self, chain):
        chain.vector_store.add_documents(
            texts=["Il T-EXCEED V2 è un autovelox omologato."],
            metadatas=[{"source": "texceed.pdf"}],
        )
        chunks = [_chunk("Il "), _chunk("T-EXCEED "), _chunk("è un autovelox [1].")]
        with patch.object(chain.client.chat.completions, "create", return_value=iter(chunks)):
            events = list(chain.stream_query("Cos'è il T-EXCEED?"))

        phases = [e[0] for e in events]
        assert phases[0] == "meta"
        assert phases[-1] == "done"
        assert phases.count("token") == 3            # one event per delta
        streamed = "".join(p for ph, p in events if ph == "token")
        assert "T-EXCEED" in streamed
        done = events[-1][1]
        assert done["grounded"] is True
        assert "[1]" in done["response"]             # finalized answer keeps the citation
        assert done["sources"] == ["texceed.pdf"]

    def test_message_branch_emits_single_token(self, chain):
        # Empty KB → gate 1 (no context): the whole fallback is one token, no LLM stream.
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            events = list(chain.stream_query("domanda senza documenti"))
        mock_create.assert_not_called()
        phases = [e[0] for e in events]
        assert phases == ["meta", "token", "done"]
        done = events[-1][1]
        assert done["grounded"] is False
        assert done["response"]                       # deterministic fallback message

    def test_done_result_shape_matches_query(self, chain):
        chain.vector_store.add_documents(
            texts=["Autovelox omologato."], metadatas=[{"source": "x.pdf"}]
        )
        with patch.object(chain.client.chat.completions, "create",
                          return_value=iter([_chunk("Risposta [1].")])):
            done = list(chain.stream_query("Cos'è?"))[-1][1]
        for key in ("query", "standalone_query", "response", "sources", "context",
                    "model", "grounded", "ambiguous", "obsolete", "top_score",
                    "role", "confidence", "cached"):
            assert key in done


# ── /api/query/stream endpoint ───────────────────────────────────────────────

class _FakeVS:
    def get_stats(self):
        return {"count": 10, "collection": "x"}

    def count_sources(self):
        return 3


class _StreamRAG:
    def __init__(self):
        self.vector_store = _FakeVS()
        self.model = "fake/model"
        self.pseudonymizer = MagicMock()

    def stream_query(self, question, chat_history=None, k=None, role=None):
        yield ("meta", {"grounded": True, "confidence": "green", "role": role, "cached": False})
        yield ("token", "Ciao ")
        yield ("token", "mondo [1].")
        yield ("done", {
            "query": question, "standalone_query": question, "response": "Ciao mondo [1].",
            "sources": ["a.pdf"], "context": [], "model": self.model, "grounded": True,
            "ambiguous": False, "obsolete": False, "top_score": 0.9, "role": role,
            "confidence": "green", "cached": False,
        })


def _parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        line = block.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


class TestStreamEndpoint:
    def test_stream_endpoint_emits_phases(self, monkeypatch):
        from fastapi.testclient import TestClient
        from src.nextpulse import api

        monkeypatch.setattr(api, "RAGChain", _StreamRAG)
        monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)

        with TestClient(api.app) as client:
            resp = client.post("/api/query/stream", json={"question": "ciao"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(resp.text)
        phases = [e["phase"] for e in events]
        assert phases == ["meta", "token", "token", "done"]
        assert events[-1]["data"]["response"] == "Ciao mondo [1]."
        assert events[-1]["data"]["sources"] == ["a.pdf"]
