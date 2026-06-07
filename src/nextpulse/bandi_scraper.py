"""Portale Appalti MIT bandi/gare scraper + RAG ingestion.

Scrapes the **Ministero delle Infrastrutture e dei Trasporti** e-procurement portal
(https://portaleappalti.mit.gov.it — Maggioli "PortaleAppalti" / Struts2 platform) and
indexes the live tenders into a dedicated Qdrant collection for the gare chatbot.

Pipeline (driven by :meth:`PortaleAppaltiScraper.ingest`):

  1. Bootstrap a session and clear the portal's **Friendly Captcha** gate (a proof-of-work
     puzzle solved locally with BLAKE2b — see :func:`solve_friendly_captcha`). Without it
     the tender *schede* (detail pages) render empty.
  2. Fetch the tender listing for the two requested business states — ``stato=1`` (In corso)
     and ``stato=2`` (In aggiudicazione) — paginating ``listAllBandi.action``.
  3. Open each tender's *scheda* (``view.action``) and scrape its public document links
     (``downloadDocumentoPubblico.action``: disciplinare, capitolato, bando, esiti …).
  4. Download + token-chunk the requirement-bearing PDFs with the shared
     :class:`DocumentProcessor`.
  5. Heuristically extract the *requisiti di partecipazione* of each bando and synthesise a
     retrievable "scheda" + "requisiti" chunk.
  6. Index everything into the dedicated Qdrant collection so the bandi chatbot answers
     grounded ONLY on the scraped gare.

The portal is in Italian only: the session **must** send ``Accept-Language: it`` or the
scheda content is not rendered.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import struct
import time
from html import unescape
from typing import Callable, Dict, List, Optional, Tuple

import requests

from src.nextpulse.document_processor import DocumentProcessor
from src.nextpulse.vector_store import VectorStore

logger = logging.getLogger("nextpulse.bandi_scraper")

# ── portal endpoints ─────────────────────────────────────────────────────────────
BASE_URL = "https://portaleappalti.mit.gov.it/PortaleAppalti"
LIST_PAGE = "/it/ppgare_bandi_lista.wp"
# The list/detail showlets are invoked through the .wp wrapper via `actionPath`.
LIST_ACTION = LIST_PAGE + "?actionPath=/ExtStr2/do/FrontEnd/Bandi/listAllBandi.action&currentFrame=7"
VIEW_ACTION = LIST_PAGE + "?actionPath=/ExtStr2/do/FrontEnd/Bandi/view.action&currentFrame=7&codice={codice}"
ALLOW_CAPTCHA = "/do/allowCaptcha.action"

BANDI_COLLECTION = "bandi_mit"        # dedicated Qdrant collection (separate from the KB)

# Friendly Captcha (Maggioli self-hosted, proof-of-work). Parsed from the page when
# possible; these are the fallback defaults observed on the portal.
CAPTCHA_PUZZLE_ENDPOINT = "https://apis.maggioli.cloud/rest/captcha/v2/puzzle"
CAPTCHA_SITEKEY = "FCMV995O03V7RIMQ"

# Portal `model.stato` filter values → business category.
STATO_IN_CORSO = "1"
STATO_AGGIUDICAZIONE = "2"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    # The scheda is rendered only for the Italian locale.
    "Accept-Language": "it-IT,it;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Be a polite scraper: small pause between detail-page / PDF requests.
_REQUEST_PAUSE_S = 0.25
# How many tenders to request per listing page (portal allows up to 100).
_PAGE_SIZE = 50
# Newest first, so a capped run indexes the most recent (most relevant) tenders.
_ORDER_CRITERIA = "DATA_PUBBLICAZIONE_DESC"
# The "In aggiudicazione" state alone holds hundreds of tenders; downloading every PDF for
# all of them is impractical and abusive. Cap how many tenders per category we ingest by
# default (override via ``ingest(max_per_category=…)``). ``None`` means no cap.
_MAX_TENDERS_PER_CATEGORY = 60

# ── business categories ──────────────────────────────────────────────────────────
CATEGORY_IN_CORSO = "in_corso"
CATEGORY_AGGIUDICAZIONE = "aggiudicazione"
CATEGORY_LABELS = {
    CATEGORY_IN_CORSO: "Bandi in corso",
    CATEGORY_AGGIUDICAZIONE: "Bandi in aggiudicazione",
}
_STATO_TO_CATEGORY = {
    STATO_IN_CORSO: CATEGORY_IN_CORSO,
    STATO_AGGIUDICAZIONE: CATEGORY_AGGIUDICAZIONE,
}

# ── document selection (portal-agnostic) ─────────────────────────────────────────
# A tender attaches many boilerplate forms (DGUE, privacy, modello offerta…) that carry no
# participation requirements. Keep only the requirement-bearing documents, ranked, capped.
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


# ── requirements extraction (portal-agnostic) ────────────────────────────────────
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


# ── Friendly Captcha proof-of-work solver ────────────────────────────────────────
# The portal gates tender schede behind a Friendly-Captcha-style PoW: the client fetches a
# signed puzzle, finds nonces whose BLAKE2b-256 digest (first 4 bytes, little-endian) falls
# below a difficulty threshold, and submits them. This is solvable head-less — it is a
# rate-limiter, not a human check.

def _decode_puzzle(puzzle: str) -> Tuple[str, str, bytes, int, int]:
    """Decode a Friendly Captcha puzzle string into (signature, base64, buffer, n, threshold)."""
    signature, b64 = puzzle.split(".", 1)
    buffer = base64.b64decode(b64)
    n = buffer[14]
    diff = max(0, min(255, buffer[15]))
    threshold = int(2 ** ((255.999 - diff) / 8))
    return signature, b64, buffer, n, threshold


def _solve_subpuzzle(solver_input: bytearray, threshold: int) -> bool:
    """Brute-force the 8-byte nonce of one sub-puzzle in place; True when solved.

    Mirrors the widget's solver: byte 123 is bumped by the outer loop, bytes 124..127 hold a
    little-endian uint32 counter; the digest's first 32-bit word (LE) must be < threshold.
    """
    for outer in range(256):
        solver_input[123] = outer
        for counter in range(0x100000000):
            struct.pack_into("<I", solver_input, 124, counter)
            digest = hashlib.blake2b(bytes(solver_input), digest_size=32).digest()
            if int.from_bytes(digest[:4], "little") < threshold:
                return True
    return False


def solve_friendly_captcha(
    sitekey: str = CAPTCHA_SITEKEY,
    puzzle_endpoint: str = CAPTCHA_PUZZLE_ENDPOINT,
    *,
    timeout: int = 30,
) -> str:
    """Fetch a puzzle for ``sitekey`` and return the ``frc-captcha-solution`` string."""
    resp = requests.get(
        f"{puzzle_endpoint}?sitekey={sitekey}",
        headers={"x-frc-client": "js-0.9.1", "User-Agent": HEADERS["User-Agent"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    puzzle = resp.json()["data"]["puzzle"]
    signature, b64, buffer, n, threshold = _decode_puzzle(puzzle)

    solutions = bytearray()
    for idx in range(n):
        solver_input = bytearray(128)
        solver_input[: len(buffer)] = buffer
        solver_input[120] = idx  # sub-puzzle index
        if not _solve_subpuzzle(solver_input, threshold):
            raise RuntimeError(f"Friendly Captcha sub-puzzle {idx}/{n} unsolved")
        solutions += solver_input[120:128]  # the 8-byte nonce

    diagnostics = bytes([1, 0, 0])  # solverType=JS, time=0 (not validated server-side)
    return (
        f"{signature}.{b64}."
        f"{base64.b64encode(bytes(solutions)).decode()}."
        f"{base64.b64encode(diagnostics).decode()}"
    )


# ── HTML parsing helpers ─────────────────────────────────────────────────────────
_TAG_STRIP = re.compile(r"<[^>]+>")
_CSRF_RE = re.compile(r'name="_csrf"\s+value="([^"]+)"')
_SITEKEY_RE = re.compile(r'data-sitekey="([^"]+)"')
_PUZZLE_EP_RE = re.compile(r'data-puzzle-?endpoint="([^"]+)"', re.I)
_TOTAL_RE = re.compile(r"ha restituito\s*(\d+)\s*risultati", re.I)
_CODICE_RE = re.compile(r"codice=(G\d+)")
_DOC_ANCHOR = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']*downloadDocumentoPubblico\.action[^"\']*)["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_DOC_ID_RE = re.compile(r"[?&]id=(\d+)")


def _clean(fragment: str) -> str:
    """Strip tags, unescape entities and collapse whitespace from an HTML fragment."""
    return re.sub(r"\s+", " ", unescape(_TAG_STRIP.sub(" ", fragment))).strip()


def _abs_url(href: str) -> str:
    href = href.replace("&amp;", "&")
    if href.startswith("http"):
        return href
    if href.startswith("/PortaleAppalti"):
        return "https://portaleappalti.mit.gov.it" + href
    return BASE_URL + ("" if href.startswith("/") else "/") + href


def _main_region(html: str) -> str:
    """Return the <main> content region (where the scheda/list is rendered)."""
    start = html.find("<main")
    end = html.find("</main>")
    return html[start:end] if start != -1 and end != -1 else html


def _field(block: str, label: str) -> str:
    """Extract the value following ``<label>{label} :</label>`` inside a block.

    Each field value sits between its ``<label>`` and the next block boundary
    (``</div>``/``</dd>``/``</td>`` or the next ``<label>``/anchor).
    """
    m = re.search(
        r"<label[^>]*>\s*" + re.escape(label) + r"\s*:?\s*</label>(.*?)"
        r"(?=<label|<a\b|</div>|</dd>|</td>)",
        block, re.S | re.I,
    )
    return _clean(m.group(1)) if m else ""


def parse_listing_items(html: str, category: str) -> List[dict]:
    """Parse the ``listAllBandi`` HTML into normalized tender dicts (one per ``list-item``)."""
    items: List[dict] = []
    seen: set = set()
    # Each tender is a `<div class="list-item">…`; split on the marker and parse each chunk.
    for chunk in html.split('<div class="list-item">')[1:]:
        codice_m = _CODICE_RE.search(chunk)
        if not codice_m:
            continue  # empty placeholder list-item
        codice = codice_m.group(1)
        if codice in seen:
            continue
        seen.add(codice)
        items.append(_normalize_tender(chunk, codice, category))
    return items


def _normalize_tender(block: str, codice: str, category: str) -> dict:
    """Project a listing ``list-item`` block onto the fields the app cares about."""
    return {
        "id": codice,
        "codice": codice,
        "title": _field(block, "Titolo"),
        "cig": "",  # not in the listing; filled from the scheda when available
        "tipologia": _field(block, "Tipologia appalto"),
        "stato": _field(block, "Stato") or CATEGORY_LABELS.get(category, ""),
        "category": category,
        "data_pubblicazione": _field(block, "Data pubblicazione"),
        "data_scadenza": _field(block, "Data scadenza"),
        "importo": _field(block, "Importo"),
        "servizio": _field(block, "Stazione appaltante"),
        "detail_url": _abs_url(VIEW_ACTION.format(codice=codice)),
    }


# Scheda fields worth surfacing in a synthetic, retrievable "scheda" chunk.
_SCHEDA_FIELDS = [
    ("Stazione appaltante", "Denominazione"),
    ("RUP", "RUP"),
    ("Procedura di gara", "Procedura di gara"),
    ("Criterio di aggiudicazione", "Criterio di aggiudicazione"),
    ("Importo a base di gara", "Importo a base di gara"),
    ("CIG", "CIG"),
]


def parse_scheda(html: str) -> dict:
    """Extract enrichment fields (CIG, RUP, stazione appaltante…) from a scheda page."""
    main = _main_region(html)
    out: dict = {}
    for key, label in _SCHEDA_FIELDS:
        val = _field(main, label)
        if val:
            out[key] = val
    return out


def fetch_tender_documents(html: str) -> List[Tuple[str, str]]:
    """Return ``[(absolute_pdf_url, label), …]`` for a scheda page's public documents."""
    main = _main_region(html)
    docs: List[Tuple[str, str]] = []
    seen: set = set()
    for href, inner in _DOC_ANCHOR.findall(main):
        url = _abs_url(href)
        if url in seen:
            continue
        seen.add(url)
        label = _clean(inner) or url.rsplit("/", 1)[-1]
        docs.append((url, label))
    return docs


# ── ingestion pipeline ───────────────────────────────────────────────────────────

ProgressCallback = Callable[[dict], None]


class PortaleAppaltiScraper:
    """Scrape Portale Appalti MIT bandi end-to-end and index them into Qdrant."""

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
        self._csrf: Optional[str] = None

    # -- session / captcha ---------------------------------------------------------

    def _bootstrap(self) -> None:
        """Load the list page, then clear the Friendly Captcha gate if present."""
        resp = self.session.get(BASE_URL + LIST_PAGE, timeout=30)
        resp.raise_for_status()
        self._csrf = self._extract_csrf(resp.text)

        if "frc-captcha" not in resp.text:
            return  # already unlocked (session cookie still valid)

        sitekey_m = _SITEKEY_RE.search(resp.text)
        endpoint_m = _PUZZLE_EP_RE.search(resp.text)
        solution = solve_friendly_captcha(
            sitekey=sitekey_m.group(1) if sitekey_m else CAPTCHA_SITEKEY,
            puzzle_endpoint=endpoint_m.group(1) if endpoint_m else CAPTCHA_PUZZLE_ENDPOINT,
        )
        self.session.post(
            BASE_URL + ALLOW_CAPTCHA,
            data={"_csrf": self._csrf or "", "frc-captcha-solution": solution},
            headers={"Referer": BASE_URL + LIST_PAGE},
            timeout=30,
        ).raise_for_status()

        # Refresh the CSRF token from the now-unlocked page.
        resp = self.session.get(BASE_URL + LIST_PAGE, timeout=30)
        self._csrf = self._extract_csrf(resp.text)

    @staticmethod
    def _extract_csrf(html: str) -> Optional[str]:
        m = _CSRF_RE.search(html)
        return m.group(1) if m else None

    # -- listing -------------------------------------------------------------------

    def fetch_listing(self, stato: str, max_tenders: Optional[int] = None) -> List[dict]:
        """Fetch tenders for a ``model.stato`` value, following pagination up to ``max_tenders``.

        The portal applies a search only when the **full filter form** is posted *without*
        the ``last`` flag (page 1); subsequent pages reuse that session-held search via
        ``last=1`` + ``model.currentPage``.
        """
        category = _STATO_TO_CATEGORY.get(stato, CATEGORY_AGGIUDICAZIONE)
        tenders: List[dict] = []
        total: Optional[int] = None
        page = 1
        while True:
            if page == 1:
                data = {
                    "_csrf": self._csrf or "",
                    "model.stazioneAppaltante": "", "model.oggetto": "", "model.cig": "",
                    "model.stato": stato,
                    "model.orderCriteria": _ORDER_CRITERIA,
                    "model.tipoAppalto": "",
                    "model.dataPubblicazioneDa": "", "model.dataPubblicazioneA": "",
                    "model.dataScadenzaDa": "", "model.dataScadenzaA": "",
                    "model.codice": "",
                    "model.sommaUrgenza": "", "model.isGreen": "",
                    "model.isRecycle": "", "model.isPnrr": "",
                    "model.iDisplayLength": str(_PAGE_SIZE),
                }
            else:
                # Pagination reuses the session-held search; it resets the page size to the
                # default (10) unless ``iDisplayLength`` is re-sent.
                data = {
                    "_csrf": self._csrf or "", "last": "1",
                    "model.currentPage": str(page),
                    "model.iDisplayLength": str(_PAGE_SIZE),
                }
            resp = self.session.post(
                BASE_URL + LIST_ACTION, data=data,
                headers={"Referer": BASE_URL + LIST_PAGE}, timeout=30,
            )
            resp.raise_for_status()
            if total is None:
                tm = _TOTAL_RE.search(resp.text)
                total = int(tm.group(1)) if tm else None
            page_items = parse_listing_items(resp.text, category)
            if not page_items:
                break
            tenders.extend(page_items)
            if max_tenders is not None and len(tenders) >= max_tenders:
                return tenders[:max_tenders]
            if (total is not None and len(tenders) >= total) or len(page_items) < _PAGE_SIZE:
                break
            page += 1
            time.sleep(_REQUEST_PAUSE_S)
        return tenders

    # -- per-tender ----------------------------------------------------------------

    def _index_tender(self, tender: dict) -> dict:
        """Open the scheda, download + index its PDFs, extract requirements."""
        codice = tender["codice"]
        resp = self.session.get(
            BASE_URL + VIEW_ACTION.format(codice=codice),
            headers={"Referer": BASE_URL + LIST_PAGE}, timeout=30,
        )
        resp.raise_for_status()
        scheda = parse_scheda(resp.text)
        if scheda.get("CIG"):
            tender["cig"] = scheda["CIG"]

        documents = select_relevant_documents(fetch_tender_documents(resp.text))

        all_chunks: List[Tuple[str, dict]] = []
        full_text_parts: List[str] = []
        indexed_docs: List[dict] = []

        for url, label in documents:
            time.sleep(_REQUEST_PAUSE_S)
            try:
                r = self.session.get(url, headers={"Referer": tender["detail_url"]}, timeout=60)
                r.raise_for_status()
                raw = r.content
            except Exception as exc:  # noqa: BLE001 — one bad PDF must not abort the bando
                logger.warning("Download failed for %s (%s)", url, exc)
                continue
            if not raw[:5].startswith(b"%PDF"):
                logger.debug("Skipping non-PDF document %s", url)
                continue

            doc_id_m = _DOC_ID_RE.search(url)
            source = f"bando_{codice}_doc{doc_id_m.group(1) if doc_id_m else label[:20]}"
            base_meta = {
                "source": source,
                "doc_type": "pdf",
                "category": "bando",
                "gara_category": tender["category"],
                "tender_id": codice,
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

        # A synthetic, retrievable "scheda" chunk so the chatbot can surface tender
        # metadata (stazione appaltante, RUP, importo, scadenza…) even without readable PDFs.
        scheda_lines = [f"{k}: {v}" for k, v in scheda.items()]
        scheda_text = (
            f"Scheda gara «{tender['title']}» (rif. {codice}, {tender['stato']}).\n"
            f"Stazione appaltante: {tender['servizio']}.\n"
            f"Tipologia: {tender['tipologia']}. Importo: {tender['importo']}.\n"
            f"Data pubblicazione: {tender['data_pubblicazione']}. "
            f"Data scadenza: {tender['data_scadenza']}.\n"
            + "\n".join(scheda_lines)
        )
        all_chunks.append((scheda_text, self._meta(tender, "scheda", "Scheda gara")))

        if requirements:
            req_text = (
                f"Requisiti di partecipazione del bando «{tender['title']}» "
                f"(rif. {codice}, CIG {tender['cig'] or 'n/d'}):\n- " + "\n- ".join(requirements)
            )
            all_chunks.append((req_text, self._meta(tender, "requisiti", "Requisiti (estratti)")))

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

    def _meta(self, tender: dict, kind: str, label: str) -> dict:
        """Metadata for a synthetic (non-PDF) chunk tied to a tender."""
        return {
            "source": f"bando_{tender['codice']}_{kind}",
            "doc_type": "txt",
            "category": "bando",
            "gara_category": tender["category"],
            "tender_id": tender["codice"],
            "tender_title": tender["title"],
            "cig": tender["cig"],
            "stato": tender["stato"] or "",
            "doc_label": label,
            "source_url": tender["detail_url"],
            "chunk_id": 0,
        }

    # -- driver --------------------------------------------------------------------

    def ingest(
        self,
        progress: Optional[ProgressCallback] = None,
        max_per_category: Optional[int] = _MAX_TENDERS_PER_CATEGORY,
    ) -> List[dict]:
        """Run the full scrape→index pipeline, emitting progress events.

        ``max_per_category`` caps how many (most-recent) tenders are ingested per business
        state — the portal's "In aggiudicazione" alone holds hundreds. ``None`` ingests all.

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

        self._bootstrap()

        tenders: List[dict] = []
        for stato in (STATO_IN_CORSO, STATO_AGGIUDICAZIONE):
            try:
                tenders.extend(self.fetch_listing(stato, max_tenders=max_per_category))
            except Exception as exc:  # noqa: BLE001 — one empty/broken state must not abort
                logger.error("Listing failed for stato=%s: %s", stato, exc, exc_info=True)
        tenders = [t for t in tenders if t["id"] and t["title"]]
        emit({"phase": "listing", "total": len(tenders)})

        results: List[dict] = []
        total_chunks = 0
        for i, tender in enumerate(tenders, start=1):
            try:
                enriched = self._index_tender(tender)
            except Exception as exc:  # noqa: BLE001 — robustness: skip the broken bando
                logger.error("Failed to ingest tender %s: %s", tender["id"], exc, exc_info=True)
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
