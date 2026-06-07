"""R.A.M. (Logistica Infrastrutture e Trasporti SpA) bandi/gare scraper + RAG ingestion.

The RAM e-procurement portal (https://ramspa.acquistitelematici.it — DigitalPA "pacman"
platform) renders its tender lists client-side from a JSON web service and links the
actual tender documents (disciplinare, capitolato, bando …) as PDFs on per-tender detail
pages. This module:

  1. Pulls the tender listing from the JSON endpoint  (``/ws/tender/fe``).
  2. Buckets each tender by ``stato`` into two business categories:
        • ``in_corso``        — open / running procedures (bandi in corso)
        • ``aggiudicazione``  — awarded / closed procedures (bandi in aggiudicazione)
  3. Scrapes each tender's detail page for its attached PDF documents.
  4. Downloads + token-chunks those PDFs with the shared :class:`DocumentProcessor`.
  5. Heuristically extracts the *requisiti di partecipazione* of each bando.
  6. Indexes everything into a dedicated Qdrant collection so the bandi chatbot can
     answer questions grounded ONLY on the scraped gare.

The :class:`RamScraper.ingest` method drives the whole pipeline and emits structured
progress events through a callback, which the API layer streams to the UI (loading
spinner / live progress).
"""
from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Callable, Dict, List, Optional, Tuple

import requests

from src.nextpulse import config
from src.nextpulse.document_processor import DocumentProcessor
from src.nextpulse.vector_store import VectorStore

logger = logging.getLogger("nextpulse.ram_scraper")

# ── portal endpoints ─────────────────────────────────────────────────────────────
BASE_URL = "https://ramspa.acquistitelematici.it"
LISTING_WS = "/ws/tender/fe"          # JSON: {"data": [...], "meta": {...}}
LISTING_TYPE = "procedure-gara"       # the web component's tender "type"
BANDI_COLLECTION = "bandi_ram"        # dedicated Qdrant collection (separate from the KB)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

# Be a polite scraper: small pause between detail-page / PDF requests.
_REQUEST_PAUSE_S = 0.25

# A tender can attach 20+ files, most of them boilerplate forms (DGUE, privacy, modello
# offerta, dichiarazioni…) that carry no participation requirements and would needlessly
# bloat the index and slow the scrape. We keep only the requirement-bearing documents,
# ranked by usefulness, capped per tender.
_DOC_PRIORITY = [re.compile(p, re.I) for p in (
    r"disciplinare",
    r"capitolato",
    r"\bbando\b",
    r"requisit",
    r"tecnic|prestazional",
    r"esito|aggiudic|verbale|graduatoria|determina",
    r"avviso|lettera.*invito|invito",
)]
_MAX_DOCS_PER_TENDER = 4


def select_relevant_documents(docs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Keep only requirement-bearing documents (disciplinare/capitolato/bando/esito …).

    Ranks by :data:`_DOC_PRIORITY` and caps at :data:`_MAX_DOCS_PER_TENDER`. Falls back to
    the first couple of attachments when nothing matches, so a tender is never left empty.
    """
    scored: List[Tuple[int, str, str]] = []
    for url, label in docs:
        hay = f"{label} {url}".lower()
        for rank, pat in enumerate(_DOC_PRIORITY):
            if pat.search(hay):
                scored.append((rank, url, label))
                break
    scored.sort(key=lambda x: x[0])
    selected = [(u, l) for _, u, l in scored[:_MAX_DOCS_PER_TENDER]]
    if not selected and docs:
        selected = docs[:2]
    return selected

# ── tender-status → business category ────────────────────────────────────────────
# The portal exposes a fine-grained `stato`; we collapse it into the two buckets the
# brief asks for: "bandi in corso" vs "bandi in aggiudicazione".
_IN_CORSO_STATI = {"in_corso", "scaduta", "in_svolgimento", "pubblicata", "in_pubblicazione"}
_AGGIUDICAZIONE_STATI = {
    "aggiudicata", "aggiudicazione", "conclusa", "conclusa_affidata",
    "chiusa", "annullata",
}
CATEGORY_IN_CORSO = "in_corso"
CATEGORY_AGGIUDICAZIONE = "aggiudicazione"
CATEGORY_LABELS = {
    CATEGORY_IN_CORSO: "Bandi in corso",
    CATEGORY_AGGIUDICAZIONE: "Bandi in aggiudicazione",
}


def categorize_stato(stato: Optional[str]) -> str:
    """Map a portal ``stato`` onto a business category (defaults to aggiudicazione)."""
    s = (stato or "").strip().lower()
    if s in _IN_CORSO_STATI:
        return CATEGORY_IN_CORSO
    if s in _AGGIUDICAZIONE_STATI:
        return CATEGORY_AGGIUDICAZIONE
    # Unknown states: if it sounds open keep it in corso, else treat as awarded.
    return CATEGORY_IN_CORSO if "cors" in s or "svolg" in s else CATEGORY_AGGIUDICAZIONE


# ── requirements extraction ──────────────────────────────────────────────────────
# Headings that introduce a requirements block in an Italian disciplinare di gara.
_REQ_HEADING = re.compile(
    r"requisit[oi]\b.*?(partecipazion|idoneit|ordine generale|capacit|"
    r"tecnic|professional|economic|finanziari|special)",
    re.I,
)
_REQ_ANY = re.compile(r"\brequisit[oi]\b", re.I)
# A line that reads like an individual requirement item.
_REQ_ITEM_HINT = re.compile(
    r"\b(deve|devono|dovrà|possesso|iscrizion[e]|fatturat|abilitazion|"
    r"attestazion|certificazion|esperienz|requisit[oi]|idoneit|capacit)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[-•▪◦*]|[a-z]\)|[a-z]\.|\d+[\).]|[ivx]+\))\s+", re.I)
# Table-of-contents lines (dotted leaders / trailing page numbers) carry no real content.
_TOC_LINE = re.compile(r"\.{4,}|…{2,}|\.\s*\.\s*\.")
_TRAILING_PAGENO = re.compile(r"[\s.·]+\d{1,4}\s*$")
_MAX_REQUIREMENTS = 12
_MAX_REQ_LEN = 320


def extract_requirements(text: str) -> List[str]:
    """Best-effort extraction of the *requisiti di partecipazione* from a bando's text.

    Strategy: locate paragraphs introduced by a "requisiti …" heading and keep the
    item-like lines that follow; if no heading is found, fall back to scanning the whole
    document for requirement-shaped sentences. Returns a de-duplicated, length-capped list.
    """
    if not text:
        return []

    # Normalise per physical line and drop table-of-contents lines (dotted leaders),
    # which otherwise masquerade as headings and swamp the real requirement text.
    raw_lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if _TOC_LINE.search(ln):
            continue
        ln = _TRAILING_PAGENO.sub("", ln).strip()
        raw_lines.append(ln)
    found: List[str] = []
    seen: set = set()

    def _push(item: str) -> None:
        item = re.sub(r"\s+", " ", item).strip(" .;:-•▪◦*")
        if len(item) < 12 or len(item) > _MAX_REQ_LEN:
            if len(item) > _MAX_REQ_LEN:
                item = item[:_MAX_REQ_LEN].rsplit(" ", 1)[0] + "…"
            else:
                return
        key = item.lower()[:80]
        if key in seen:
            return
        seen.add(key)
        found.append(item)

    # Pass 1 — collect lines inside a "requisiti …" section (until a blank gap / new heading).
    in_section = False
    section_gap = 0
    for ln in raw_lines:
        if not ln:
            section_gap += 1
            if section_gap >= 3:
                in_section = False
            continue
        section_gap = 0
        if _REQ_HEADING.search(ln):
            in_section = True
            # The heading itself is a useful "requirement category" label.
            _push(ln)
            continue
        if in_section and (_BULLET.match(ln) or _REQ_ITEM_HINT.search(ln)):
            _push(ln)
        if len(found) >= _MAX_REQUIREMENTS:
            return found

    # Pass 2 — fallback: requirement-shaped sentences anywhere in the document.
    if len(found) < 3:
        joined = re.sub(r"\s+", " ", " ".join(l for l in raw_lines if l))
        for sentence in re.split(r"(?<=[.;])\s+", joined):
            if _REQ_ANY.search(sentence) and _REQ_ITEM_HINT.search(sentence):
                _push(sentence)
            if len(found) >= _MAX_REQUIREMENTS:
                break

    return found[:_MAX_REQUIREMENTS]


# ── listing + detail scraping ────────────────────────────────────────────────────

def _abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return BASE_URL + ("" if href.startswith("/") else "/") + href


def fetch_listing(session: requests.Session, type_: str = LISTING_TYPE) -> List[dict]:
    """Fetch the raw tender records from the portal's JSON web service."""
    url = _abs_url(LISTING_WS)
    resp = session.get(url, params={"type": type_}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", []) if isinstance(payload, dict) else []


def normalize_tender(raw: dict) -> dict:
    """Project a raw portal record onto the fields the app cares about."""
    tid = str(raw.get("id", "")).strip()
    title = (raw.get("title") or raw.get("oggetto") or "").strip()
    detail_path = raw.get("url_tender") or (f"/tender/{tid}" if tid else "")
    stato = raw.get("stato")
    return {
        "id": tid,
        "title": title,
        "cig": (raw.get("cig_number") or raw.get("cig") or "").strip(),
        "tipologia": (raw.get("tipologia") or "").strip(),
        "stato": stato,
        "category": categorize_stato(stato),
        "data_pubblicazione": raw.get("data_pubblicazione") or "",
        "data_scadenza": raw.get("data_scadenza") or "",
        "importo": raw.get("importo_complessivo_gara") or raw.get("importo_gara") or "",
        "rup": (raw.get("rup") or "").strip(),
        "detail_url": _abs_url(detail_path) if detail_path else "",
        "servizio": (raw.get("servizio") or raw.get("nome_ente") or "").strip(),
    }


# Anchor → (href, label) for tender PDF attachments. Document links live under
# /tender/documenti/... (gara docs) and /tender/award-document/... (esiti/aggiudicazione).
_DOC_ANCHOR = re.compile(
    r'href="(/tender/(?:documenti|award-document)/[^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_TAG_STRIP = re.compile(r"<[^>]+>")
# Skip machine files that carry no readable requirements (eForms XML, signatures …).
_SKIP_DOC_EXT = (".xml", ".p7m", ".asice", ".asics", ".zip", ".json")


def fetch_tender_documents(
    session: requests.Session, detail_url: str
) -> List[Tuple[str, str]]:
    """Return ``[(absolute_pdf_url, label), …]`` for a tender's detail page."""
    resp = session.get(detail_url, timeout=30)
    resp.raise_for_status()
    docs: List[Tuple[str, str]] = []
    seen: set = set()
    for href, inner in _DOC_ANCHOR.findall(resp.text):
        low = href.lower()
        if any(low.split("?")[0].endswith(ext) for ext in _SKIP_DOC_EXT):
            continue
        url = _abs_url(href)
        if url in seen:
            continue
        seen.add(url)
        label = unescape(_TAG_STRIP.sub("", inner)).strip()
        label = re.sub(r"\s+", " ", label) or href.rsplit("/", 1)[-1]
        docs.append((url, label))
    return docs


# ── ingestion pipeline ───────────────────────────────────────────────────────────

ProgressCallback = Callable[[dict], None]


class RamScraper:
    """Scrape RAM bandi end-to-end and index them into the bandi Qdrant collection."""

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        processor: Optional[DocumentProcessor] = None,
        session: Optional[requests.Session] = None,
    ):
        self.vs = vector_store or VectorStore(collection_name=BANDI_COLLECTION)
        self.processor = processor or DocumentProcessor()
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    # -- per-tender ----------------------------------------------------------------

    def _index_tender(self, tender: dict) -> dict:
        """Download a tender's PDFs, chunk + index them, extract requirements.

        Returns an enriched tender dict (adds documents, requirements, chunk count).
        """
        documents = fetch_tender_documents(self.session, tender["detail_url"]) \
            if tender.get("detail_url") else []
        # Index only the requirement-bearing documents (skip boilerplate forms).
        documents = select_relevant_documents(documents)

        all_chunks: List[Tuple[str, dict]] = []
        full_text_parts: List[str] = []
        indexed_docs: List[dict] = []

        for url, label in documents:
            time.sleep(_REQUEST_PAUSE_S)
            try:
                r = self.session.get(url, timeout=60)
                r.raise_for_status()
                raw = r.content
            except Exception as exc:  # noqa: BLE001 — one bad PDF must not abort the bando
                logger.warning("Download failed for %s (%s)", url, exc)
                continue

            source = f"RAM_bando_{tender['id']}_{url.rsplit('/', 1)[-1]}"
            base_meta = {
                "source": source,
                "doc_type": "pdf",
                "category": "bando",
                "gara_category": tender["category"],
                "tender_id": tender["id"],
                "tender_title": tender["title"],
                "cig": tender["cig"],
                "stato": tender["stato"] or "",
                "doc_label": label,
                "source_url": tender["detail_url"],
            }
            try:
                chunks = self.processor.process_pdf_bytes(raw, base_meta)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Chunking failed for %s (%s)", source, exc)
                chunks = []

            if chunks:
                all_chunks.extend(chunks)
                full_text_parts.append("\n".join(t for t, _ in chunks))
                indexed_docs.append({"label": label, "url": url, "chunks": len(chunks)})

        requirements = extract_requirements("\n".join(full_text_parts))

        # A synthetic, retrievable "requisiti" chunk per bando, so the chatbot can surface
        # the participation requirements directly even when they are spread across PDFs.
        if requirements:
            req_text = (
                f"Requisiti di partecipazione del bando «{tender['title']}» "
                f"(CIG {tender['cig'] or 'n/d'}):\n- " + "\n- ".join(requirements)
            )
            all_chunks.append((req_text, {
                "source": f"RAM_bando_{tender['id']}_requisiti",
                "doc_type": "txt",
                "category": "bando",
                "gara_category": tender["category"],
                "tender_id": tender["id"],
                "tender_title": tender["title"],
                "cig": tender["cig"],
                "stato": tender["stato"] or "",
                "doc_label": "Requisiti (estratti)",
                "source_url": tender["detail_url"],
                "chunk_id": 0,
            }))

        if all_chunks:
            # Idempotent re-index: drop previous chunks for these sources first.
            for src in {m["source"] for _, m in all_chunks}:
                self.vs.delete_by_source(src)
            self.vs.add_documents(
                texts=[t for t, _ in all_chunks],
                metadatas=[m for _, m in all_chunks],
            )

        return {
            **tender,
            "documents": indexed_docs,
            "requirements": requirements,
            "chunks": len(all_chunks),
        }

    # -- driver --------------------------------------------------------------------

    def ingest(self, progress: Optional[ProgressCallback] = None) -> List[dict]:
        """Run the full scrape→index pipeline, emitting progress events.

        Progress events (dicts) have a ``phase`` key:
            • ``listing``  — {phase, total}
            • ``tender``   — {phase, index, total, tender:{…}, requirements, chunks}
            • ``done``     — {phase, total, chunks, by_category}
            • ``error``    — {phase, message}
        Returns the list of enriched tender dicts.
        """
        def emit(event: dict) -> None:
            if progress is not None:
                try:
                    progress(event)
                except Exception:  # never let the UI stream break the pipeline
                    logger.debug("progress callback raised", exc_info=True)

        raw = fetch_listing(self.session)
        tenders = [normalize_tender(r) for r in raw]
        tenders = [t for t in tenders if t["id"] and t["title"]]
        emit({"phase": "listing", "total": len(tenders)})

        results: List[dict] = []
        total_chunks = 0
        for i, tender in enumerate(tenders, start=1):
            try:
                enriched = self._index_tender(tender)
            except Exception as exc:  # noqa: BLE001 — robustness: skip the broken bando
                logger.error("Failed to ingest tender %s: %s", tender["id"], exc,
                             exc_info=True)
                enriched = {**tender, "documents": [], "requirements": [],
                            "chunks": 0, "error": str(exc)}
            results.append(enriched)
            total_chunks += enriched.get("chunks", 0)
            emit({
                "phase": "tender",
                "index": i,
                "total": len(tenders),
                "tender": {k: enriched[k] for k in (
                    "id", "title", "cig", "tipologia", "stato", "category",
                    "data_pubblicazione", "data_scadenza", "importo", "detail_url",
                )},
                "documents": enriched.get("documents", []),
                "requirements": enriched.get("requirements", []),
                "chunks": enriched.get("chunks", 0),
            })

        by_category: Dict[str, int] = {}
        for t in results:
            by_category[t["category"]] = by_category.get(t["category"], 0) + 1
        emit({
            "phase": "done",
            "total": len(results),
            "chunks": total_chunks,
            "by_category": by_category,
        })
        return results
