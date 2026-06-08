"""Tests for MIT metadata enrichment (script logic + source_links surfacing)."""
import pytest

from scripts.enrich_metadata import _build_index, _iso_to_date
from src.nextpulse.rag_chain import RAGChain


# ── enrichment script logic ──────────────────────────────────────────────────

def test_iso_to_date():
    assert _iso_to_date("20251219") == "2025-12-19"
    assert _iso_to_date("") == ""
    assert _iso_to_date("not-a-date") == "not-a-date"


def test_build_index_maps_basename_to_official_fields():
    entries = [
        {
            "output_file": "C:/dl/20251219-Decreto n. 589 del 19-12-2025 - X.pdf",
            "detail_title": "Decreto Direttoriale n. 589 del 19/12/2025",
            "detail_url": "https://www.mit.gov.it/normativa/decreto-589",
            "attachment_url": "https://www.mit.gov.it/files/589.pdf",
            "decree_number": "589",
            "detail_date_iso": "20251219",
        },
        {"output_file": "", "detail_title": "ignored (no output_file)"},
    ]
    idx = _build_index(entries)
    key = "20251219-Decreto n. 589 del 19-12-2025 - X.pdf"
    assert key in idx
    assert idx[key]["official_title"].startswith("Decreto Direttoriale n. 589")
    assert idx[key]["source_url"] == "https://www.mit.gov.it/normativa/decreto-589"
    assert idx[key]["pdf_url"].endswith("589.pdf")
    assert idx[key]["decreto"] == "589"
    assert idx[key]["data_decreto"] == "2025-12-19"
    assert len(idx) == 1  # the entry without output_file is skipped


# ── source_links surfacing (rag_chain) ───────────────────────────────────────

def test_format_source_links_aligned_with_sources_order():
    # Two distinct sources; first-appearance order is a.pdf then b.pdf. Only a.pdf has a URL.
    metas = [
        {"source": "a.pdf", "source_url": "https://mit.gov.it/a"},
        {"source": "b.pdf"},
        {"source": "a.pdf", "source_url": "https://mit.gov.it/a"},
    ]
    sources = RAGChain._format_sources(metas)
    links = RAGChain._format_source_links(metas)
    assert len(links) == len(sources)              # aligned 1:1 with the legend
    assert links[0] == "https://mit.gov.it/a"      # a.pdf first → its URL
    assert links[1] is None                         # b.pdf has no URL

def test_format_source_links_empty():
    assert RAGChain._format_source_links([]) == []


# ── set_payload_by_source (integration) ──────────────────────────────────────

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr("src.nextpulse.vector_store.config.QDRANT_PATH", tmp_path / "qd")
    monkeypatch.setattr("src.nextpulse.vector_store.config.COLLECTION_NAME", "enrich_test")
    from src.nextpulse.vector_store import VectorStore
    vs = VectorStore()
    vs.add_documents(texts=["Decreto di prova."], metadatas=[{"source": "decreto.pdf"}])
    return vs


def test_set_payload_by_source_adds_fields(store):
    store.set_payload_by_source("decreto.pdf", {
        "source_url": "https://www.mit.gov.it/normativa/decreto-x",
        "official_title": "Decreto X",
    })
    _docs, metas, _scores, _top = store.search("prova", k=1)
    assert metas[0]["source_url"] == "https://www.mit.gov.it/normativa/decreto-x"
    assert metas[0]["official_title"] == "Decreto X"
    assert metas[0]["source"] == "decreto.pdf"     # existing field preserved
