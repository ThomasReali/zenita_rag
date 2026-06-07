"""Vector store using Qdrant (embedded local) — hybrid dense + BM25 sparse retrieval."""
import re
import uuid
import zlib
from collections import Counter
from typing import List, Optional, Tuple
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer
from src.nextpulse import config

_UPSERT_BATCH = 1000
_TEXT_KEY = "_text"          # chunk text stored in the point payload
_DENSE = "dense"             # named dense vector
_SPARSE = "bm25"             # named sparse vector (BM25, IDF applied by Qdrant)
_RRF_K = 60                  # Reciprocal Rank Fusion constant
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _sparse_vector(text: str, is_query: bool) -> Optional[models.SparseVector]:
    """BM25-style sparse vector: hashed token ids → term frequencies (None if empty)."""
    tokens = [t.lower() for t in _TOKEN.findall(text) if len(t) > 2]
    if not tokens:
        return None
    counts = Counter(zlib.crc32(t.encode("utf-8")) & 0x7FFFFFFF for t in tokens)
    indices = list(counts.keys())
    # query: presence (1.0); document: term frequency. Qdrant applies IDF (collection modifier).
    values = [1.0] * len(indices) if is_query else [float(c) for c in counts.values()]
    return models.SparseVector(indices=indices, values=values)


class VectorStore:
    """Local Qdrant store with hybrid retrieval; interface stable across phases."""

    def __init__(self, collection_name: Optional[str] = None, client=None, embedder=None):
        # The embedded (path-based) Qdrant locks the whole storage folder per process, so a
        # second store over the same data MUST reuse the existing client (and may reuse the
        # embedder to avoid loading the model twice). Pass `client`/`embedder` to share them;
        # only the `collection_name` differs (e.g. the bandi/gare corpus).
        self.embedder = embedder or SentenceTransformer(config.EMBEDDING_MODEL)
        self.collection_name = collection_name or config.COLLECTION_NAME
        if client is not None:
            self.client = client
        elif config.QDRANT_URL:
            self.client = QdrantClient(url=config.QDRANT_URL)
        else:
            self.client = QdrantClient(path=str(config.QDRANT_PATH))

        if hasattr(self.embedder, "get_embedding_dimension"):
            dim = self.embedder.get_embedding_dimension()
        else:  # older sentence-transformers
            dim = self.embedder.get_sentence_embedding_dimension()

        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    _DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)
                },
                sparse_vectors_config={
                    _SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )

    @staticmethod
    def _make_id(text: str, meta: dict) -> str:
        """Deterministic UUID from source + chunk_id + text → idempotent upsert (RF6)."""
        key = f"{meta.get('source', '')}|{meta.get('chunk_id', '')}|{text}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    def add_documents(
        self,
        texts: List[str],
        metadatas: List[dict],
        ids: Optional[List[str]] = None,
    ):
        """Embed (dense + sparse) and upsert documents. Idempotent on deterministic ids."""
        if ids is None:
            ids = [self._make_id(t, m) for t, m in zip(texts, metadatas)]

        for i in range(0, len(texts), _UPSERT_BATCH):
            sl = slice(i, i + _UPSERT_BATCH)
            dense = self.embedder.encode(
                [config.EMBEDDING_PASSAGE_PREFIX + t for t in texts[sl]],
                convert_to_tensor=False,
            ).tolist()
            points = []
            for pid, dvec, txt, meta in zip(ids[sl], dense, texts[sl], metadatas[sl]):
                vector = {_DENSE: dvec}
                sparse = _sparse_vector(txt, is_query=False)
                if sparse is not None:
                    vector[_SPARSE] = sparse
                points.append(
                    models.PointStruct(
                        id=pid, vector=vector,
                        # Governance default: every chunk is born "active" unless the caller
                        # says otherwise. Legacy points predating this field stay filter-safe
                        # (the retrieval filter excludes known-bad statuses, not missing ones).
                        payload={"status": "active", **meta, _TEXT_KEY: txt},
                    )
                )
            self.client.upsert(collection_name=self.collection_name, points=points)

    def _query_filter(self, where: Optional[dict] = None, exclude_status: Tuple[str, ...] = ()):
        """Build a Qdrant payload filter: `where` → equality (must); `exclude_status` →
        must_not on `status`. Excluding by status (rather than requiring status="active")
        keeps legacy points without a status field visible — back-compatible, no re-index."""
        must = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in (where or {}).items()
        ]
        must_not = [
            models.FieldCondition(key="status", match=models.MatchValue(value=s))
            for s in exclude_status
        ]
        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)

    def search(
        self, query: str, k: Optional[int] = None, where: Optional[dict] = None,
        exclude_status: Tuple[str, ...] = (),
    ) -> Tuple[List[str], List[dict], List[float], float]:
        """Hybrid (dense + BM25) retrieval with RRF fusion.

        Returns (texts, metadatas, rrf_scores, max_dense_cosine). The last value is the top
        dense cosine similarity, used by the governance gate (RF10) — a stable, calibrated
        signal independent of the RRF fusion scale. `where` applies a payload filter to both;
        `exclude_status` hides chunks the deterministic audit flagged (obsolete/poisoned/draft).
        """
        k = k or config.RETRIEVAL_K
        qfilter = self._query_filter(where, exclude_status)
        fetch = max(k * 4, 20)

        dense_vec = self.embedder.encode(
            config.EMBEDDING_QUERY_PREFIX + query, convert_to_tensor=False
        ).tolist()
        dense_pts = self.client.query_points(
            collection_name=self.collection_name, query=dense_vec, using=_DENSE,
            limit=fetch, query_filter=qfilter, with_payload=True,
        ).points

        sparse_vec = _sparse_vector(query, is_query=True)
        sparse_pts = []
        if sparse_vec is not None:
            sparse_pts = self.client.query_points(
                collection_name=self.collection_name, query=sparse_vec, using=_SPARSE,
                limit=fetch, query_filter=qfilter, with_payload=True,
            ).points

        # Reciprocal Rank Fusion
        rrf: dict = {}
        payloads: dict = {}
        cosines: dict = {}
        for rank, p in enumerate(dense_pts):
            rrf[p.id] = rrf.get(p.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            payloads[p.id] = p.payload
            cosines[p.id] = p.score
        for rank, p in enumerate(sparse_pts):
            rrf[p.id] = rrf.get(p.id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            payloads.setdefault(p.id, p.payload)

        top_ids = sorted(rrf, key=lambda i: rrf[i], reverse=True)[:k]
        docs: List[str] = []
        metas: List[dict] = []
        scores: List[float] = []
        for pid in top_ids:
            payload = dict(payloads[pid] or {})
            docs.append(payload.pop(_TEXT_KEY, ""))
            metas.append(payload)
            scores.append(rrf[pid])

        # Gate signal: best dense cosine among returned results.
        # If dense returned nothing (empty collection / strict filter) but BM25 did,
        # pass the gate at the minimum threshold instead of blocking on a spurious 0.0.
        max_cosine = max(cosines.values()) if cosines else (config.SCORE_THRESHOLD if rrf else 0.0)
        return docs, metas, scores, max_cosine

    def get_stats(self) -> dict:
        try:
            count = self.client.count(
                collection_name=self.collection_name, exact=True
            ).count
        except Exception:
            count = 0
        return {"count": count, "collection": self.collection_name}

    def count_sources(self) -> int:
        """Number of distinct source documents (KB status — RF15, not the chunk count)."""
        sources = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=1000, offset=offset,
                with_payload=["source"], with_vectors=False,
            )
            for p in points:
                src = (p.payload or {}).get("source")
                if src:
                    sources.add(src)
            if offset is None:
                break
        return len(sources)

    def delete_by_source(self, source: str):
        """Remove all chunks of a given source document (incremental re-index)."""
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key="source", match=models.MatchValue(value=source)
                    )]
                )
            ),
        )

    def set_status_by_source(
        self, source: str, status: str, *,
        replaced_by: Optional[str] = None, validity_end: Optional[str] = None,
    ):
        """Flag every chunk of a source with a governance `status` (Qdrant set_payload).

        Deterministic, idempotent, NO re-embedding. This is the primitive used by the
        nightly obsolescence audit (active→obsolete) and by the anti-poisoning quarantine
        (→poisoned). `replaced_by`/`validity_end` are written only when provided, so the
        abrogation notice can be built from metadata alone (no LLM)."""
        payload: dict = {"status": status}
        if replaced_by is not None:
            payload["replaced_by"] = replaced_by
        if validity_end is not None:
            payload["validity_end"] = validity_end
        self.client.set_payload(
            collection_name=self.collection_name,
            payload=payload,
            points=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key="source", match=models.MatchValue(value=source)
                    )]
                )
            ),
        )

    def source_statuses(self) -> dict:
        """Map each distinct `source` → its current `status` (first chunk seen).

        Used by the audit/quarantine tools to know the previous status (for the
        governance log) and to skip no-op updates (idempotency)."""
        out: dict = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=1000, offset=offset,
                with_payload=["source", "status"], with_vectors=False,
            )
            for p in points:
                pl = p.payload or {}
                src = pl.get("source")
                if src and src not in out:
                    out[src] = pl.get("status", "active")
            if offset is None:
                break
        return out
