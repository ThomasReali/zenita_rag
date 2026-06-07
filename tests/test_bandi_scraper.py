"""Tests for the Portale Appalti MIT bandi scraper (offline — no network, no embeddings).

Covers: listing-item parsing + normalization, scheda field extraction, detail-page document
link extraction, requirement-bearing document selection, the heuristic requirements
extractor, and the Friendly Captcha proof-of-work solver (with a stubbed puzzle fetch).
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import bandi_scraper as bs


# ── listing parsing ──────────────────────────────────────────────────────────────

_LISTING_HTML = """
<div class="list-summary">La ricerca ha restituito 2 risultati.</div>
<div class="list-item">
    <div class="list-item-row"><label>Stazione appaltante : </label> Provveditorato OO.PP. Lazio </div>
    <div class="list-item-row"><label>Titolo : </label> Messa in sicurezza del Porto di Ponza </div>
    <div class="list-item-row"><label>Tipologia appalto : </label> Servizi </div>
    <div class="list-item-row"><label>Importo : </label> 1.007.223,12 &euro; </div>
    <div class="list-item-row"><label>Data pubblicazione : </label> 21/04/2026 </div>
    <div class="list-item-row"><label>Data scadenza : </label> 12/06/2026 entro le 12:00 </div>
    <div class="list-item-row"><label>Riferimento procedura :</label> G06567 </div>
    <div class="list-item-row"><label>Stato : </label> In corso </div>
    <div class="list-action">
        <a href='https://portaleappalti.mit.gov.it/PortaleAppalti/it/ppgare_bandi_lista.wp?actionPath=/ExtStr2/do/FrontEnd/Bandi/view.action&amp;currentFrame=7&amp;codice=G06567'>Visualizza scheda</a>
    </div>
</div>
<div class="list-item">
    <div class="list-item-row"><label>Titolo : </label> Servizio di manutenzione triennale </div>
    <div class="list-item-row"><label>Stato : </label> In corso </div>
    <div class="list-action">
        <a href='...view.action&amp;currentFrame=7&amp;codice=G06515'>Visualizza scheda</a>
    </div>
</div>
<div class="list-item"></div>
"""


class TestParseListing:
    def test_parses_items_and_core_fields(self):
        items = bs.parse_listing_items(_LISTING_HTML, bs.CATEGORY_IN_CORSO)
        assert [t["codice"] for t in items] == ["G06567", "G06515"]  # empty placeholder skipped
        first = items[0]
        assert first["id"] == "G06567"
        assert first["title"] == "Messa in sicurezza del Porto di Ponza"
        assert first["tipologia"] == "Servizi"
        assert first["importo"] == "1.007.223,12 €"  # &euro; unescaped
        assert first["data_scadenza"].startswith("12/06/2026")
        assert first["servizio"] == "Provveditorato OO.PP. Lazio"
        assert first["category"] == bs.CATEGORY_IN_CORSO
        assert first["detail_url"].endswith("codice=G06567")

    def test_dedupes_repeated_codice(self):
        html = _LISTING_HTML + _LISTING_HTML
        items = bs.parse_listing_items(html, bs.CATEGORY_AGGIUDICAZIONE)
        assert [t["codice"] for t in items] == ["G06567", "G06515"]
        assert all(t["category"] == bs.CATEGORY_AGGIUDICAZIONE for t in items)


# ── scheda parsing + document links ──────────────────────────────────────────────

_SCHEDA_HTML = """
<html><body><main>
    <div class="scheda-row"><label>Denominazione : </label> Direzione generale del personale </div>
    <div class="scheda-row"><label>RUP : </label> Costantini Valentino </div>
    <div class="scheda-row"><label>Criterio di aggiudicazione : </label> Offerta economicamente più vantaggiosa </div>
    <div class="scheda-row"><label>Importo a base di gara : </label> 607.604,46 &euro; </div>
    <div class="scheda-row"><label>CIG : </label> B12345ABCD </div>
    <a href="/PortaleAppalti/do/FrontEnd/DocDig/downloadDocumentoPubblico.action?codice=G06515&amp;id=162832&amp;idprg=">Disciplinare di gara</a>
    <a href="/PortaleAppalti/do/FrontEnd/DocDig/downloadDocumentoPubblico.action?codice=G06515&amp;id=158353&amp;idprg=">Capitolato Speciale d'Appalto</a>
    <a href="https://ted.europa.eu/udl?uri=TED:NOTICE:1-2026">eForm 16 - Bando di gara</a>
    <a href="/PortaleAppalti/do/FrontEnd/DocDig/downloadDocumentoPubblico.action?codice=G06515&amp;id=162832&amp;idprg=">Duplicato disciplinare</a>
</main></body></html>
"""


class TestParseScheda:
    def test_extracts_enrichment_fields(self):
        sched = bs.parse_scheda(_SCHEDA_HTML)
        assert sched["Stazione appaltante"] == "Direzione generale del personale"
        assert sched["RUP"] == "Costantini Valentino"
        assert sched["Importo a base di gara"] == "607.604,46 €"
        assert sched["CIG"] == "B12345ABCD"


class TestFetchTenderDocuments:
    def test_extracts_download_links_resolves_and_dedupes(self):
        docs = bs.fetch_tender_documents(_SCHEDA_HTML)
        urls = [u for u, _ in docs]
        assert all(u.startswith("https://portaleappalti.mit.gov.it/PortaleAppalti/do/FrontEnd/DocDig") for u in urls)
        assert all("&amp;" not in u for u in urls)  # entities decoded
        # the TED external link is ignored; the duplicate download URL is collapsed
        assert len(urls) == len(set(urls)) == 2
        labels = {l for _, l in docs}
        assert "Disciplinare di gara" in labels


class TestSelectRelevantDocuments:
    def test_keeps_requirement_docs_drops_boilerplate_and_caps(self):
        docs = [
            ("/d/1?id=1", "Disciplinare di gara"),
            ("/d/2?id=2", "Capitolato Tecnico e Prestazionale"),
            ("/d/3?id=3", "Domanda di partecipazione"),
            ("/d/4?id=4", "Informativa privacy"),
            ("/d/5?id=5", "Modello di Offerta economica"),
            ("/d/6?id=6", "Bando GUUE"),
        ]
        sel = bs.select_relevant_documents(docs)
        labels = [l for _, l in sel]
        assert len(sel) <= bs._MAX_DOCS_PER_TENDER
        assert "Disciplinare di gara" in labels
        assert "Capitolato Tecnico e Prestazionale" in labels
        assert "Informativa privacy" not in labels
        assert labels.index("Disciplinare di gara") < labels.index("Bando GUUE")

    def test_fallback_when_nothing_matches(self):
        docs = [("/d/1?id=1", "Allegato A"), ("/d/2?id=2", "Mappa")]
        assert bs.select_relevant_documents(docs) == docs[:2]

    def test_empty_input(self):
        assert bs.select_relevant_documents([]) == []


# ── requirements extraction ──────────────────────────────────────────────────────

_DISCIPLINARE = """
SOMMARIO
6. REQUISITI DI ORDINE GENERALE ................................................ 12
7. REQUISITI DI CAPACITA' ECONOMICA ........................................... 15

6. REQUISITI DI ORDINE GENERALE E ALTRE CAUSE DI ESCLUSIONE
I concorrenti devono essere in possesso, a pena di esclusione, dei requisiti di ordine generale.
E' richiesta l'iscrizione nel registro della Camera di Commercio.

7. REQUISITI DI CAPACITA' ECONOMICA E FINANZIARIA
Il concorrente deve aver realizzato un fatturato minimo annuo non inferiore a 500.000 euro.
"""


class TestRequirements:
    def test_extracts_real_content_not_toc(self):
        reqs = bs.extract_requirements(_DISCIPLINARE)
        joined = " ".join(reqs).lower()
        assert reqs
        assert not any("....." in r for r in reqs)
        assert "fatturato" in joined
        assert "iscrizione" in joined or "camera di commercio" in joined

    def test_empty_text_returns_empty_list(self):
        assert bs.extract_requirements("") == []

    def test_caps_at_max(self):
        big = "\n".join(f"Il concorrente deve possedere il requisito numero {i}." for i in range(50))
        assert len(bs.extract_requirements(big)) <= bs._MAX_REQUIREMENTS


# ── Friendly Captcha proof-of-work solver ────────────────────────────────────────

def _make_easy_puzzle(n: int = 2) -> str:
    """Build a signed-looking puzzle with the lowest difficulty (threshold ≈ 2**32)."""
    buffer = bytearray(32)
    buffer[13] = 12   # expiry minutes
    buffer[14] = n    # number of sub-puzzles
    buffer[15] = 0    # difficulty 0 → threshold ~ 2**32 (solves on the first nonce)
    return "SIGNATURE." + base64.b64encode(bytes(buffer)).decode()


class TestCaptchaSolver:
    def test_decode_puzzle_reads_n_and_threshold(self):
        sig, b64, buf, n, threshold = bs._decode_puzzle(_make_easy_puzzle(3))
        assert sig == "SIGNATURE"
        assert n == 3
        assert len(buf) == 32
        assert threshold > 0

    def test_solve_subpuzzle_finds_nonce_with_easy_threshold(self):
        solver_input = bytearray(128)
        assert bs._solve_subpuzzle(solver_input, threshold=2 ** 32) is True

    def test_solve_friendly_captcha_assembles_solution(self, monkeypatch):
        n = 2
        puzzle = _make_easy_puzzle(n)

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"data": {"puzzle": puzzle}}

        monkeypatch.setattr(bs.requests, "get", lambda *a, **k: _Resp())
        solution = bs.solve_friendly_captcha(sitekey="X", puzzle_endpoint="https://x/puzzle")

        parts = solution.split(".")
        assert len(parts) == 4
        sig, b64, sols_b64, diag_b64 = parts
        assert sig == "SIGNATURE"
        # solutions buffer is 8 bytes per sub-puzzle
        assert len(base64.b64decode(sols_b64)) == 8 * n
