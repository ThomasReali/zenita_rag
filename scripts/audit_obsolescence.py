#!/usr/bin/env python
"""Deterministic obsolescence audit (NO LLM) — hybrid source of truth.

Why deterministic: the reliability the Public Administration sale requires cannot be
delegated to a semantic/AI judgement. Whether a decree is in force is decided by the
DATABASE and METADATA, not by the model. This job confronts the indexed corpus with
authoritative sources and flips the chunk `status` in Qdrant accordingly.

Sources (hybrid):
  1. PRIMARY — internal master file (config.GOVERNANCE_MASTER_FILE): a CSV or JSON
     mapping `source` (file name) → status / replaced_by / validity_end / reason.
  2. SECONDARY — Normattiva enrichment (config.NORMATTIVA_AUDIT_ENABLED, Fase 2):
     pluggable + best-effort. Normattiva has no stable public REST API, so the check
     is isolated and degrades gracefully: if unreachable it logs and falls back to the
     master file. It NEVER fails the job.

For every changed source: `VectorStore.set_status_by_source(...)` (set_payload, no
re-embedding) + an immutable row in the governance log (NIS2 traceability). The job is
idempotent (a source already at the target status is skipped) and best-effort per source.

Schedule nightly, like scripts/anonymize_logs.py. Use --dry-run to preview.

Master file (CSV) columns (header required): source,status,replaced_by,validity_end,reason
Master file (JSON): {"<source>": {"status": "...", "replaced_by": "...", ...}, ...}
"""
import argparse
import csv
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' import works standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import config  # noqa: E402
from src.nextpulse.governance_log import GovernanceLog  # noqa: E402
from src.nextpulse.vector_store import VectorStore  # noqa: E402

_ACTOR = "audit_obsolescence"
_VALID_STATUSES = {"active", "obsolete", "poisoned", "draft"}


def load_master_file(path: Path) -> dict:
    """Parse the master file into {source: {status, replaced_by, validity_end, reason}}.

    Tolerant: unknown columns ignored, missing file → {} (job still runs Normattiva if on).
    """
    if not path.exists():
        print(f"⚠️  Master file non trovato: {path} (salto la fonte primaria)")
        return {}
    suffix = path.suffix.lower()
    rules: dict = {}
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        for source, rule in (data or {}).items():
            rules[source] = {k: rule.get(k) for k in
                             ("status", "replaced_by", "validity_end", "reason")}
    else:  # CSV (default)
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                source = (row.get("source") or "").strip()
                if not source or source.startswith("#"):  # skip blanks / comment lines
                    continue
                rules[source] = {
                    "status": (row.get("status") or "").strip() or None,
                    "replaced_by": (row.get("replaced_by") or "").strip() or None,
                    "validity_end": (row.get("validity_end") or "").strip() or None,
                    "reason": (row.get("reason") or "").strip() or "master_file",
                }
    return rules


def normattiva_rule(source: str, current_status: str) -> dict | None:
    """Best-effort Normattiva enrichment (Fase 2). Returns a rule dict or None.

    Stub by design: Normattiva exposes no stable REST API, so a real implementation
    would scrape/parse and is fragile. Kept behind NORMATTIVA_AUDIT_ENABLED and wrapped
    by the caller in try/except so it can NEVER crash the deterministic job.
    """
    # TODO (Fase 2): query Normattiva for `source`'s decree and detect abrogation.
    return None


def run_audit(vs, gov, master: dict, *, dry_run: bool = False,
              normattiva_enabled: bool = False) -> tuple:
    """Core audit loop (no I/O setup) — confront the corpus with `master` and apply
    status changes. Returns (updated, skipped, errors). Kept separate from main() so it
    can be tested with a SHARED VectorStore (embedded Qdrant locks the folder per process)."""
    statuses = vs.source_statuses()  # source → current status (also used for idempotency)
    print(f"\n🔎 sorgenti in collection: {len(statuses)} | regole master: {len(master)} | "
          f"normattiva: {'ON' if normattiva_enabled else 'off'}")

    updated, skipped, errors = 0, 0, 0
    for source, current in sorted(statuses.items()):
        rule = master.get(source)

        # Secondary (best-effort): Normattiva may provide/override a rule. Never fatal.
        if normattiva_enabled:
            try:
                enriched = normattiva_rule(source, current)
                if enriched:
                    rule = {**(rule or {}), **{k: v for k, v in enriched.items() if v}}
            except Exception as exc:  # noqa: BLE001 — robustness over purity
                print(f"  ⚠️  normattiva fallita su {source[:40]}: {type(exc).__name__}")

        if not rule:
            continue
        new_status = rule.get("status")
        if new_status not in _VALID_STATUSES:
            print(f"  ⚠️  stato non valido '{new_status}' per {source[:40]} — ignorato")
            skipped += 1
            continue
        if new_status == current:
            skipped += 1  # idempotent: already at target status
            continue

        if dry_run:
            print(f"  [dry-run] {source[:44]} : {current} → {new_status}")
            updated += 1
            continue

        try:
            vs.set_status_by_source(
                source, new_status,
                replaced_by=rule.get("replaced_by"),
                validity_end=rule.get("validity_end"),
            )
            gov.record(
                source=source, old_status=current, new_status=new_status,
                reason=rule.get("reason") or "master_file",
                replaced_by=rule.get("replaced_by"), validity_end=rule.get("validity_end"),
                actor=_ACTOR,
            )
            updated += 1
            print(f"  ✅ {source[:44]} : {current} → {new_status}")
        except Exception as exc:  # noqa: BLE001 — one bad source must not abort the batch
            errors += 1
            print(f"  ❌ {source[:44]} :: {type(exc).__name__}: {exc}")
    return updated, skipped, errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic obsolescence audit (no LLM).")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing to Qdrant / the log")
    args = ap.parse_args()

    print("=" * 60)
    print("🛡️  Audit obsolescenza (deterministico, no-LLM)")
    print("=" * 60)

    vs = VectorStore()
    gov = GovernanceLog()
    master = load_master_file(config.GOVERNANCE_MASTER_FILE)

    updated, skipped, errors = run_audit(
        vs, gov, master, dry_run=args.dry_run,
        normattiva_enabled=config.NORMATTIVA_AUDIT_ENABLED,
    )

    verb = "da aggiornare" if args.dry_run else "aggiornati"
    print(f"\n✅ Fatto! {verb}: {updated} | invariati: {skipped} | errori: {errors}")


if __name__ == "__main__":
    main()
