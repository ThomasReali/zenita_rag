"""R.A.M. bandi/gare → Qdrant ingestion CLI (mirrors ingest_mit.py).

Scrapes the R.A.M. Logistica Infrastrutture e Trasporti SpA procurement portal
(bandi in corso + in aggiudicazione), chunks the requirement-bearing documents and
indexes them into the dedicated bandi Qdrant collection — the same pipeline the UI
drives via /api/bandi/scrape, runnable headless for ops / scheduled re-indexing.

Usage:
    python scripts/ingest_ram.py            # full run, prints live progress
    python scripts/ingest_ram.py --quiet    # only the final summary

NOTE: the embedded Qdrant locks its storage folder per process — stop the API server
(or point QDRANT_URL at a Qdrant server) before running this against the same data dir.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse.ram_scraper import CATEGORY_LABELS, RamScraper

logger = logging.getLogger("ram_ingestion")


def _progress(event: dict) -> None:
    phase = event.get("phase")
    if phase == "listing":
        logger.info("Trovati %d bandi — inizio download e indicizzazione…", event["total"])
    elif phase == "tender":
        t = event["tender"]
        logger.info(
            "[%2d/%d] %-14s chunks=%3d requisiti=%2d  %s",
            event["index"], event["total"], t["category"],
            event["chunks"], len(event["requirements"]), t["title"][:60],
        )
    elif phase == "done":
        by_cat = ", ".join(
            f"{CATEGORY_LABELS.get(k, k)}: {v}" for k, v in event["by_category"].items()
        )
        logger.info("Completato — %d bandi, %d chunk (%s)",
                    event["total"], event["chunks"], by_cat)
    elif phase == "error":
        logger.error("Scraping fallito: %s", event["message"])


def main() -> None:
    p = argparse.ArgumentParser(description="Scrape + index R.A.M. bandi into Qdrant.")
    p.add_argument("--quiet", action="store_true", help="Only log the final summary.")
    args = p.parse_args()

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.WARNING if args.quiet else logging.INFO,
    )

    scraper = RamScraper()
    results = scraper.ingest(progress=None if args.quiet else _progress)

    total_chunks = sum(t.get("chunks", 0) for t in results)
    print(f"\nDone. {len(results)} bandi indicizzati, {total_chunks} chunk totali.")


if __name__ == "__main__":
    main()
