"""MIT normativa → Qdrant ingestion (Approach C: hybrid scraper + RAG pipeline).

Architecture:
  1. Scraper HTTP utilities download HTML pages and PDFs from MIT normativa.
  2. Instead of the scraper's character-based chunker, raw bytes are passed to
     DocumentProcessor → token-based structural chunking (≤480 tokens, cuts at
     Art./Articolo boundaries) with per-page metadata.
  3. Metadata is mapped from scraper ListingEntry → RAG schema
     (source, category, decreto, data_decreto, source_url, title).
  4. New decrees are inserted; updated decrees are first deleted from Qdrant
     (via delete_by_source) then re-inserted for a clean re-index.
  5. Change detection uses the scraper's SQLite (content_hash per decree).

Usage:
    python scripts/ingest_mit.py                         # full incremental run
    python scripts/ingest_mit.py --dry-run               # no writes, log only
    python scripts/ingest_mit.py --db-path data/mit.db  # custom DB path
    python scripts/ingest_mit.py --debug                 # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import List, Tuple

# ── path bootstrap ─────────────────────────────────────────────────────────────
# _PROJECT_ROOT: the feature-branch root (contains src/, scripts/, data/ …)
# _WORKSPACE_ROOT: parent, contains ScraipingListingPagina/ as a sibling
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_ROOT = _PROJECT_ROOT.parent

for _p in [str(_PROJECT_ROOT), str(_WORKSPACE_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests
from bs4 import BeautifulSoup

from ScraipingListingPagina.scraper import (
    HEADERS,
    STATUS_NEW,
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    DocumentRecord,
    ListingEntry,
    _sleep_level2,
    extract_text_from_html,
    fetch_raw,
    find_pdf_links,
    get_document,
    init_db,
    iter_listing_pages,
    sha256_hex,
    upsert_document,
)
from src.nextpulse.document_processor import DocumentProcessor
from src.nextpulse.vector_store import VectorStore

logger = logging.getLogger("mit_ingestion")

DEFAULT_SCRAPER_DB = _PROJECT_ROOT / "data" / "mit_scraper.db"


# ── metadata helpers ───────────────────────────────────────────────────────────

def _source_id(protocol_id: str, content_type: str) -> str:
    """Stable filename key used for delete_by_source and query deduplication."""
    ext = "pdf" if content_type == "pdf" else "txt"
    return f"MIT_decreto_{protocol_id}.{ext}"


def _build_base_meta(entry: ListingEntry, content_type: str) -> dict:
    """Map scraper ListingEntry → RAG metadata schema (minus chunk_id / page)."""
    protocol_id = entry["protocol_id"]
    # protocol_id format: "{number}-{YYYY-MM-DD}"  (or "unknown-{hash}" for fallbacks)
    parts = protocol_id.split("-", 1)
    return {
        "source": _source_id(protocol_id, content_type),
        "doc_type": content_type,
        "category": "normativa",
        "decreto": parts[0],
        "data_decreto": parts[1] if len(parts) > 1 else entry.get("doc_date", ""),
        "title": entry.get("title", ""),
        "source_url": entry.get("page_url", ""),
    }


# ── per-entry processing ───────────────────────────────────────────────────────

def _process_entry(
    entry: ListingEntry,
    processor: DocumentProcessor,
    vs: VectorStore,
    conn: sqlite3.Connection,
    session: requests.Session,
    dry_run: bool,
) -> dict:
    """Download, rechunk, and upsert one decree.  Returns {status, chunks}."""
    protocol_id = entry["protocol_id"]
    page_url = entry["page_url"]
    today = str(date.today())

    logger.info("Processing: %s — %s", protocol_id, entry["title"][:60])

    # Level-2 request: download the decree HTML page
    _sleep_level2()
    html_raw = fetch_raw(page_url, session)
    if html_raw is None:
        logger.warning("Page download failed for %s — skipping", protocol_id)
        return {"status": "failed", "chunks": 0}

    page_hash = sha256_hex(html_raw)
    existing = get_document(conn, protocol_id)

    # ── change detection ───────────────────────────────────────────────────────
    if existing is not None and existing["content_hash"] == page_hash:
        record = DocumentRecord(
            protocol_id=protocol_id,
            page_url=page_url,
            pdf_url=existing.get("pdf_url", ""),
            title=entry["title"],
            summary=entry.get("summary", ""),
            content_hash=page_hash,
            content_type=existing.get("content_type", ""),
            last_seen=today,
            status=STATUS_UNCHANGED,
        )
        if not dry_run:
            upsert_document(conn, record)
        logger.debug("UNCHANGED: %s", protocol_id)
        return {"status": STATUS_UNCHANGED, "chunks": 0}

    status = STATUS_NEW if existing is None else STATUS_UPDATED

    # ── PDF discovery and download ─────────────────────────────────────────────
    soup = BeautifulSoup(html_raw, "html.parser")
    pdf_urls = find_pdf_links(soup)

    content_type = "html"
    content_hash = page_hash
    pdf_url_used = ""
    chunks: List[Tuple[str, dict]] = []

    if pdf_urls:
        pdf_url_used = pdf_urls[0]
        logger.info("Found PDF for %s — downloading %s", protocol_id, pdf_url_used)
        _sleep_level2()
        pdf_raw = fetch_raw(pdf_url_used, session)

        if pdf_raw:
            content_hash = sha256_hex(pdf_raw)
            content_type = "pdf"
            base_meta = _build_base_meta(entry, "pdf")
            try:
                chunks = processor.process_pdf_bytes(pdf_raw, base_meta)
            except Exception as exc:
                logger.warning(
                    "PDF chunking failed for %s (%s) — falling back to HTML",
                    protocol_id, exc,
                )
                chunks = []

        if not chunks:
            # PDF download failed or yielded no text → HTML fallback
            logger.info("Falling back to HTML text for %s", protocol_id)
            text = extract_text_from_html(html_raw)
            content_type = "html"
            content_hash = page_hash
            pdf_url_used = ""
            if text:
                chunks = processor.process_text_content(text, _build_base_meta(entry, "txt"))
    else:
        logger.info("No PDF found for %s — extracting HTML text", protocol_id)
        text = extract_text_from_html(html_raw)
        if text:
            chunks = processor.process_text_content(text, _build_base_meta(entry, "txt"))

    if not chunks:
        logger.warning("No extractable content for %s — skipping", protocol_id)
        return {"status": "failed", "chunks": 0}

    source_key = chunks[0][1]["source"]   # same across all chunks of this decree

    # ── Qdrant upsert ──────────────────────────────────────────────────────────
    if not dry_run:
        if status == STATUS_UPDATED:
            logger.info("Deleting stale Qdrant chunks for %s", source_key)
            vs.delete_by_source(source_key)

        vs.add_documents(
            texts=[t for t, _ in chunks],
            metadatas=[m for _, m in chunks],
        )
        logger.info(
            "Indexed %d chunks for %s (status: %s)", len(chunks), protocol_id, status
        )

        upsert_document(
            conn,
            DocumentRecord(
                protocol_id=protocol_id,
                page_url=page_url,
                pdf_url=pdf_url_used,
                title=entry["title"],
                summary=entry.get("summary", ""),
                content_hash=content_hash,
                content_type=content_type,
                last_seen=today,
                status=status,
            ),
        )
    else:
        logger.info(
            "[DRY-RUN] Would index %d chunks for %s (status: %s)",
            len(chunks), protocol_id, status,
        )

    return {"status": status, "chunks": len(chunks)}


# ── main loop ──────────────────────────────────────────────────────────────────

def run_ingestion(
    db_path: Path = DEFAULT_SCRAPER_DB,
    dry_run: bool = False,
) -> dict:
    """Full incremental ingestion run. Returns summary stats."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    init_db(conn)

    processor = DocumentProcessor()
    vs = VectorStore()
    session = requests.Session()
    session.headers.update(HEADERS)

    stats: dict = {STATUS_NEW: 0, STATUS_UPDATED: 0, STATUS_UNCHANGED: 0, "failed": 0, "total_chunks": 0}

    try:
        for page_entries in iter_listing_pages(session):
            for entry in page_entries:
                try:
                    result = _process_entry(entry, processor, vs, conn, session, dry_run)
                    stat_key = result["status"]
                    stats[stat_key if stat_key in stats else "failed"] += 1
                    stats["total_chunks"] += result["chunks"]
                except Exception as exc:
                    logger.error(
                        "Unexpected error for %s: %s",
                        entry.get("protocol_id", "?"), exc,
                        exc_info=True,
                    )
                    stats["failed"] += 1
    finally:
        conn.close()
        session.close()

    return stats


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="MIT normativa → Qdrant ingestion (Approach C).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Log actions without writing to DB or Qdrant.")
    p.add_argument("--db-path", type=Path, default=DEFAULT_SCRAPER_DB,
                   help="Scraper SQLite database path.")
    p.add_argument("--debug", action="store_true",
                   help="Enable DEBUG-level logging.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    if args.dry_run:
        logger.info("=== DRY-RUN MODE — no DB or Qdrant writes ===")
    logger.info("Scraper DB: %s", args.db_path.resolve())

    stats = run_ingestion(db_path=args.db_path, dry_run=args.dry_run)

    logger.info(
        "Done. new=%d updated=%d unchanged=%d failed=%d total_chunks=%d",
        stats[STATUS_NEW], stats[STATUS_UPDATED], stats[STATUS_UNCHANGED],
        stats["failed"], stats["total_chunks"],
    )
