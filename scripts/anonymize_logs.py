#!/usr/bin/env python
"""GDPR data-anonymization job — run nightly (cron / systemd timer).

Instead of DELETING historical query logs, this UPDATEs every row older than the
retention window (default: 6 months), setting `user_id = NULL` and
`session_id = NULL`. The residual rows stay available for statistics
(e.g. "most-asked topics by the Sales profile in 2024") but are no longer linked
to an individual, so they leave the GDPR perimeter. Rows are NEVER deleted.

Usage:
    python scripts/anonymize_logs.py                 # use config retention (6 months)
    python scripts/anonymize_logs.py --months 6      # explicit window
    python scripts/anonymize_logs.py --dry-run       # report only, no writes

Cron (every night at 02:30):
    30 2 * * *  cd /path/to/NextPulse && uv run python scripts/anonymize_logs.py >> anonymize.log 2>&1
"""
import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' imports standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import config  # noqa: E402
from src.nextpulse.query_log import QueryLog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GDPR anonymization of old query logs (user_id/session_id → NULL)"
    )
    parser.add_argument(
        "--months", type=int, default=config.LOG_RETENTION_MONTHS,
        help=f"retention window in months (default: {config.LOG_RETENTION_MONTHS})",
    )
    parser.add_argument(
        "--db", type=Path, default=config.QUERY_LOG_PATH,
        help="path to the query-log SQLite database",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report how many rows would be anonymized, without writing",
    )
    args = parser.parse_args()

    log = QueryLog(db_path=args.db)

    if args.dry_run:
        n = log.count_anonymizable(args.months)
        print(f"[dry-run] {n} riga/righe più vecchie di {args.months} mesi "
              f"verrebbero anonimizzate (user_id/session_id → NULL). Nessuna scrittura.")
        return

    changed = log.anonymize_older_than(args.months)
    s = log.stats()
    print(f"✅ Anonimizzate {changed} righe più vecchie di {args.months} mesi "
          f"(user_id/session_id → NULL · righe NON cancellate).")
    print(f"   Log totali: {s['total']} · ancora identificabili: {s['identified']} · "
          f"anonimizzate: {s['anonymized']}")


if __name__ == "__main__":
    main()
