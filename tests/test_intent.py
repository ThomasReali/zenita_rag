"""Tests for the intent gate: off-topic/chit-chat → plain reply, no governance chrome."""
from unittest.mock import MagicMock, patch

import pytest


def _mk(content):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)
    monkeypatch.setattr("src.nextpulse.config.RESPONSE_CACHE_ENABLED", False)
    monkeypatch.setattr("src.nextpulse.config.INTENT_GATE", True)  # enable for these tests
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "intent_test")
    from src.nextpulse.rag_chain import RAGChain
    c = RAGChain()
    c.vector_store.add_documents(
        texts=["Il T-EXCEED V2 è un autovelox omologato dal MIT."],
        metadatas=[{"source": "texceed.pdf"}],
    )
    return c


class TestIntentGate:
    def test_off_topic_gets_plain_reply(self, chain):
        # classify → ALTRO, then a plain conversational reply. No retrieval/generation.
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[_mk("ALTRO"), _mk("Ciao! Come posso aiutarti?")]) as m:
            r = chain.query("ciao")
        assert r["off_topic"] is True
        assert r["grounded"] is False
        assert r["ambiguous"] is False
        assert r["sources"] == []
        assert r["context"] == []                 # no chunks attached
        assert "aiutarti" in r["response"]
        assert m.call_count == 2                   # classify + plain reply only

    def test_domain_query_runs_grounded_pipeline(self, chain):
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[_mk("DOMINIO"), _mk("Il T-EXCEED è un autovelox [1].")]) as m:
            r = chain.query("Cos'è il T-EXCEED?")
        assert r["off_topic"] is False
        assert r["grounded"] is True
        assert r["sources"] == ["texceed.pdf"]
        assert m.call_count == 2                   # classify + generation

    def test_classifier_failsafe_to_domain_on_error(self, chain):
        # First create() (the classifier) raises → _is_domain_query returns True (domain),
        # so the second create() (generation) runs the grounded pipeline.
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[RuntimeError("boom"), _mk("Risposta grounded [1].")]):
            r = chain.query("Quali requisiti per gli autovelox?")
        assert r["off_topic"] is False
        assert r["grounded"] is True

    def test_gate_disabled_skips_classifier(self, chain, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.INTENT_GATE", False)
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[_mk("Risposta [1].")]) as m:
            r = chain.query("ciao")
        assert r["off_topic"] is False             # no classification → straight to pipeline
        assert m.call_count == 1                   # only generation, no classifier call

    def test_off_topic_streams_as_plain(self, chain):
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[_mk("ALTRO"), _mk("Ciao, dimmi pure.")]):
            events = list(chain.stream_query("grazie mille"))
        done = events[-1][1]
        assert done["off_topic"] is True
        assert done["sources"] == []
