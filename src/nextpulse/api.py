"""FastAPI backend for the NextPulse Sales Assistant.

Run from the project root:  uvicorn src.nextpulse.api:app --reload
Endpoints:
  GET  /api/status  → { documents, chunks, model }
  POST /api/query   → QueryResponse  (body: { question, history[], k? })
If web/dist exists (built frontend), it is served at /.
"""
import asyncio
import json
import logging
import queue
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

# Allow `uvicorn src.nextpulse.api:app` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from openai import RateLimitError  # noqa: E402 — distinguish LLM quota/rate-limit from outages
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from src.nextpulse import config  # noqa: E402
from src.nextpulse.rag_chain import RAGChain  # noqa: E402
from src.nextpulse.query_log import QueryLog  # noqa: E402
from src.nextpulse.ratelimit import SlidingWindowLimiter  # noqa: E402
from src.nextpulse.vector_store import VectorStore  # noqa: E402
from src.nextpulse.bandi_scraper import (  # noqa: E402
    BANDI_COLLECTION,
    CATEGORY_LABELS,
    PortaleAppaltiScraper,
)
try:
    from role_manager import ROLES  # noqa: E402
except ImportError as _err:
    raise ImportError(
        "Cannot import role_manager — launch uvicorn from the project root "
        f"or set PYTHONPATH to the project root. ({_err})"
    ) from _err


logger = logging.getLogger("nextpulse.api")

# Per-IP rate limiter shared across the LLM-cost endpoints (process-local sliding window).
_rate_limiter = SlidingWindowLimiter()


def _enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency: reject requests over the per-IP quota with HTTP 429.

    Thresholds are read from config at call time, so they can be tuned (or disabled) via
    env without restarting in tests. Keyed by client IP; falls back to a constant key when
    the peer address is unavailable (e.g. some test transports)."""
    if not config.RATE_LIMIT_ENABLED:
        return
    key = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(
        key, config.RATE_LIMIT_PER_MINUTE, config.RATE_LIMIT_WINDOW_SECONDS
    ):
        logger.warning("rate limit exceeded for %s", key)
        raise HTTPException(
            status_code=429,
            detail=("Troppe richieste in un breve intervallo. Attendi qualche secondo "
                    "prima di riprovare."),
        )

# ── Input limits (defense-in-depth: reject oversized/abusive payloads at the edge) ──
MAX_QUESTION_CHARS = 4000     # a question longer than this is almost certainly abuse
MAX_MESSAGE_CHARS = 8000      # per chat-history message
MAX_HISTORY_MESSAGES = 20     # bound conversational-memory size (token/cost guard)
MAX_ID_CHARS = 200            # opaque session/user identifiers

# ── Bandi/gare (Portale Appalti MIT) chatbot — domain-specific grounding prompt ──
BANDI_SYSTEM_PROMPT = """\
Sei un assistente specializzato nelle gare d'appalto del Ministero delle \
Infrastrutture e dei Trasporti (Portale Appalti MIT). Supporti l'ufficio gare a \
capire requisiti, scadenze, importi e condizioni dei bandi pubblicati sul portale \
(stato «In corso» e «In aggiudicazione»).

REGOLE FONDAMENTALI:
1. GROUNDING: Rispondi ESCLUSIVAMENTE con le informazioni presenti nei \
"DOCUMENTI DI GARA" qui sotto (disciplinari, capitolati, bandi, esiti).
2. NO HALLUCINATION: Se l'informazione non è nei documenti, dichiara: "Questa \
informazione non è presente nei documenti di gara indicizzati." Non inventare \
requisiti, importi, CIG o scadenze.
3. REQUISITI: Quando ti vengono chiesti i requisiti di partecipazione, elencali \
in modo puntuale e distingui (idoneità professionale, capacità economico-finanziaria, \
capacità tecnico-professionale, requisiti di ordine generale) quando possibile.
4. CITAZIONI: ogni documento INIZIA con il suo marcatore tra parentesi quadre (es. [1], \
[2]). Riporta inline ESATTAMENTE quel marcatore subito dopo la frase (es. "...entro 30 \
giorni [1]."). Scrivi SOLO il marcatore: niente parola "Fonte", niente nome del file, \
niente elenco fonti finale (la legenda è allegata dal sistema).
5. TONE: Professionale, sintetico, strutturato (elenchi puntati).

DOCUMENTI DI GARA:
{context_str}

Domanda dell'utente: {standalone_query}
Risposta:"""

BANDI_NO_CONTEXT = (
    "Questa informazione non è presente nei documenti di gara indicizzati. "
    "Avvia o aggiorna lo scraping dei bandi, oppure riformula la domanda."
)


def _bandi_vector_store(app: FastAPI) -> VectorStore:
    """Lazily create (and cache) the VectorStore bound to the bandi collection.

    Kept separate from the main KB so the gare corpus never mixes with the company
    documentation, and so scraping/indexing works even without an LLM API key."""
    vs = getattr(app.state, "bandi_vs", None)
    if vs is None:
        # Reuse the main store's Qdrant client + embedder: the embedded Qdrant locks the
        # whole storage folder per process, and a second SentenceTransformer load is wasteful.
        main = app.state.rag.vector_store
        vs = VectorStore(
            collection_name=BANDI_COLLECTION,
            client=main.client,
            embedder=main.embedder,
        )
        app.state.bandi_vs = vs
    return vs


def _bandi_rag(app: FastAPI) -> RAGChain:
    """Lazily create (and cache) the bandi-scoped RAG chatbot (needs an LLM key)."""
    rag = getattr(app.state, "bandi_rag", None)
    if rag is None:
        rag = RAGChain(
            vector_store=_bandi_vector_store(app),
            system_prompt_template=BANDI_SYSTEM_PROMPT,
            no_context_message=BANDI_NO_CONTEXT,
        )
        app.state.bandi_rag = rag
    return rag


class ChatMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: List[ChatMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    k: Optional[int] = Field(default=None, ge=1, le=20)  # bound retrieval breadth (cost/DoS)
    role: Optional[str] = Field(default=None, max_length=32)  # membership validated downstream (ROLES)
    session_id: Optional[str] = Field(default=None, max_length=MAX_ID_CHARS)  # opaque id (PII)
    user_id: Optional[str] = Field(default=None, max_length=MAX_ID_CHARS)     # opaque id (PII)


class QueryResponse(BaseModel):
    query: str
    standalone_query: str
    response: str
    sources: List[str]
    source_links: Optional[List[Optional[str]]] = None  # URL ufficiale per fonte (enrichment MIT)
    context: List[str]
    model: str
    grounded: bool
    ambiguous: bool
    obsolete: bool = False  # provvedimento pertinente trovato ma ABROGATO (avviso deterministico)
    top_score: float
    role: Optional[str] = None
    confidence: Optional[str] = None
    pii_masked: Optional[int] = None  # entità PII pseudonimizzate prima dell'invio all'LLM
    latency_ms: Optional[int] = None  # durata totale della pipeline in millisecondi
    cached: bool = False              # risposta servita dalla cache locale (no round-trip LLM)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load embedder + Qdrant once at startup (not per request).
    app.state.rag = RAGChain()
    # count_sources() scrolls Qdrant synchronously — offload to a thread
    # so the async event loop is not blocked during startup.
    app.state.documents = await asyncio.to_thread(app.state.rag.vector_store.count_sources)
    app.state.query_log = QueryLog() if config.QUERY_LOG_ENABLED else None
    # Bandi/gare section state: cached scrape results + a lock serializing access to the
    # embedded Qdrant bandi collection (writes during scrape vs reads during chat).
    app.state.bandi_cache = []
    app.state.bandi_lock = threading.Lock()
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


@app.post("/api/query", response_model=QueryResponse,
          dependencies=[Depends(_enforce_rate_limit)])
def query(req: QueryRequest):
    rag = app.state.rag
    try:
        result = rag.query(
            req.question,
            chat_history=[m.model_dump() for m in req.history],
            k=req.k,
            role=req.role,
        )
    except RateLimitError:
        # LLM quota / rate limit exhausted (e.g. OpenRouter free-models-per-day): this is an
        # actionable, distinct condition — surface it truthfully (HTTP 429) instead of a generic
        # outage, so the user knows to wait for the reset or configure a model with credit.
        logger.warning("LLM rate limit / quota exceeded on /api/query")
        raise HTTPException(
            status_code=429,
            detail=("Limite di richieste del modello LLM raggiunto (quota del piano gratuito). "
                    "Riprova più tardi o configura un modello con credito disponibile."),
        )
    except Exception:  # never leak provider internals / stack traces to the client
        logger.exception("query pipeline failed")
        raise HTTPException(
            status_code=502,
            detail="Il servizio di generazione non è momentaneamente disponibile. Riprova tra poco.",
        )

    # Query logging must never break the response (GDPR audit trail, best-effort).
    _log_query_result(rag, result, req)
    return result


def _log_query_result(rag: RAGChain, result: dict, req: "QueryRequest") -> None:
    """Best-effort GDPR audit log of a query result (never breaks the response)."""
    log = getattr(app.state, "query_log", None)
    if log is None:
        return
    try:
        logged = dict(result)
        if config.PII_MASKING_ENABLED:
            with rag.pseudonymizer.session() as s:
                logged["query"] = s.mask(logged.get("query") or "")
                logged["standalone_query"] = s.mask(logged.get("standalone_query") or "")
        log.record_result(logged, session_id=req.session_id, user_id=req.user_id)
    except Exception:
        pass


@app.post("/api/query/stream", dependencies=[Depends(_enforce_rate_limit)])
def query_stream(req: QueryRequest):
    """Streaming variant of /api/query (Server-Sent Events).

    Each line is `data: {json}` with a `phase` field: `meta` (gates decided),
    `token` (answer delta), `done` (final QueryResult), or `error`. The governance gates,
    role layer, PII masking and cache are identical to /api/query — only the delivery differs.
    """
    rag = app.state.rag
    history = [m.model_dump() for m in req.history]

    def gen():
        try:
            for phase, payload in rag.stream_query(
                req.question, chat_history=history, k=req.k, role=req.role
            ):
                if phase == "done":
                    _log_query_result(rag, payload, req)
                yield f"data: {json.dumps({'phase': phase, 'data': payload}, ensure_ascii=False)}\n\n"
        except RateLimitError:
            logger.warning("LLM rate limit / quota exceeded on /api/query/stream")
            err = {"phase": "error", "data": {"status": 429, "message": (
                "Limite di richieste del modello LLM raggiunto (quota del piano gratuito). "
                "Riprova più tardi o configura un modello con credito disponibile.")}}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("stream query pipeline failed")
            err = {"phase": "error", "data": {"status": 502, "message": (
                "Il servizio di generazione non è momentaneamente disponibile. Riprova tra poco.")}}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Offer-draft configurator (bozza d'offerta, grounded) ─────────────────────────

class ConfigureRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    needs: List[str] = Field(default_factory=list, max_length=10)  # esigenze per ampliare il retrieval
    k: Optional[int] = Field(default=None, ge=1, le=20)


class ConfigureResponse(BaseModel):
    scenario: str
    draft: str
    sources: List[str]
    source_links: Optional[List[Optional[str]]] = None
    grounded: bool
    top_score: float
    latency_ms: Optional[int] = None


@app.post("/api/configure", response_model=ConfigureResponse,
          dependencies=[Depends(_enforce_rate_limit)])
def configure(req: ConfigureRequest):
    """Produce a grounded, NON-BINDING draft offer for a customer scenario (RF4/UC4)."""
    from src.nextpulse.configurator import OfferConfigurator
    try:
        return OfferConfigurator(app.state.rag).configure(
            req.scenario, needs=req.needs, k=req.k
        )
    except RateLimitError:
        logger.warning("LLM rate limit / quota exceeded on /api/configure")
        raise HTTPException(
            status_code=429,
            detail=("Limite di richieste del modello LLM raggiunto (quota del piano gratuito). "
                    "Riprova più tardi o configura un modello con credito disponibile."),
        )
    except Exception:
        logger.exception("configure pipeline failed")
        raise HTTPException(
            status_code=502,
            detail="Il servizio di generazione non è momentaneamente disponibile. Riprova tra poco.",
        )


# ── Bandi / Gare d'Appalto (Portale Appalti MIT) ─────────────────────────────────

class BandiQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: List[ChatMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    k: Optional[int] = Field(default=None, ge=1, le=20)


def _grouped(tenders: List[dict]) -> dict:
    """Shape the cached scrape result for the UI: grouped by business category."""
    groups: dict = {key: [] for key in CATEGORY_LABELS}
    for t in tenders:
        groups.setdefault(t.get("category", "aggiudicazione"), []).append(t)
    return {
        "categories": [
            {"key": key, "label": CATEGORY_LABELS.get(key, key), "tenders": groups.get(key, [])}
            for key in CATEGORY_LABELS
        ],
        "total": len(tenders),
    }


@app.get("/api/bandi")
def bandi_list():
    """Return the last scraped bandi (grouped), without re-scraping."""
    return _grouped(getattr(app.state, "bandi_cache", []))


@app.get("/api/bandi/scrape")
async def bandi_scrape():
    """Scrape MIT bandi (in corso + aggiudicazione), index them, and stream progress.

    Server-Sent Events: each line is `data: {json}` with a `phase` field
    (`listing` | `tender` | `done` | `error`). The UI shows a live spinner/progress
    and renders each bando as it is indexed.
    """
    q: "queue.Queue" = queue.Queue()
    holder: dict = {}
    vs = _bandi_vector_store(app)
    lock = app.state.bandi_lock

    def worker():
        # Serialize against the bandi chatbot's reads on the embedded Qdrant collection.
        with lock:
            try:
                scraper = PortaleAppaltiScraper(vector_store=vs)
                holder["results"] = scraper.ingest(progress=q.put)
            except Exception as exc:  # surface a clean error event to the stream
                logger.exception("bandi scrape failed")
                q.put({"phase": "error", "message": str(exc)})
            finally:
                q.put(None)  # stream sentinel

    threading.Thread(target=worker, daemon=True).start()

    async def event_stream():
        while True:
            event = await asyncio.to_thread(q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if "results" in holder:
            app.state.bandi_cache = holder["results"]

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/bandi/query", response_model=QueryResponse,
          dependencies=[Depends(_enforce_rate_limit)])
def bandi_query(req: BandiQueryRequest):
    """RAG chatbot scoped to the scraped MIT bandi corpus."""
    try:
        rag = _bandi_rag(app)
    except ValueError as exc:  # missing LLM API key
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        with app.state.bandi_lock:
            result = rag.query(
                req.question,
                chat_history=[m.model_dump() for m in req.history],
                k=req.k,
            )
    except RateLimitError:
        logger.warning("LLM rate limit / quota exceeded on /api/bandi/query")
        raise HTTPException(
            status_code=429,
            detail=("Limite di richieste del modello LLM raggiunto (quota del piano gratuito). "
                    "Riprova più tardi o configura un modello con credito disponibile."),
        )
    except Exception:
        logger.exception("bandi query pipeline failed")
        raise HTTPException(
            status_code=502,
            detail="Il servizio di generazione non è momentaneamente disponibile. Riprova tra poco.",
        )
    return result


# Serve the built frontend (web/dist) if present — single-origin deploy.
_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
