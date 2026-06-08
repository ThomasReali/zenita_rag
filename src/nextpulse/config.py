"""Configuration for RAG system"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# LLM API — OpenRouter (OpenAI-compatible). The OpenAI SDK talks to it via base_url.
# Put your OpenRouter key in OPENROUTER_API_KEY (OPENAI_API_KEY is also accepted as fallback).
OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
CHAT_MODEL = os.getenv("CHAT_MODEL", "openai/gpt-4o")
# Retries with exponential backoff on transient LLM errors (e.g. 429 rate-limit).
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "4"))

# Embeddings (local model, no API cost — OpenRouter does not serve an embeddings endpoint).
# multilingual-e5 expects "query:"/"passage:" prefixes; set both empty for non-e5 models.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
EMBEDDING_QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "query: ")
EMBEDDING_PASSAGE_PREFIX = os.getenv("EMBEDDING_PASSAGE_PREFIX", "passage: ")

# Vector store — Qdrant (embedded local by default; set QDRANT_URL to use a server)
QDRANT_PATH = Path(os.getenv("QDRANT_PATH", "./qdrant_data"))
QDRANT_URL = os.getenv("QDRANT_URL", "")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "documents")

# Document Processing — structural chunking sized to the embedding window (Fase 3).
# Measured in TOKENS of the embedding model (e5-small window = 512); max kept < 512 to
# leave room for the "passage:" prefix + special tokens → chunks are never truncated.
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "480"))
CHUNK_MIN_TOKENS = int(os.getenv("CHUNK_MIN_TOKENS", "200"))
# Legacy (char-based) — kept for back-compat; no longer used by the processor.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# RAG
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
# Response cache (in-memory, per RAGChain): repeated identical questions skip the LLM
# round-trip — lower latency/cost during demos and a guard against free-tier 429s.
RESPONSE_CACHE_ENABLED = os.getenv("RESPONSE_CACHE_ENABLED", "1") == "1"
RESPONSE_CACHE_SIZE = int(os.getenv("RESPONSE_CACHE_SIZE", "256"))
RESPONSE_CACHE_TTL_SECONDS = float(os.getenv("RESPONSE_CACHE_TTL_SECONDS", "1800"))
# Cross-encoder re-ranking (opt-in): fetch more candidates from the hybrid retrieval and
# re-order them with a cross-encoder before keeping top-k. Improves precision of the cited
# sources. OFF by default — it downloads a model and adds per-query latency; the pipeline is
# unchanged when disabled. The gate signal (dense cosine) is untouched by re-ranking.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "0") == "1"
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
RERANK_CANDIDATES = int(os.getenv("RERANK_CANDIDATES", "20"))

# Per-IP rate limiting on the LLM-cost endpoints (/api/query, /api/query/stream,
# /api/bandi/query): a sliding window guards against request floods / cost abuse — a real
# defense beyond input validation. In-memory, per process. RATE_LIMIT_PER_MINUTE requests
# are allowed per client IP within RATE_LIMIT_WINDOW_SECONDS; excess gets HTTP 429.
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1") == "1"
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# OCR fallback for scanned PDFs (opt-in). Pages whose extractable text is below
# OCR_PAGE_MIN_CHARS are rendered (PyMuPDF) and read with Tesseract — recovering the ~19
# scanned decrees that ingestion otherwise skips. OFF by default: needs the `ocr` extra
# (pymupdf + pytesseract + pillow) AND the Tesseract binary with the Italian language pack.
OCR_ENABLED = os.getenv("OCR_ENABLED", "0") == "1"
OCR_LANG = os.getenv("OCR_LANG", "ita")
OCR_DPI = int(os.getenv("OCR_DPI", "300"))
OCR_PAGE_MIN_CHARS = int(os.getenv("OCR_PAGE_MIN_CHARS", "100"))
# Explicit path to tesseract.exe when it is not on PATH (typical on Windows installs, e.g.
# C:\\Program Files\\Tesseract-OCR\\tesseract.exe). Empty → rely on PATH.
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
# Governance gate: top cosine score below this → deterministic "not in documentation"
# (no generation). Tuned for multilingual-e5-small (in-domain ≈0.85–0.90, off-topic ≈0.80).
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.82"))
# Ambiguity gate: an LLM judge checks if the retrieved sources are in conflict; if so the
# assistant responds with DISCRETION (cites sources, defers to the Bid Manager — no interpretation).
AMBIGUITY_JUDGE = os.getenv("AMBIGUITY_JUDGE", "1") == "1"
# Dominance guard: if ONE source leads the fused (RRF) ranking by at least this relative
# margin over the best of any other source, the retrieval points firmly at a single
# provvedimento → no genuine ambiguity to arbitrate, so the conflict judge is skipped.
# Prevents RF19 from misfiring on several parallel, non-contradictory provvedimenti.
AMBIGUITY_DOMINANCE_GAP = float(os.getenv("AMBIGUITY_DOMINANCE_GAP", "0.15"))
# Focus guard: a genuine RF19 conflict is a FOCUSED disagreement between few provvedimenti on
# the same point. When retrieval is fragmented across MORE distinct sources than this, it is a
# broad / under-specified query (many parallel provvedimenti), not a contradiction → the judge
# is skipped (answer grounded) instead of wrongly deferring. Keeps RF19 for the 2–3 source case.
AMBIGUITY_MAX_DISTINCT = int(os.getenv("AMBIGUITY_MAX_DISTINCT", "3"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
# Incremental indexing: per-file content-hash manifest (skip unchanged files on re-index).
INDEX_MANIFEST = QDRANT_PATH.parent / ".index_manifest.json"

# ── Governance: obsolescence & data-poisoning (DETERMINISTIC audit) ──────────
# The retrieval filter and the audit are deterministic (DB + metadata), never the
# LLM: this is the reliability the Public Administration sale requires.
# Retrieval hides chunks the audit has flagged. Legacy chunks WITHOUT a `status`
# field are NOT excluded (back-compatible: no re-index needed).
STATUS_FILTER_ENABLED = os.getenv("STATUS_FILTER_ENABLED", "1") == "1"
# Statuses hidden from retrieval; `active` and missing-status always pass through.
EXCLUDED_STATUSES = tuple(
    s.strip() for s in os.getenv("EXCLUDED_STATUSES", "obsolete,poisoned,draft").split(",")
    if s.strip()
)
# Second-pass "abrogato" notice: when the filtered retrieval finds nothing relevant,
# re-search WITHOUT the status filter; if the best matches are OBSOLETE, return a
# deterministic notice built from metadata (replaced_by/validity_end) — no LLM, no
# hallucination. Poisoned/draft stay silently hidden (generic refusal).
OBSOLETE_NOTICE_ENABLED = os.getenv("OBSOLETE_NOTICE_ENABLED", "1") == "1"
# Deterministic obsolescence audit (nightly job) — HYBRID source of truth.
# Primary: internal master file mapping source → status/replaced_by/validity_end.
GOVERNANCE_MASTER_FILE = Path(
    os.getenv("GOVERNANCE_MASTER_FILE", "./data/_governance/obsolescence.csv")
)
# Secondary (best-effort, Fase 2): Normattiva enrichment. OFF by default — Normattiva
# has no stable public REST API, so the check is pluggable and never fails the job.
NORMATTIVA_AUDIT_ENABLED = os.getenv("NORMATTIVA_AUDIT_ENABLED", "0") == "1"
# Append-only governance audit log (NIS2 integrity/traceability of status changes).
GOVERNANCE_LOG_PATH = Path(os.getenv("GOVERNANCE_LOG_PATH", "./governance_log.db"))

# Metadata enrichment (deterministic, offline): the MIT download manifest maps each decree
# file → official title, date, number and the official mit.gov.it URLs. scripts/enrich_metadata.py
# joins it onto the indexed chunks (set_payload, no re-embedding) so citations become verifiable
# with a link to the source. Join key = basename of the manifest `output_file`.
MIT_MANIFEST_FILE = Path(
    os.getenv("MIT_MANIFEST_FILE", "./KNOWLEDGE/MIT Decreti PDF/manifest_download_mit.json")
)

# Query log (GDPR) — one SQLite row per query for analytics/audit.
QUERY_LOG_ENABLED = os.getenv("QUERY_LOG_ENABLED", "1") == "1"
QUERY_LOG_PATH = Path(os.getenv("QUERY_LOG_PATH", "./query_log.db"))
# Data Anonymization: the nightly job NULLs user_id/session_id on log rows older
# than this window, so residual rows become purely statistical (out of GDPR scope).
LOG_RETENTION_MONTHS = int(os.getenv("LOG_RETENTION_MONTHS", "6"))

# Reversible Pseudonymization (GDPR Art. 32) — local PII masking layer between the
# Vector DB and the external LLM. Real PII never leaves the machine: it is tokenized
# before the prompt is sent and re-identified locally on the response.
PII_MASKING_ENABLED = os.getenv("PII_MASKING_ENABLED", "1") == "1"
# Detector backend: "auto" (Presidio if installed, else regex) | "regex" | "presidio".
PII_BACKEND = os.getenv("PII_BACKEND", "auto")
# spaCy model used by the Presidio backend (download separately, e.g. it_core_news_lg).
PII_SPACY_MODEL = os.getenv("PII_SPACY_MODEL", "it_core_news_lg")


def ensure_directories() -> None:
    """Create required directories (call once at startup, not on import)."""
    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
