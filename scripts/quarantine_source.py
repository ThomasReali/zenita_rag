#!/usr/bin/env python
"""Quarantine (or erase) a poisoned knowledge-base source — deterministic, by source.

Data-poisoning response: if a wrong/malicious document was indexed, the operator makes
it disappear from retrieval IMMEDIATELY and deterministically (by source id, not by AI).

  Soft (default): status → "poisoned". Instantly invisible to retrieval, REVERSIBLE
                  (re-run with --status active to restore).
  Hard (--delete): physically removes all chunks. Use for GDPR erasure when the source
                  contains personal data — a status flag is NOT erasure (Art. 17).

Both append an immutable row to the governance log (NIS2 traceability).

Examples:
  python scripts/quarantine_source.py "decreto_falso.pdf"
  python scripts/quarantine_source.py "scheda.pdf" --status active        # un-quarantine
  python scripts/quarantine_source.py "dossier_con_pii.pdf" --delete --reason gdpr_erasure
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse.governance_log import GovernanceLog  # noqa: E402
from src.nextpulse.vector_store import VectorStore  # noqa: E402

_ACTOR = "quarantine_cli"


def main() -> None:
    ap = argparse.ArgumentParser(description="Quarantine/erase a poisoned KB source.")
    ap.add_argument("source", help="document source (file name) to act on")
    ap.add_argument("--delete", action="store_true",
                    help="physically delete all chunks (GDPR erasure) instead of flagging")
    ap.add_argument("--status", default="poisoned",
                    help="status to set when not deleting (default: poisoned; use 'active' to restore)")
    ap.add_argument("--reason", default="manual_quarantine", help="audit reason / note")
    args = ap.parse_args()

    vs = VectorStore()
    gov = GovernanceLog()

    old = vs.source_statuses().get(args.source)
    if old is None:
        print(f"⚠️  Source non trovata nella collection: {args.source}")
        sys.exit(1)

    if args.delete:
        vs.delete_by_source(args.source)
        gov.record(source=args.source, old_status=old, new_status="deleted",
                   reason=args.reason, actor=_ACTOR)
        print(f"🗑️  Eliminata fisicamente (era '{old}'): {args.source}")
    else:
        vs.set_status_by_source(args.source, args.status)
        gov.record(source=args.source, old_status=old, new_status=args.status,
                   reason=args.reason, actor=_ACTOR)
        print(f"🚫 Stato '{old}' → '{args.status}': {args.source}")


if __name__ == "__main__":
    main()
