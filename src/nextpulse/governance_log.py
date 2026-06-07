"""Append-only governance audit log (NIS2 integrity & traceability).

Every DETERMINISTIC status change to a knowledge-base source — active → obsolete
(abrogation), → poisoned (data-poisoning quarantine), → deleted (physical erasure)
— is recorded as one immutable row: what changed, when, from which source of truth,
and the resulting replaced_by / validity_end.

Unlike the GDPR query log (`query_log.py`), rows here are NEVER anonymized or
deleted: they ARE the integrity trail of the knowledge base. The log answers the
NIS2 question "who/what changed the corpus, when, and on what basis?" without ever
involving the LLM. Writing is best-effort: a logging failure must never abort the
underlying Qdrant operation.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.nextpulse import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS governance_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at    TEXT NOT NULL,        -- ISO-8601 UTC
    source        TEXT NOT NULL,        -- document source (file name)
    old_status    TEXT,                 -- best-effort previous status
    new_status    TEXT NOT NULL,        -- active | obsolete | poisoned | draft | deleted
    reason        TEXT,                 -- source of truth / note (master_file, normattiva, quarantine…)
    replaced_by   TEXT,                 -- abrogating provvedimento, when known
    validity_end  TEXT,                 -- end of validity, when known
    actor         TEXT                  -- who applied it (job name / operator)
);
CREATE INDEX IF NOT EXISTS idx_gov_log_source ON governance_log(source);
CREATE INDEX IF NOT EXISTS idx_gov_log_changed ON governance_log(changed_at);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GovernanceLog:
    """Append-only, never-anonymized audit trail of knowledge-base status changes."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or config.GOVERNANCE_LOG_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── write ────────────────────────────────────────────────────────────────
    def record(
        self,
        *,
        source: str,
        new_status: str,
        old_status: Optional[str] = None,
        reason: Optional[str] = None,
        replaced_by: Optional[str] = None,
        validity_end: Optional[str] = None,
        actor: Optional[str] = None,
        changed_at: Optional[datetime] = None,
    ) -> int:
        """Append one status-change event; returns the row id."""
        ts = (changed_at or _utcnow()).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO governance_log
                       (changed_at, source, old_status, new_status, reason,
                        replaced_by, validity_end, actor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, source, old_status, new_status, reason,
                 replaced_by, validity_end, actor),
            )
            return int(cur.lastrowid)

    # ── read ─────────────────────────────────────────────────────────────────
    def history(self, source: Optional[str] = None, *, limit: int = 200) -> List[dict]:
        """Recent status changes (optionally for one source), newest first."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if source is None:
                rows = conn.execute(
                    "SELECT * FROM governance_log ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM governance_log WHERE source = ? ORDER BY id DESC LIMIT ?",
                    (source, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Counts for a governance dashboard."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM governance_log").fetchone()[0]
            by_status = dict(
                conn.execute(
                    "SELECT new_status, COUNT(*) FROM governance_log GROUP BY new_status"
                ).fetchall()
            )
            sources = conn.execute(
                "SELECT COUNT(DISTINCT source) FROM governance_log"
            ).fetchone()[0]
            last = conn.execute("SELECT MAX(changed_at) FROM governance_log").fetchone()[0]
        return {"total": total, "by_status": by_status, "sources": sources, "last": last}
