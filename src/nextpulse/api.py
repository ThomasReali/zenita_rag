"""FastAPI backend for the NextPulse Sales Assistant.

Run from the project root:  uvicorn src.nextpulse.api:app --reload
Endpoints:
  GET  /api/status  → { documents, chunks, model }
  POST /api/query   → QueryResponse  (body: { question, history[], k? })
If web/dist exists (built frontend), it is served at /.
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

# Allow `uvicorn src.nextpulse.api:app` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.nextpulse import config  # noqa: E402
from src.nextpulse.rag_chain import RAGChain  # noqa: E402
from src.nextpulse.query_log import QueryLog  # noqa: E402
try:
    from role_manager import ROLES  # noqa: E402
except ImportError as _err:
    raise ImportError(
        "Cannot import role_manager — launch uvicorn from the project root "
        f"or set PYTHONPATH to the project root. ({_err})"
    ) from _err


class ChatMessage(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    history: List[ChatMessage] = []
    k: Optional[int] = None
    role: Optional[str] = None  # sales | presales | bid_manager (None → default behaviour)
    session_id: Optional[str] = None  # opaque per-session id (PII → anonymized after retention)
    user_id: Optional[str] = None     # opaque client id (PII → anonymized after retention)


class QueryResponse(BaseModel):
    query: str
    standalone_query: str
    response: str
    sources: List[str]
    context: List[str]
    model: str
    grounded: bool
    ambiguous: bool
    top_score: float
    role: Optional[str] = None
    confidence: Optional[str] = None
    pii_masked: Optional[int] = None  # entità PII pseudonimizzate prima dell'invio all'LLM
    latency_ms: Optional[int] = None  # durata totale della pipeline in millisecondi


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load embedder + Qdrant once at startup (not per request).
    app.state.rag = RAGChain()
    # count_sources() scrolls Qdrant synchronously — offload to a thread
    # so the async event loop is not blocked during startup.
    app.state.documents = await asyncio.to_thread(app.state.rag.vector_store.count_sources)
    app.state.query_log = QueryLog() if config.QUERY_LOG_ENABLED else None
    yield


app = FastAPI(title="NextPulse Sales Assistant API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def status():
    rag = app.state.rag
    return {
        "documents": app.state.documents,
        "chunks": rag.vector_store.get_stats()["count"],
        "model": rag.model,
    }


@app.get("/api/roles")
def roles():
    return [
        {
            "key": key,
            "name": rc.name,
            "terminology_level": rc.terminology_level,
            "require_source_citation": rc.require_source_citation,
        }
        for key, rc in ROLES.items()
    ]


@app.get("/api/privacy")
def privacy():
    """Data-governance snapshot: retention policy + query-log anonymization status."""
    log = getattr(app.state, "query_log", None)
    base = {
        "logging_enabled": log is not None,
        "retention_months": config.LOG_RETENTION_MONTHS,
        "anonymization": "nightly job NULLs user_id & session_id on rows older than retention",
    }
    if log is not None:
        base.update(log.stats())
    return base


@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    rag = app.state.rag
    try:
        result = rag.query(
            req.question,
            chat_history=[m.model_dump() for m in req.history],
            k=req.k,
            role=req.role,
        )
    except Exception as e:  # surface LLM/provider errors (e.g. 429) to the UI
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")

    # Query logging must never break the response (GDPR audit trail, best-effort).
    log = getattr(app.state, "query_log", None)
    if log is not None:
        try:
            logged = dict(result)
            if config.PII_MASKING_ENABLED:
                with rag.pseudonymizer.session() as s:
                    logged["query"] = s.mask(logged.get("query") or "")
                    logged["standalone_query"] = s.mask(logged.get("standalone_query") or "")
            log.record_result(logged, session_id=req.session_id, user_id=req.user_id)
        except Exception:
            pass
    return result


# Serve the built frontend (web/dist) if present — single-origin deploy.
_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
