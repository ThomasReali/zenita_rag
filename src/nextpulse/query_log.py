"""GDPR-aware query log (SQLite, stdlib only).

One row is recorded per RAG query for analytics / audit (e.g. "which topics did
the Sales profile ask most in 2024?"). The identifier columns `user_id` and
`session_id` are PERSONAL DATA.

Data Anonymization (GDPR): instead of deleting historical rows, a nightly job
(`scripts/anonymize_logs.py`) calls `anonymize_older_than()`, which runs an
UPDATE on every row older than the retention window (default 6 months) setting
`user_id = NULL` and `session_id = NULL`. The residual data stays available for
statistics but is no longer linked to an individual, so it exits the GDPR
perimeter. Rows are NEVER deleted.
"""
from __future__ import annotations

import calendar
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.nextpulse import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT    NOT NULL,        -- ISO-8601 UTC
    user_id          TEXT,                    -- PII · NULLed on anonymization
    session_id       TEXT,                    -- PII · NULLed on anonymization
    role             TEXT,
    question         TEXT,
    standalone_query TEXT,
    confidence       TEXT,
    grounded         INTEGER,
    ambiguous        INTEGER,
    top_score        REAL,
    n_sources        INTEGER,
    model            TEXT,
    anonymized_at    TEXT                     -- set when identifiers cleared
);
CREATE INDEX IF NOT EXISTS idx_query_log_created ON query_log(created_at);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _months_before(dt: datetime, months: int) -> datetime:
    """Return `dt` minus `months` calendar months (day clamped to month length)."""
    total = (dt.year * 12 + dt.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


class QueryLog:
    """Append-only query log with a GDPR anonymization step."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or config.QUERY_LOG_PATH)
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
        question: Optional[str],
        role: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        standalone_query: Optional[str] = None,
        confidence: Optional[str] = None,
        grounded: Optional[bool] = None,
        ambiguous: Optional[bool] = None,
        top_score: Optional[float] = None,
        n_sources: Optional[int] = None,
        model: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        """Insert one query event; returns the row id."""
        ts = (created_at or _utcnow()).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO query_log
                       (created_at, user_id, session_id, role, question,
                        standalone_query, confidence, grounded, ambiguous,
                        top_score, n_sources, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts, user_id, session_id, role, question, standalone_query,
                    confidence,
                    None if grounded is None else int(bool(grounded)),
                    None if ambiguous is None else int(bool(ambiguous)),
                    top_score, n_sources, model,
                ),
            )
            return int(cur.lastrowid)

    def record_result(
        self,
        result: dict,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        """Convenience: log straight from a `RAGChain.query()` result dict."""
        return self.record(
            question=result.get("query"),
            role=result.get("role"),
            session_id=session_id,
            user_id=user_id,
            standalone_query=result.get("standalone_query"),
            confidence=result.get("confidence"),
            grounded=result.get("grounded"),
            ambiguous=result.get("ambiguous"),
            top_score=result.get("top_score"),
            n_sources=len(result.get("sources") or []),
            model=result.get("model"),
            created_at=created_at,
        )

    # ── GDPR anonymization ─────────────────────────────────────────────────────
    def anonymize_older_than(
        self, months: Optional[int] = None, *, now: Optional[datetime] = None
    ) -> int:
        """NULL `user_id`/`session_id` on rows older than `months`. Returns rows changed.

        Rows are not deleted. Idempotent: rows already anonymized (both ids NULL)
        are skipped, so re-running on the same window changes 0 rows.
        """
        months = config.LOG_RETENTION_MONTHS if months is None else months
        ref = now or _utcnow()
        cutoff = _months_before(ref, months).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE query_log
                      SET user_id = NULL, session_id = NULL, anonymized_at = ?
                    WHERE created_at < ?
                      AND (user_id IS NOT NULL OR session_id IS NOT NULL)""",
                (ref.isoformat(), cutoff),
            )
            return cur.rowcount

    def count_anonymizable(
        self, months: Optional[int] = None, *, now: Optional[datetime] = None
    ) -> int:
        """How many rows the anonymization job would touch (for --dry-run)."""
        months = config.LOG_RETENTION_MONTHS if months is None else months
        cutoff = _months_before(now or _utcnow(), months).isoformat()
        with self._connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM query_log
                    WHERE created_at < ?
                      AND (user_id IS NOT NULL OR session_id IS NOT NULL)""",
                (cutoff,),
            ).fetchone()[0]

    # ── read ────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        """Counts for the privacy/governance dashboard."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
            identified = conn.execute(
                """SELECT COUNT(*) FROM query_log
                    WHERE user_id IS NOT NULL OR session_id IS NOT NULL"""
            ).fetchone()[0]
            anonymized = conn.execute(
                "SELECT COUNT(*) FROM query_log WHERE anonymized_at IS NOT NULL"
            ).fetchone()[0]
            oldest = conn.execute("SELECT MIN(created_at) FROM query_log").fetchone()[0]
        return {
            "total": total,
            "identified": identified,
            "anonymized": anonymized,
            "retention_months": config.LOG_RETENTION_MONTHS,
            "oldest": oldest,
        }
