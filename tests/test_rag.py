"""Test suite for the NextPulse RAG system.

Run with:  pytest tests/ -v

Tests marked 'integration' require OPENAI_API_KEY to be set.
Tests marked 'slow' perform embedding downloads (first run only).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure the project root is on sys.path ───────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


###############################################################################
# 1.  Config
###############################################################################

class TestConfig:
    def test_import_loads_without_crashing(self):
        """Importing config must not trigger I/O side effects."""
        from src.nextpulse import config
        assert config.CHUNK_SIZE == 500
        assert config.CHUNK_OVERLAP == 50
        assert config.RETRIEVAL_K == 5
        assert config.EMBEDDING_MODEL == "intfloat/multilingual-e5-small"

    def test_defaults_when_no_env(self, monkeypatch):
        """All values fall back to hard-coded defaults when .env is absent."""
        monkeypatch.delenv("CHUNK_SIZE", raising=False)
        monkeypatch.delenv("CHUNK_OVERLAP", raising=False)
        monkeypatch.delenv("RETRIEVAL_K", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("QDRANT_PATH", raising=False)
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        # Don't let a real .env repopulate the keys we just cleared on reload.
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)

        import importlib
        from src.nextpulse import config
        importlib.reload(config)

        assert config.CHUNK_SIZE == 500
        assert config.OPENAI_API_KEY == ""
        assert config.DATA_DIR.name == "data"

    def test_ensure_directories_creates_dirs(self, monkeypatch, tmp_path):
        """ensure_directories() creates both directories."""
        monkeypatch.setattr(
            "src.nextpulse.config.QDRANT_PATH",
            tmp_path / ".test_qdrant",
        )
        monkeypatch.setattr(
            "src.nextpulse.config.DATA_DIR",
            tmp_path / ".test_data",
        )
        from src.nextpulse.config import ensure_directories
        ensure_directories()
        assert (tmp_path / ".test_qdrant").exists()
        assert (tmp_path / ".test_data").exists()


###############################################################################
# 2.  DocumentProcessor
###############################################################################

class TestDocumentProcessor:
    def test_load_text(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        txt = tmp_path / "sample.txt"
        txt.write_text("Hello world.", encoding="utf-8")

        processor = DocumentProcessor()
        result = processor.load_text(str(txt))
        assert result == "Hello world."

    def test_load_document_txt(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        txt = tmp_path / "sample.txt"
        txt.write_text("Hello.", encoding="utf-8")

        processor = DocumentProcessor()
        result = processor.load_document(str(txt))
        assert result == "Hello."

    def test_load_document_unsupported_raises(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        img = tmp_path / "image.png"
        img.write_text("fake", encoding="utf-8")

        processor = DocumentProcessor()
        with pytest.raises(ValueError, match="Unsupported format"):
            processor.load_document(str(img))

    def test_chunk_words_respects_max(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(min_size=5, max_size=10)
        text = ". ".join(f"s{i} alpha beta gamma" for i in range(10)) + "."
        chunks = processor.chunk_text(text)
        assert len(chunks) > 1
        assert all(len(c.split()) <= 10 for c in chunks)           # never exceeds max
        assert all(len(c.split()) >= 5 for c in chunks[:-1])       # only the tail may be < min

    def test_chunk_words_short_is_single_chunk(self):
        from src.nextpulse.document_processor import DocumentProcessor

        assert DocumentProcessor().chunk_text("tiny") == ["tiny"]

    def test_chunk_text_empty(self):
        from src.nextpulse.document_processor import DocumentProcessor

        assert DocumentProcessor().chunk_text("   ") == []

    def test_chunk_text_normalizes_whitespace(self):
        from src.nextpulse.document_processor import DocumentProcessor

        chunks = DocumentProcessor().chunk_text("hello    world\n\ntest")
        assert "  " not in chunks[0]
        assert "\n" not in chunks[0]

    def test_chunk_words_cuts_at_legal_boundary(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(min_size=5, max_size=100)
        text = "uno due tre quattro cinque sei\nArticolo 2 alfa beta gamma delta"
        chunks = processor.chunk_text(text)
        assert len(chunks) == 2
        assert chunks[1].startswith("Articolo 2")     # cut at the structural boundary

    def test_chunk_words_ignores_boundary_below_min(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(min_size=50, max_size=100)
        text = "uno due tre\nArticolo 2 alfa beta"
        chunks = processor.chunk_text(text)
        assert len(chunks) == 1                        # boundary ignored: chunk too small

    def test_process_document_adds_metadata(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        txt = tmp_path / "doc1.txt"
        txt.write_text("x" * 600, encoding="utf-8")

        processor = DocumentProcessor()
        results = processor.process_document(str(txt))
        assert len(results) >= 1
        for _, meta in results:
            assert meta["source"] == "doc1.txt"
            assert isinstance(meta["chunk_id"], int)
            assert meta["doc_type"] == "txt"

    def test_process_directory_routes_and_skips(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        (tmp_path / "a.txt").write_text("hello world from a document", encoding="utf-8")
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n")          # unsupported → skip
        (tmp_path / "manifest_download.json").write_text("[]", encoding="utf-8")  # metadata → skip

        processor = DocumentProcessor()
        results = processor.process_directory(str(tmp_path))
        sources = {m["source"] for _, m in results}

        assert "a.txt" in sources
        assert "img.png" not in sources
        assert "manifest_download.json" not in sources
        assert any("non supportata" in why for _, why in processor.skipped)
        assert any("manifest" in why for _, why in processor.skipped)

    def test_process_document_csv_chunks_with_header(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        csv = tmp_path / "t.csv"
        csv.write_text("col_a;col_b\n1;2\n3;4\n", encoding="utf-8")

        processor = DocumentProcessor()
        results = processor.process_document(str(csv))
        assert len(results) >= 1
        assert results[0][1]["doc_type"] == "csv"
        assert "col_a" in results[0][0] and "col_b" in results[0][0]  # header repeated

    def test_load_json_flattens(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        j = tmp_path / "d.json"
        j.write_text('{"title": "Decreto X", "year": 2020}', encoding="utf-8")

        processor = DocumentProcessor()
        text = processor.load_document(str(j))
        assert "Decreto X" in text


###############################################################################
# 3.  VectorStore
###############################################################################

# ChromaDB rejects empty dicts as metadata; use a dummy fixture.
_EMPTY_META = {"source": "test"}


class TestVectorStore:
    @pytest.fixture(autouse=True)
    def _isolate_store(self, monkeypatch, tmp_path):
        """Force every VectorStore in this class to use a temp Qdrant path."""
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.QDRANT_PATH",
            tmp_path / "qdrant_test",
        )
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.COLLECTION_NAME",
            "test_collection",
        )

    def test_init_creates_collection(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        assert vs.get_stats()["collection"] == "test_collection"

    def test_add_and_search(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(
            texts=["Rome is the Italian capital.", "The sun is a star."],
            metadatas=[{"source": "geo.txt"}, {"source": "astro.txt"}],
        )

        docs, metas, scores, max_cosine = vs.search("capital of Italy")
        assert len(docs) > 0
        assert "Rome" in docs[0]
        assert metas[0]["source"] == "geo.txt"
        assert max_cosine > 0

    def test_search_limit_k(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(
            texts=["a", "b", "c", "d", "e"],
            metadatas=[_EMPTY_META] * 5,
        )
        docs, _, _, _ = vs.search("a", k=2)
        assert len(docs) == 2

    def test_get_stats_empty(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        stats = vs.get_stats()
        assert stats["count"] == 0
        assert stats["collection"] == "test_collection"

    def test_reindex_does_not_collide(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(texts=["first"], metadatas=[_EMPTY_META])
        vs.add_documents(texts=["second"], metadatas=[_EMPTY_META])
        stats = vs.get_stats()
        assert stats["count"] == 2


###############################################################################
# 4.  RAGChain (unit, no live API)
###############################################################################

MOCK_COMPLETION = MagicMock()
MOCK_COMPLETION.choices = [
    MagicMock(message=MagicMock(content="riformulata standalone"))
]

MOCK_ANSWER = MagicMock()
MOCK_ANSWER.choices = [
    MagicMock(message=MagicMock(content="Engine SpA answer"))
]


class TestRAGChainUnit:
    """Tests that don't hit the real OpenAI API."""

    @pytest.fixture
    def chain(self, monkeypatch, tmp_path):
        # Monkeypatch config attributes directly — config is already imported,
        # so setenv won't help.
        monkeypatch.setattr(
            "src.nextpulse.config.OPENAI_API_KEY",
            "sk-test",
        )
        monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)  # disable gate by default
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.QDRANT_PATH",
            tmp_path / "qdrant_test",
        )
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.COLLECTION_NAME",
            "unit_test",
        )
        from src.nextpulse.rag_chain import RAGChain
        return RAGChain()

    def test_init_requires_key(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "")
        from src.nextpulse.rag_chain import RAGChain
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            RAGChain()

    # ── _build_chat_context ──────────────────────────────────────────────

    def test_build_context_empty(self, chain):
        assert "Nessuna cronologia" in chain._build_chat_context([])

    def test_build_context_formats_roles(self, chain):
        ctx = chain._build_chat_context([
            {"role": "user", "content": "quanto costa?"},
            {"role": "assistant", "content": "Dipende dal modello."},
        ])
        assert "Venditore: quanto costa?" in ctx
        assert "Assistente: Dipende dal modello." in ctx

    def test_build_context_truncates_to_6(self, chain):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        ctx = chain._build_chat_context(msgs)
        lines = [l for l in ctx.split("\n") if l.startswith("Venditore")]
        assert len(lines) == 6
        assert "msg4" in lines[0]
        assert "msg9" in lines[-1]

    # ── _reformulate_query ───────────────────────────────────────────────

    def test_reformulate_no_history_returns_query(self, chain):
        assert chain._reformulate_query("test", []) == "test"

    def test_reformulate_calls_openai(self, chain):
        with patch.object(
            chain.client.chat.completions, "create",
            return_value=MOCK_COMPLETION,
        ):
            result = chain._reformulate_query("costa?", [
                {"role": "user", "content": "parlami del T-EXCEED"},
            ])
        assert result == "riformulata standalone"

    # ── query ────────────────────────────────────────────────────────────

    def test_query_pipeline_happy_path(self, chain):
        """Full pipeline: reformulate → retrieve → generate."""
        chain.vector_store.add_documents(
            texts=["Il T-EXCEED V2 è un autovelox."],
            metadatas=[{"source": "texceed.pdf"}],
        )

        with patch.object(
            chain.client.chat.completions, "create",
            side_effect=[MOCK_COMPLETION, MOCK_ANSWER],
        ):
            result = chain.query(
                "quanto costa?",
                chat_history=[
                    {"role": "user", "content": "parlami del T-EXCEED V2"},
                    {"role": "assistant", "content": "È un autovelox."},
                ],
            )
        assert result["query"] == "quanto costa?"
        assert result["standalone_query"] == "riformulata standalone"
        assert result["response"] == "Engine SpA answer"
        assert len(result["context"]) >= 1
        assert result["sources"] == ["texceed.pdf"]
        from src.nextpulse import config
        assert result["model"] == config.CHAT_MODEL

    def test_query_no_docs_triggers_fallback(self, chain):
        from src.nextpulse.rag_chain import NO_CONTEXT_MESSAGE

        with patch.object(chain.client.chat.completions, "create") as mock_create:
            result = chain.query("query senza documenti")
        assert result["context"] == []
        assert result["grounded"] is False
        assert result["response"] == NO_CONTEXT_MESSAGE
        mock_create.assert_not_called()  # no generation when nothing is retrieved

    def test_query_low_score_triggers_fallback(self, chain, monkeypatch):
        from src.nextpulse.rag_chain import NO_CONTEXT_MESSAGE

        monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.999)
        chain.vector_store.add_documents(
            texts=["Il T-EXCEED è un autovelox."], metadatas=[{"source": "t.pdf"}]
        )
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            result = chain.query("argomento totalmente diverso")
        assert result["grounded"] is False
        assert result["response"] == NO_CONTEXT_MESSAGE
        mock_create.assert_not_called()

    # ── ambiguity gate (LLM conflict judge) ──────────────────────────────────

    def test_ambiguity_judge_triggers_discretion(self, chain):
        from src.nextpulse.rag_chain import AMBIGUITY_MESSAGE

        chain.vector_store.add_documents(
            texts=["Il decreto A prevede X.", "Il decreto B prevede Y."],
            metadatas=[{"source": "a.pdf", "decreto": "100"},
                       {"source": "b.pdf", "decreto": "200"}],
        )
        judge = MagicMock()
        judge.choices = [MagicMock(message=MagicMock(content="CONFLITTO"))]
        with patch.object(chain.client.chat.completions, "create",
                          return_value=judge) as mock_create:
            result = chain.query("cosa prevede la norma?")
        assert result["ambiguous"] is True
        assert result["grounded"] is False
        assert AMBIGUITY_MESSAGE in result["response"]
        assert mock_create.call_count == 1  # only the judge — no answer generated

    def test_ambiguity_judge_ok_allows_answer(self, chain):
        chain.vector_store.add_documents(
            texts=["Il decreto A prevede X.", "Il decreto B conferma X."],
            metadatas=[{"source": "a.pdf"}, {"source": "b.pdf"}],
        )
        judge = MagicMock()
        judge.choices = [MagicMock(message=MagicMock(content="OK"))]
        with patch.object(chain.client.chat.completions, "create",
                          side_effect=[judge, MOCK_ANSWER]):
            result = chain.query("cosa prevede?")
        assert result["ambiguous"] is False
        assert result["grounded"] is True
        assert result["response"] == "Engine SpA answer"

    def test_ambiguity_judge_skipped_for_single_source(self, chain):
        """Bug fix: chunks from ONE source can't conflict → the judge (and its LLM call)
        is skipped, so a genuine single-document answer is not wrongly gated."""
        chain.vector_store.add_documents(
            texts=["Il decreto A prevede X.", "Il decreto A specifica anche Y."],
            metadatas=[{"source": "a.pdf"}, {"source": "a.pdf"}],
        )
        with patch.object(chain.client.chat.completions, "create",
                          return_value=MOCK_ANSWER) as mock_create:
            result = chain.query("cosa prevede il decreto A?")
        assert result["ambiguous"] is False
        assert result["grounded"] is True
        assert result["response"] == "Engine SpA answer"
        assert mock_create.call_count == 1  # only generation — conflict judge skipped

    def test_ambiguity_discretion_cites_sources_with_role(self, chain):
        """Bug fix: discretion must list the conflicting provvedimenti even for a
        non-legal role (the old role-red template hid the sources)."""
        from src.nextpulse.rag_chain import AMBIGUITY_MESSAGE

        chain.vector_store.add_documents(
            texts=["Il decreto A fissa il limite a 50.", "Il decreto B fissa il limite a 70."],
            metadatas=[{"source": "a.pdf", "decreto": "100"},
                       {"source": "b.pdf", "decreto": "200"}],
        )
        judge = MagicMock()
        judge.choices = [MagicMock(message=MagicMock(content="CONFLITTO"))]
        with patch.object(chain.client.chat.completions, "create", return_value=judge):
            result = chain.query("quale limite si applica?", role="presales")
        assert result["ambiguous"] is True
        assert result["confidence"] == "red"
        assert AMBIGUITY_MESSAGE in result["response"]
        assert "a.pdf" in result["response"] and "b.pdf" in result["response"]

    # ── role-awareness integration ───────────────────────────────────────────

    def test_query_role_presales_cites_source(self, chain):
        chain.vector_store.add_documents(
            texts=["Velomatic FX: ±2 km/h, 6 corsie."],
            metadatas=[{"source": "datasheet.pdf", "page": 2}],
        )
        with patch.object(chain.client.chat.completions, "create", return_value=MOCK_ANSWER):
            result = chain.query("specifiche velomatic", role="presales")
        assert result["role"] == "presales"
        assert result["confidence"] in ("green", "yellow")
        assert "Fonte" in result["response"]  # Pre-Sales cita la fonte

    def test_query_role_sales_red_no_source(self, chain):
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            result = chain.query("ricetta della carbonara", role="sales")
        assert result["role"] == "sales"
        assert result["confidence"] == "red"
        assert "Fonte" not in result["response"]   # Sales non cita fonti
        mock_create.assert_not_called()

    # ── reversible pseudonymization (GDPR Art. 32) ───────────────────────────
    def test_pii_masked_before_llm_and_unmasked_after(self, chain):
        """Zero-knowledge: the provider sees tokens; the user sees real data restored."""
        chain.vector_store.add_documents(
            texts=["Referente Mario Rossi, margine 15% per il Comune di Milano."],
            metadatas=[{"source": "offerta.pdf", "page": 3}],
        )
        seen = {}

        def fake_create(model, messages, **kw):
            seen["payload"] = "\n".join(m["content"] for m in messages)
            # the LLM manipulates the tokens, never the real values
            content = "Per il [ORG_1] contatta [PERSON_1] (margine [PERCENT_1])."
            return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])

        with patch.object(chain.client.chat.completions, "create", side_effect=fake_create):
            result = chain.query("a chi mi rivolgo per l'offerta?")

        # outbound to OpenRouter: real PII absent, tokens present
        assert "Mario Rossi" not in seen["payload"]
        assert "Comune di Milano" not in seen["payload"]
        assert "[PERSON_1]" in seen["payload"] and "[ORG_1]" in seen["payload"]
        # inbound to the user: re-identified
        assert "Mario Rossi" in result["response"]
        assert "Comune di Milano" in result["response"]
        assert result["pii_masked"] >= 2

    def test_query_respects_custom_k(self, chain):
        chain.vector_store.add_documents(
            texts=["a", "b", "c", "d", "e"],
            metadatas=[_EMPTY_META] * 5,
        )
        with patch.object(
            chain.client.chat.completions, "create",
            side_effect=[MOCK_COMPLETION, MOCK_ANSWER],
        ):
            result = chain.query("x", k=2)
        assert len(result["context"]) == 2

    def test_condense_prompt_contains_required_words(self, chain):
        from src.nextpulse.rag_chain import CONDENSE_QUESTION_PROMPT
        assert "riformula" in CONDENSE_QUESTION_PROMPT
        assert "cronologia" in CONDENSE_QUESTION_PROMPT
        assert "MANTIENI" in CONDENSE_QUESTION_PROMPT

    def test_system_prompt_contains_required_words(self, chain):
        from src.nextpulse.rag_chain import SYSTEM_PROMPT
        assert "GROUNDING" in SYSTEM_PROMPT
        assert "NO HALLUCINATION" in SYSTEM_PROMPT
        assert "Bid Manager" in SYSTEM_PROMPT


###############################################################################
# 5.  Scripts (smoke)
###############################################################################

class TestScripts:
    def test_index_script_imports(self):
        """index_documents.py is importable without side effects."""
        import scripts.index_documents  # noqa: F401

    def test_query_script_imports(self):
        import scripts.query_rag  # noqa: F401

    def test_anonymize_script_imports(self):
        import scripts.anonymize_logs  # noqa: F401


###############################################################################
# 6.  FastAPI backend (stubbed RAGChain — no model load, no Qdrant)
###############################################################################

class _FakeVS:
    def get_stats(self):
        return {"count": 10, "collection": "x"}

    def count_sources(self):
        return 3


class _FakePseudoSession:
    """Minimal stand-in for a pseudonymizer session (identity masking)."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def mask(self, text):
        return text


class _FakePseudonymizer:
    def session(self):
        return _FakePseudoSession()


class _FakeRAG:
    def __init__(self):
        self.vector_store = _FakeVS()
        self.model = "fake/model"
        # api.py masks query text before logging when PII_MASKING_ENABLED is on.
        self.pseudonymizer = _FakePseudonymizer()

    def query(self, question, chat_history=None, k=None, role=None):
        if question == "boom":
            raise RuntimeError("provider 429")
        return {
            "query": question, "standalone_query": question, "response": "ok",
            "sources": ["a.pdf (pag. 1)"], "context": ["ctx"],
            "model": self.model, "grounded": True, "ambiguous": False, "top_score": 0.9,
            "role": role, "confidence": "green",
        }


class TestAPI:
    def _client(self, monkeypatch, log_db=None):
        from fastapi.testclient import TestClient
        from src.nextpulse import api
        monkeypatch.setattr(api, "RAGChain", _FakeRAG)
        if log_db is None:
            # Keep the query log off so other API tests don't write a db file.
            monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)
        else:
            monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", True)
            monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_PATH", log_db)
        return TestClient(api.app)

    def test_status(self, monkeypatch):
        with self._client(monkeypatch) as client:
            s = client.get("/api/status").json()
        assert s == {"documents": 3, "chunks": 10, "model": "fake/model"}

    def test_query_ok(self, monkeypatch):
        with self._client(monkeypatch) as client:
            r = client.post("/api/query", json={"question": "ciao", "history": []}).json()
        assert r["grounded"] is True
        assert r["sources"] == ["a.pdf (pag. 1)"]
        assert r["model"] == "fake/model"

    def test_query_error_is_502(self, monkeypatch):
        with self._client(monkeypatch) as client:
            resp = client.post("/api/query", json={"question": "boom"})
        assert resp.status_code == 502
        # Security: raw provider/exception text must NOT leak to the client.
        detail = resp.json()["detail"]
        assert "provider 429" not in detail
        assert "RuntimeError" not in detail
        assert "non è momentaneamente disponibile" in detail

    def test_query_rejects_abusive_input(self, monkeypatch):
        """Input-validation hard limits return 422 (defense-in-depth, not 500/502)."""
        with self._client(monkeypatch) as client:
            assert client.post("/api/query", json={"question": ""}).status_code == 422
            assert client.post("/api/query", json={"question": "A" * 9000}).status_code == 422
            assert client.post("/api/query", json={"question": "x", "k": -1}).status_code == 422
            assert client.post("/api/query", json={"question": "x", "k": 9999}).status_code == 422
            big_history = [{"role": "user", "content": "a"} for _ in range(50)]
            assert client.post(
                "/api/query", json={"question": "x", "history": big_history}
            ).status_code == 422

    def test_query_role_passthrough(self, monkeypatch):
        with self._client(monkeypatch) as client:
            r = client.post("/api/query", json={"question": "ciao", "role": "bid_manager"}).json()
        assert r["role"] == "bid_manager"
        assert r["confidence"] == "green"

    def test_roles_endpoint(self, monkeypatch):
        with self._client(monkeypatch) as client:
            data = client.get("/api/roles").json()
        keys = {r["key"] for r in data}
        assert keys == {"sales", "presales", "bid_manager"}

    def test_query_is_logged(self, monkeypatch, tmp_path):
        with self._client(monkeypatch, log_db=tmp_path / "q.db") as client:
            client.post("/api/query", json={
                "question": "ciao", "session_id": "s1", "user_id": "u1",
            })
            p = client.get("/api/privacy").json()
        assert p["logging_enabled"] is True
        assert p["retention_months"] == 6
        assert p["total"] >= 1

    def test_privacy_when_logging_disabled(self, monkeypatch):
        with self._client(monkeypatch) as client:
            p = client.get("/api/privacy").json()
        assert p["logging_enabled"] is False
        assert p["retention_months"] == 6


###############################################################################
# 7.  role_manager (standalone module)
###############################################################################

class TestRoles:
    def test_default_and_persistence(self, tmp_path):
        from role_manager import RoleManager
        p = tmp_path / "role_state.json"
        assert RoleManager(state_path=p).current_key == "presales"   # default
        rm = RoleManager(state_path=p)
        rm.set_role("bid_manager")
        assert RoleManager(state_path=p).current_key == "bid_manager"  # persisted

    def test_invalid_role_raises(self, tmp_path):
        from role_manager import RoleManager
        with pytest.raises(ValueError):
            RoleManager(state_path=tmp_path / "s.json").set_role("ceo")

    def test_system_prompt_per_role(self, tmp_path):
        from role_manager import RoleManager
        rm = RoleManager(state_path=tmp_path / "s.json")
        rm.set_role("bid_manager")
        assert "BID MANAGER" in rm.get_system_prompt()
        rm.set_role("sales")
        assert "SALES" in rm.get_system_prompt()

    def test_format_red_refuses(self, tmp_path):
        from role_manager import RoleManager
        rm = RoleManager(state_path=tmp_path / "s.json")
        rm.set_role("bid_manager")
        out = rm.format_response("qualcosa", [], "red").lower()
        assert "conformità" in out or "escalation" in out

    def test_format_presales_cites_source(self, tmp_path):
        from role_manager import RoleManager
        rm = RoleManager(state_path=tmp_path / "s.json")
        rm.set_role("presales")
        out = rm.format_response("±2 km/h", [{"source": "datasheet.pdf", "page": 2}], "green")
        assert "±2 km/h" in out and "Fonte" in out

    def test_format_sales_no_source(self, tmp_path):
        from role_manager import RoleManager
        rm = RoleManager(state_path=tmp_path / "s.json")
        rm.set_role("sales")
        out = rm.format_response("Copre l'intera carreggiata", [{"source": "x.pdf"}], "green")
        assert "Fonte" not in out


###############################################################################
# 8.  Query log + GDPR data-anonymization
###############################################################################

class TestQueryLog:
    def _log(self, tmp_path):
        from src.nextpulse.query_log import QueryLog
        return QueryLog(db_path=tmp_path / "q.db")

    def test_record_and_stats(self, tmp_path):
        log = self._log(tmp_path)
        log.record(question="q1", role="sales", user_id="u1", session_id="s1",
                   confidence="green", grounded=True, ambiguous=False,
                   top_score=0.9, n_sources=1, model="m")
        s = log.stats()
        assert s["total"] == 1 and s["identified"] == 1 and s["anonymized"] == 0
        assert s["retention_months"] == 6

    def test_record_result_from_query_dict(self, tmp_path):
        log = self._log(tmp_path)
        result = {
            "query": "q", "role": "presales", "standalone_query": "q",
            "confidence": "green", "grounded": True, "ambiguous": False,
            "top_score": 0.88, "sources": ["a.pdf (pag. 1)"], "model": "m",
        }
        rid = log.record_result(result, session_id="s1", user_id="u1")
        assert rid >= 1
        assert log.stats()["total"] == 1

    def test_anonymize_older_than(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        log = self._log(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=200)     # > 6 months
        recent = datetime.now(timezone.utc) - timedelta(days=10)   # < 6 months
        log.record(question="old", user_id="u1", session_id="s1", created_at=old)
        log.record(question="new", user_id="u2", session_id="s2", created_at=recent)

        changed = log.anonymize_older_than(6)
        assert changed == 1
        s = log.stats()
        assert s["total"] == 2        # rows are NOT deleted
        assert s["identified"] == 1   # only the recent row keeps its identifiers
        assert s["anonymized"] == 1

    def test_anonymize_is_idempotent(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        log = self._log(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        log.record(question="old", user_id="u1", session_id="s1", created_at=old)
        assert log.anonymize_older_than(6) == 1
        assert log.anonymize_older_than(6) == 0   # already anonymized → no-op

    def test_count_anonymizable_dry_run(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        log = self._log(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=300)
        log.record(question="old", user_id="u1", created_at=old)
        log.record(question="fresh", user_id="u2")   # now → not a candidate
        assert log.count_anonymizable(6) == 1

    def test_months_before_clamps_day(self):
        from datetime import datetime, timezone
        from src.nextpulse.query_log import _months_before
        # 31 Aug minus 6 months → Feb (clamped to 28, non-leap year)
        d = datetime(2025, 8, 31, tzinfo=timezone.utc)
        assert _months_before(d, 6).strftime("%Y-%m-%d") == "2025-02-28"


###############################################################################
# 9.  Reversible Pseudonymization (PII masking — GDPR Art. 32)
###############################################################################

class TestPseudonymizer:
    def _session(self):
        from src.nextpulse.pseudonymizer import Pseudonymizer
        return Pseudonymizer(backend="regex").session()

    def test_directive_example_masks_org_person_percent(self):
        s = self._session()
        masked = s.mask("Il margine previsto per il Comune di Milano è del 15%, referente Mario Rossi.")
        assert masked == "Il margine previsto per il [ORG_1] è del [PERCENT_1], referente [PERSON_1]."

    def test_roundtrip_restores_original(self):
        s = self._session()
        original = "Il margine per il Comune di Milano è del 15%, referente Mario Rossi."
        masked = s.mask(original)
        # an LLM "manipulates tokens" then we re-identify
        assert s.unmask(masked) == original

    def test_structured_pii_recognizers(self):
        s = self._session()
        masked = s.mask("Scrivi a mario.rossi@engine.it, IBAN IT60X0542811101000000123456.")
        assert "mario.rossi@engine.it" not in masked
        assert "IT60X0542811101000000123456" not in masked
        assert "[EMAIL_1]" in masked and "[IBAN_1]" in masked

    def test_token_reuse_same_value(self):
        s = self._session()
        masked = s.mask("Comune di Milano e ancora Comune di Milano")
        assert masked == "[ORG_1] e ancora [ORG_1]"
        assert s.masked_count == 1

    def test_identity_when_no_pii(self):
        s = self._session()
        text = "Quali requisiti per l'omologazione degli autovelox a ±2 km/h?"
        assert s.mask(text) == text
        assert s.masked_count == 0

    def test_unmask_tolerant_to_whitespace(self):
        s = self._session()
        s.mask("referente Mario Rossi")
        assert s.unmask("Contatta [ PERSON_1 ].") == "Contatta Mario Rossi."

    def test_close_wipes_map(self):
        s = self._session()
        s.mask("referente Mario Rossi")
        assert s.masked_count == 1
        s.close()
        assert s.masked_count == 0
        assert s.mapping == {}

    def test_context_manager_closes(self):
        from src.nextpulse.pseudonymizer import Pseudonymizer
        with Pseudonymizer(backend="regex").session() as s:
            s.mask("referente Mario Rossi")
            assert s.masked_count == 1
        assert s.masked_count == 0

    def test_auto_backend_falls_back_to_regex_without_presidio(self):
        from src.nextpulse.pseudonymizer import Pseudonymizer
        # Presidio/spaCy are not installed in this env → auto must degrade gracefully.
        assert Pseudonymizer(backend="auto").backend_name == "regex"
