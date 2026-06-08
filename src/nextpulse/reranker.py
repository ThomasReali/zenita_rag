"""Optional cross-encoder re-ranking stage (opt-in via config.RERANK_ENABLED).

Hybrid retrieval (dense e5 + BM25, RRF) is recall-oriented: it surfaces plausible
candidates fast. A cross-encoder reads the (query, passage) pair *jointly* and scores
true relevance far more precisely than the bi-encoder cosine — so re-ranking the fused
candidates and keeping the best top-k sharpens which sources end up cited.

The model is loaded lazily and once (module-level singleton): the first reranked query
pays the download/load, the rest reuse it. Disabled by default so neither the running app
nor the test suite incur the model download.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from src.nextpulse import config

logger = logging.getLogger("nextpulse.reranker")

_model = None  # lazily-loaded CrossEncoder singleton


def get_model():
    """Load (once) and return the cross-encoder. Imported lazily so the heavy model is
    only touched when re-ranking is actually enabled."""
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        logger.info("loading cross-encoder reranker: %s", config.RERANK_MODEL)
        _model = CrossEncoder(config.RERANK_MODEL)
    return _model


def rerank(
    query: str,
    docs: List[str],
    metas: List[dict],
    scores: List[float],
    top_k: int,
    *,
    model=None,
) -> Tuple[List[str], List[dict], List[float]]:
    """Re-order (docs, metas, scores) by cross-encoder relevance to `query`, keep top_k.

    The returned `scores` are the ORIGINAL RRF scores carried along in the new order — the
    downstream RF19 dominance heuristic expects the positive RRF scale, and the relevance
    gate uses the dense cosine (computed upstream), so neither is disturbed. Re-ranking only
    changes WHICH candidates survive and in WHAT order (citation numbering + LLM context).
    On any failure it degrades gracefully to the original ranking (truncated to top_k).
    """
    if not docs:
        return docs, metas, scores
    try:
        ce = model or get_model()
        ce_scores = ce.predict([(query, d) for d in docs])
        order = sorted(range(len(docs)), key=lambda i: float(ce_scores[i]), reverse=True)[:top_k]
    except Exception:  # never let an optional stage break retrieval
        logger.exception("re-ranking failed; falling back to the fused ranking")
        order = list(range(min(top_k, len(docs))))
    return (
        [docs[i] for i in order],
        [metas[i] for i in order],
        [scores[i] for i in order],
    )
