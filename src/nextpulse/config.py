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
# Governance gate: top cosine score below this → deterministic "not in documentation"
# (no generation). Tuned for multilingual-e5-small (in-domain ≈0.85–0.90, off-topic ≈0.80).
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.82"))
# Ambiguity gate: an LLM judge checks if the retrieved sources are in conflict; if so the
# assistant responds with DISCRETION (cites sources, defers to the Bid Manager — no interpretation).
AMBIGUITY_JUDGE = os.getenv("AMBIGUITY_JUDGE", "1") == "1"
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
# Incremental indexing: per-file content-hash manifest (skip unchanged files on re-index).
INDEX_MANIFEST = QDRANT_PATH.parent / ".index_manifest.json"

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
