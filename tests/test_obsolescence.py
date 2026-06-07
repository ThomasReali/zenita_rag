"""Tests for the deterministic obsolescence / data-poisoning governance layer.

Covers: the status metadata default, the must_not retrieval filter (back-compatible
with legacy status-less points), set_status_by_source, the no-LLM "abrogato" gate,
the append-only governance log, and the hybrid audit job.

Run with:  pytest tests/test_obsolescence.py -v
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


###############################################################################
# 1.  DocumentProcessor — status / validity metadata
###############################################################################

class TestStatusMetadata:
    def test_process_document_defaults_status_active(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        txt = tmp_path / "doc.txt"
        txt.write_text("x " * 400, encoding="utf-8")
        results = DocumentProcessor().process_document(str(txt))
        assert results and all(m["status"] == "active" for _, m in results)

    def test_validity_start_from_decree_date(self, tmp_path):
        from src.nextpulse.document_processor import DocumentProcessor

        # filename carries a date → _doc_metadata extracts data_decreto → validity_start
        txt = tmp_path / "decreto_2014-08-06.txt"
        txt.write_text("Articolo 1. " + "parola " * 300, encoding="utf-8")
        results = DocumentProcessor().process_document(str(txt))
        _, meta = results[0]
        assert meta["data_decreto"] == "2014-08-06"
        assert meta["validity_start"] == "2014-08-06"


###############################################################################
# 2.  VectorStore — status filter + set_status_by_source
###############################################################################

class TestVectorStoreStatus:
    @pytest.fixture(autouse=True)
    def _isolate_store(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.nextpulse.config.QDRANT_PATH", tmp_path / "qdrant_test")
        monkeypatch.setattr("src.nextpulse.config.COLLECTION_NAME", "status_test")

    def test_add_documents_defaults_status_active(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(texts=["La capitale d'Italia è Roma."], metadatas=[{"source": "geo.txt"}])
        assert vs.source_statuses() == {"geo.txt": "active"}

    def test_search_excludes_flagged_status(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(
            texts=["La capitale d'Italia è Roma.",
                   "La capitale d'Italia è Roma secondo il vecchio decreto abrogato."],
            metadatas=[{"source": "vigente.txt"},
                       {"source": "abrogato.txt", "status": "obsolete"}],
        )
        docs, metas, _, _ = vs.search("qual è la capitale d'Italia", exclude_status=("obsolete",))
        sources = {m["source"] for m in metas}
        assert "vigente.txt" in sources
        assert "abrogato.txt" not in sources  # filtered out deterministically

    def test_legacy_pointwithout_status_still_retrievable(self):
        """Back-compat: a point predating the `status` field is NOT excluded by must_not."""
        from qdrant_client import models
        from src.nextpulse import config, vector_store
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vec = vs.embedder.encode(
            config.EMBEDDING_PASSAGE_PREFIX + "La capitale d'Italia è Roma.",
            convert_to_tensor=False,
        ).tolist()
        vs.client.upsert(
            collection_name=vs.collection_name,
            points=[models.PointStruct(
                id=str(uuid.uuid4()),
                vector={vector_store._DENSE: vec},
                payload={"source": "legacy.txt", vector_store._TEXT_KEY: "La capitale d'Italia è Roma."},
            )],
        )
        docs, metas, _, _ = vs.search(
            "qual è la capitale d'Italia", exclude_status=("obsolete", "poisoned", "draft")
        )
        assert "legacy.txt" in {m["source"] for m in metas}

    def test_set_status_by_source_flips_payload(self):
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(texts=["Il decreto X."], metadatas=[{"source": "x.pdf"}])
        vs.set_status_by_source("x.pdf", "obsolete",
                                replaced_by="D.123/2020", validity_end="2020-01-01")
        assert vs.source_statuses()["x.pdf"] == "obsolete"
        # the abrogation metadata is now queryable (unfiltered search sees it)
        _, metas, _, _ = vs.search("decreto X")
        m = next(m for m in metas if m["source"] == "x.pdf")
        assert m["status"] == "obsolete"
        assert m["replaced_by"] == "D.123/2020"
        assert m["validity_end"] == "2020-01-01"


###############################################################################
# 3.  GovernanceLog (append-only — NIS2)
###############################################################################

class TestGovernanceLog:
    def _log(self, tmp_path):
        from src.nextpulse.governance_log import GovernanceLog
        return GovernanceLog(db_path=tmp_path / "gov.db")

    def test_record_and_history(self, tmp_path):
        log = self._log(tmp_path)
        rid = log.record(source="d.pdf", old_status="active", new_status="obsolete",
                         reason="master_file", replaced_by="D.9/2021", actor="audit")
        assert rid >= 1
        hist = log.history("d.pdf")
        assert len(hist) == 1
        assert hist[0]["new_status"] == "obsolete"
        assert hist[0]["replaced_by"] == "D.9/2021"

    def test_stats_groups_by_status(self, tmp_path):
        log = self._log(tmp_path)
        log.record(source="a.pdf", new_status="obsolete")
        log.record(source="b.pdf", new_status="poisoned")
        log.record(source="a.pdf", new_status="active")  # restored
        s = log.stats()
        assert s["total"] == 3
        assert s["sources"] == 2
        assert s["by_status"] == {"obsolete": 1, "poisoned": 1, "active": 1}


###############################################################################
# 4.  RAGChain — deterministic "abrogato" gate (no LLM)
###############################################################################

class TestObsolescenceGate:
    @pytest.fixture
    def chain(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.nextpulse.config.OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr("src.nextpulse.config.SCORE_THRESHOLD", 0.0)
        monkeypatch.setattr("src.nextpulse.config.QDRANT_PATH", tmp_path / "qdrant_test")
        monkeypatch.setattr("src.nextpulse.config.COLLECTION_NAME", "obsolete_unit")
        from src.nextpulse.rag_chain import RAGChain
        return RAGChain()

    def test_obsolete_match_returns_deterministic_notice(self, chain):
        from src.nextpulse.rag_chain import OBSOLETE_MESSAGE

        chain.vector_store.add_documents(
            texts=["La tolleranza degli autovelox era del 5% secondo il vecchio decreto."],
            metadatas=[{"source": "vecchio.pdf", "status": "obsolete",
                        "decreto": "100", "data_decreto": "2010-01-01",
                        "replaced_by": "decreto 200/2020", "validity_end": "2020-01-01"}],
        )
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            result = chain.query("qual è la tolleranza degli autovelox?")
        assert result["obsolete"] is True
        assert result["grounded"] is False
        assert result["confidence"] == "red"
        assert OBSOLETE_MESSAGE in result["response"]
        assert "decreto 200/2020" in result["response"]   # built from metadata, no LLM
        assert "vecchio.pdf" in result["sources"]
        mock_create.assert_not_called()                   # deterministic: zero LLM calls

    def test_poisoned_match_stays_hidden_generic_refusal(self, chain):
        from src.nextpulse.rag_chain import NO_CONTEXT_MESSAGE

        chain.vector_store.add_documents(
            texts=["Dato avvelenato iniettato nella knowledge base."],
            metadatas=[{"source": "fake.pdf", "status": "poisoned"}],
        )
        with patch.object(chain.client.chat.completions, "create") as mock_create:
            result = chain.query("dato avvelenato")
        assert result["obsolete"] is False
        assert result["grounded"] is False
        assert result["response"] == NO_CONTEXT_MESSAGE   # poisoned never surfaced
        mock_create.assert_not_called()

    def test_active_doc_unaffected_by_gate(self, chain):
        from unittest.mock import MagicMock
        answer = MagicMock()
        answer.choices = [MagicMock(message=MagicMock(content="Risposta su documento vigente"))]
        chain.vector_store.add_documents(
            texts=["La tolleranza degli autovelox è del 5%."],
            metadatas=[{"source": "vigente.pdf"}],   # defaults to active
        )
        with patch.object(chain.client.chat.completions, "create", return_value=answer):
            result = chain.query("qual è la tolleranza degli autovelox?")
        assert result["obsolete"] is False
        assert result["grounded"] is True


###############################################################################
# 5.  Audit job (hybrid) + scripts smoke
###############################################################################

class TestAuditJob:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.nextpulse.config.QDRANT_PATH", tmp_path / "qdrant_test")
        monkeypatch.setattr("src.nextpulse.config.COLLECTION_NAME", "audit_test")

    def test_load_master_file_csv(self, tmp_path):
        from scripts.audit_obsolescence import load_master_file

        csv_path = tmp_path / "obsolescence.csv"
        csv_path.write_text(
            "source,status,replaced_by,validity_end,reason\n"
            "old.pdf,obsolete,D.200/2020,2020-01-01,abrogato\n",
            encoding="utf-8",
        )
        rules = load_master_file(csv_path)
        assert rules["old.pdf"]["status"] == "obsolete"
        assert rules["old.pdf"]["replaced_by"] == "D.200/2020"

    def test_load_master_file_missing_returns_empty(self, tmp_path):
        from scripts.audit_obsolescence import load_master_file
        assert load_master_file(tmp_path / "nope.csv") == {}

    def test_run_audit_flips_status_and_logs(self, tmp_path):
        from scripts.audit_obsolescence import run_audit
        from src.nextpulse.governance_log import GovernanceLog
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(
            texts=["Decreto vecchio.", "Scheda prodotto vigente."],
            metadatas=[{"source": "old.pdf"}, {"source": "keep.pdf"}],
        )
        gov = GovernanceLog(db_path=tmp_path / "gov.db")
        master = {"old.pdf": {"status": "obsolete", "replaced_by": "D.9/2021",
                              "validity_end": "2021-01-01", "reason": "master_file"}}

        updated, skipped, errors = run_audit(vs, gov, master)
        assert (updated, errors) == (1, 0)
        assert vs.source_statuses() == {"old.pdf": "obsolete", "keep.pdf": "active"}
        assert gov.history("old.pdf")[0]["new_status"] == "obsolete"

        # idempotent: a second run changes nothing
        assert run_audit(vs, gov, master)[0] == 0

    def test_run_audit_dry_run_writes_nothing(self, tmp_path):
        from scripts.audit_obsolescence import run_audit
        from src.nextpulse.governance_log import GovernanceLog
        from src.nextpulse.vector_store import VectorStore

        vs = VectorStore()
        vs.add_documents(texts=["Decreto vecchio."], metadatas=[{"source": "old.pdf"}])
        gov = GovernanceLog(db_path=tmp_path / "gov.db")
        master = {"old.pdf": {"status": "obsolete"}}

        updated, _, _ = run_audit(vs, gov, master, dry_run=True)
        assert updated == 1
        assert vs.source_statuses()["old.pdf"] == "active"   # not actually changed
        assert gov.stats()["total"] == 0


class TestGovernanceScriptsImport:
    def test_audit_script_imports(self):
        import scripts.audit_obsolescence  # noqa: F401

    def test_quarantine_script_imports(self):
        import scripts.quarantine_source  # noqa: F401
