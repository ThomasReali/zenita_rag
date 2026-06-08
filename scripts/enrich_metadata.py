#!/usr/bin/env python
"""Enrich indexed MIT decrees with official metadata from the download manifest.

Deterministic, offline, idempotent — NO re-embedding. The manifest
(`manifest_download_mit.json`) maps each decree file to its official title, date, number and
the official mit.gov.it URLs (decree page + PDF). This job joins it onto the chunks already in
Qdrant by `source` (basename of the manifest `output_file`) and writes the fields via
`set_payload`, so every citation can carry a verifiable link to the source (RF11/RF18).

Usage:
    python scripts/enrich_metadata.py --dry-run     # report matches, write nothing
    python scripts/enrich_metadata.py               # apply the enrichment
    python scripts/enrich_metadata.py --manifest <path>
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' imports standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import config  # noqa: E402
from src.nextpulse.vector_store import VectorStore  # noqa: E402


def _iso_to_date(s: str) -> str:
    """'20251219' → '2025-12-19' (best-effort; returns input unchanged if not 8 digits)."""
    s = (s or "").strip()
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _load_manifest(path: Path) -> list:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows


def _build_index(entries: list) -> dict:
    """Map basename(output_file) → enrichment payload (official title/date/number + URLs)."""
    idx = {}
    for e in entries:
        out = e.get("output_file") or ""
        if not out:
            continue
        key = os.path.basename(out)
        payload = {}
        if e.get("detail_title"):
            payload["official_title"] = e["detail_title"]
        if e.get("detail_url"):
            payload["source_url"] = e["detail_url"]
        if e.get("attachment_url"):
            payload["pdf_url"] = e["attachment_url"]
        if e.get("decree_number"):
            payload["decreto"] = str(e["decree_number"])
        if e.get("detail_date_iso"):
            payload["data_decreto"] = _iso_to_date(str(e["detail_date_iso"]))
        if payload:
            idx[key] = payload
    return idx


def main() -> None:
    parser = argparse.ArgumentParser(description="NextPulse — enrichment metadati MIT (offline)")
    parser.add_argument("--manifest", type=Path, default=config.MIT_MANIFEST_FILE)
    parser.add_argument("--dry-run", action="store_true", help="mostra i match, non scrive")
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"⚠️  Manifest non trovato: {args.manifest}")
        return

    index = _build_index(_load_manifest(args.manifest))
    print(f"📜 Manifest: {len(index)} voci con metadati ufficiali")

    vs = VectorStore()
    sources = list(vs.source_statuses().keys())  # distinct sources currently in Qdrant
    matched = [s for s in sources if s in index]
    print(f"🔎 Sorgenti in Qdrant: {len(sources)} | con corrispondenza nel manifest: {len(matched)}")

    if args.dry_run:
        for s in matched[:10]:
            print(f"   • {s[:60]} → {index[s].get('source_url', '')}")
        print(f"\n(dry-run) nessuna scrittura. Applicabili: {len(matched)} sorgenti.")
        return

    enriched = 0
    for s in matched:
        try:
            vs.set_payload_by_source(s, index[s])
            enriched += 1
        except Exception as e:  # robustness: one failure must not abort the batch
            print(f"   ❌ {s[:50]} :: {type(e).__name__}: {e}")
    print(f"\n✅ Fatto! sorgenti arricchite: {enriched}/{len(matched)} "
          f"(titolo ufficiale + URL mit.gov.it, nessun re-embedding)")


if __name__ == "__main__":
    main()
