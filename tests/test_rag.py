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
        assert config.EMBEDDING_MODEL == "all-MiniLM-L6-v2"

    def test_defaults_when_no_env(self, monkeypatch):
        """All values fall back to hard-coded defaults when .env is absent."""
        monkeypatch.delenv("CHUNK_SIZE", raising=False)
        monkeypatch.delenv("CHUNK_OVERLAP", raising=False)
        monkeypatch.delenv("RETRIEVAL_K", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("CHROMA_PERSIST_DIR", raising=False)

        import importlib
        from src.nextpulse import config
        importlib.reload(config)

        assert config.CHUNK_SIZE == 500
        assert config.OPENAI_API_KEY == ""
        assert config.DATA_DIR.name == "data"

    def test_ensure_directories_creates_dirs(self, monkeypatch, tmp_path):
        """ensure_directories() creates both directories."""
        monkeypatch.setattr(
            "src.nextpulse.config.CHROMA_PERSIST_DIR",
            tmp_path / ".test_chroma",
        )
        monkeypatch.setattr(
            "src.nextpulse.config.DATA_DIR",
            tmp_path / ".test_data",
        )
        from src.nextpulse.config import ensure_directories
        ensure_directories()
        assert (tmp_path / ".test_chroma").exists()
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

    def test_chunk_text_simple(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(chunk_size=10, chunk_overlap=2)
        chunks = processor.chunk_text("abcdefghijklmnopqrstuv")
        assert len(chunks) == 3
        assert chunks[0] == "abcdefghijklmnopqrstuv"[:10]
        assert all(len(c) > 0 for c in chunks)

    def test_chunk_text_short(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(chunk_size=100, chunk_overlap=10)
        chunks = processor.chunk_text("tiny")
        assert len(chunks) == 1
        assert chunks[0] == "tiny"

    def test_chunk_text_empty(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor()
        chunks = processor.chunk_text("   ")
        assert len(chunks) == 0

    def test_chunk_text_normalizes_whitespace(self):
        from src.nextpulse.document_processor import DocumentProcessor

        processor = DocumentProcessor(chunk_size=100)
        chunks = processor.chunk_text("hello    world\n\ntest")
        assert "  " not in chunks[0]
        assert "\n" not in chunks[0]

    def test_process_document_adds_metadata(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        txt = tmp_path / "doc1.txt"
        txt.write_text("x" * 600, encoding="utf-8")

        processor = DocumentProcessor(chunk_size=500, chunk_overlap=50)
        results = processor.process_document(str(txt))
        assert len(results) >= 1
        for _, meta in results:
            assert meta["source"] == "doc1.txt"
            assert isinstance(meta["chunk_id"], int)

    def test_process_directory_skips_non_txt_pdf(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "b.csv").write_text("a,b", encoding="utf-8")

        processor = DocumentProcessor(chunk_size=500)
        results = processor.process_directory(str(tmp_path))
        assert len(results) == 1
        assert results[0][1]["source"] == "a.txt"


###############################################################################
# 3.  VectorStore
###############################################################################

# ChromaDB rejects empty dicts as metadata; use a dummy fixture.
_EMPTY_META = {"source": "test"}


class TestVectorStore:
    @pytest.fixture(autouse=True)
    def _isolate_chroma(self, monkeypatch, tmp_path):
        """Force every VectorStore in this class to use a temp directory."""
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.CHROMA_PERSIST_DIR",
            tmp_path / "chroma_test",
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

        docs, metas = vs.search("capital of Italy")
        assert len(docs) > 0
        assert "Rome" in docs[0]
        assert metas[0]["source"] == "geo.txt"

    def test_search_limit_k(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(
            texts=["a", "b", "c", "d", "e"],
            metadatas=[_EMPTY_META] * 5,
        )
        docs, _ = vs.search("a", k=2)
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
        monkeypatch.setattr(
            "src.nextpulse.vector_store.config.CHROMA_PERSIST_DIR",
            tmp_path / "chroma_test",
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
        assert result["model"] == "gpt-4-turbo"

    def test_query_no_docs_returns_empty_context(self, chain):
        with patch.object(
            chain.client.chat.completions, "create",
            return_value=MOCK_ANSWER,
        ):
            result = chain.query("query")
        assert result["context"] == []
        assert result["sources"] == []

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
