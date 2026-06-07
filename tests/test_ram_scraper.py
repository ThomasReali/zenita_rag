"""Tests for the R.A.M. bandi scraper (offline — no network, no embedding model).

Covers: status→category bucketing, listing record normalization, detail-page document
link extraction, and the heuristic requirements extractor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import ram_scraper as rs


class _FakeResp:
    def __init__(self, *, json_data=None, text=""):
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeSession:
    """Minimal requests.Session stand-in: routes GETs by URL substring."""

    def __init__(self, listing=None, detail_html=""):
        self.listing = listing or {"data": []}
        self.detail_html = detail_html
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        if rs.LISTING_WS in url:
            return _FakeResp(json_data=self.listing)
        return _FakeResp(text=self.detail_html)


# ── categorize_stato ─────────────────────────────────────────────────────────────

class TestCategorize:
    def test_open_states_map_to_in_corso(self):
        for s in ("in_corso", "scaduta", "in_svolgimento", "IN_CORSO"):
            assert rs.categorize_stato(s) == rs.CATEGORY_IN_CORSO

    def test_awarded_states_map_to_aggiudicazione(self):
        for s in ("aggiudicata", "conclusa", "conclusa_affidata", "chiusa", "annullata"):
            assert rs.categorize_stato(s) == rs.CATEGORY_AGGIUDICAZIONE

    def test_unknown_and_empty_default_to_aggiudicazione(self):
        assert rs.categorize_stato("") == rs.CATEGORY_AGGIUDICAZIONE
        assert rs.categorize_stato(None) == rs.CATEGORY_AGGIUDICAZIONE
        # but an unknown state that "sounds open" stays in corso
        assert rs.categorize_stato("gara in corso di valutazione") == rs.CATEGORY_IN_CORSO


# ── normalize_tender ─────────────────────────────────────────────────────────────

class TestNormalize:
    def test_maps_core_fields_and_absolute_detail_url(self):
        raw = {
            "id": 74,
            "title": "PROCEDURA APERTA per servizio X",
            "cig_number": "B12345",
            "tipologia": "Procedura aperta",
            "stato": "aggiudicata",
            "data_pubblicazione": "18-05-2024",
            "data_scadenza": "20-06-2024",
            "url_tender": "/tender/74",
        }
        t = rs.normalize_tender(raw)
        assert t["id"] == "74"
        assert t["cig"] == "B12345"
        assert t["category"] == rs.CATEGORY_AGGIUDICAZIONE
        assert t["detail_url"] == rs.BASE_URL + "/tender/74"

    def test_falls_back_to_oggetto_and_synthesizes_detail_url(self):
        t = rs.normalize_tender({"id": "9", "oggetto": "Affidamento Y", "stato": "in_corso"})
        assert t["title"] == "Affidamento Y"
        assert t["detail_url"] == rs.BASE_URL + "/tender/9"
        assert t["category"] == rs.CATEGORY_IN_CORSO


# ── fetch_listing ────────────────────────────────────────────────────────────────

def test_fetch_listing_returns_data_array():
    sess = _FakeSession(listing={"data": [{"id": "1"}, {"id": "2"}], "meta": {}})
    out = rs.fetch_listing(sess)
    assert [r["id"] for r in out] == ["1", "2"]


# ── fetch_tender_documents ───────────────────────────────────────────────────────

_DETAIL_HTML = """
<html><body>
  <a href="/tender/documenti/269/Disciplinare-di-gara.pdf">Disciplinare di gara</a>
  <a href="/tender/documenti/270/All.-1_Capitolato.pdf">Allegato 1 - Capitolato</a>
  <a href="/tender/documenti/278/edgue_request_74.xml">eForms XML</a>
  <a href="/tender/award-document/100/download">Verbale di aggiudicazione</a>
  <a href="/altro/non-pertinente">Pagina informativa</a>
  <a href="/tender/documenti/270/All.-1_Capitolato.pdf">Duplicato</a>
</body></html>
"""


class TestFetchDocuments:
    def test_extracts_pdf_and_award_links_skips_xml_and_dupes(self):
        sess = _FakeSession(detail_html=_DETAIL_HTML)
        docs = sess and rs.fetch_tender_documents(sess, rs.BASE_URL + "/tender/74")
        urls = [u for u, _ in docs]
        # xml skipped, duplicate collapsed, unrelated link ignored
        assert any("Disciplinare" in u for u in urls)
        assert any("Capitolato" in u for u in urls)
        assert any("award-document/100" in u for u in urls)
        assert not any(u.endswith(".xml") for u in urls)
        assert len(urls) == len(set(urls)) == 3

    def test_labels_are_cleaned(self):
        sess = _FakeSession(detail_html=_DETAIL_HTML)
        docs = rs.fetch_tender_documents(sess, rs.BASE_URL + "/tender/74")
        labels = {l for _, l in docs}
        assert "Disciplinare di gara" in labels


class TestSelectRelevantDocuments:
    def test_keeps_requirement_docs_drops_boilerplate_and_caps(self):
        docs = [
            ("/t/1/Disciplinare-di-gara.pdf", "Disciplinare di gara"),
            ("/t/2/Capitolato-Tecnico.pdf", "Capitolato Tecnico e Prestazionale"),
            ("/t/3/All-4-Domanda-di-partecipazione.pdf", "Domanda di partecipazione"),
            ("/t/4/Informativa-privacy.pdf", "Informativa privacy"),
            ("/t/5/Modello-Offerta-economica.pdf", "Modello di Offerta economica"),
            ("/t/6/Bando-GUUE.pdf", "Bando GUUE"),
            ("/t/7/Dichiarazione-DPCM.pdf", "Dichiarazione DPCM"),
        ]
        sel = rs.select_relevant_documents(docs)
        labels = [l for _, l in sel]
        assert len(sel) <= rs._MAX_DOCS_PER_TENDER
        assert "Disciplinare di gara" in labels
        assert "Capitolato Tecnico e Prestazionale" in labels
        assert "Informativa privacy" not in labels
        assert "Domanda di partecipazione" not in labels
        # disciplinare ranks before bando
        assert labels.index("Disciplinare di gara") < labels.index("Bando GUUE")

    def test_fallback_when_nothing_matches(self):
        docs = [("/t/1/Allegato.pdf", "Allegato A"), ("/t/2/Mappa.pdf", "Mappa")]
        sel = rs.select_relevant_documents(docs)
        assert sel == docs[:2]

    def test_empty_input(self):
        assert rs.select_relevant_documents([]) == []


# ── extract_requirements ─────────────────────────────────────────────────────────

_DISCIPLINARE = """
SOMMARIO
6. REQUISITI DI ORDINE GENERALE ................................................ 12
7. REQUISITI DI CAPACITA' ECONOMICA ........................................... 15

6. REQUISITI DI ORDINE GENERALE E ALTRE CAUSE DI ESCLUSIONE
I concorrenti devono essere in possesso, a pena di esclusione, dei requisiti di ordine generale.
E' richiesta l'iscrizione nel registro della Camera di Commercio.

7. REQUISITI DI CAPACITA' ECONOMICA E FINANZIARIA
Il concorrente deve aver realizzato un fatturato minimo annuo non inferiore a 500.000 euro.
Devono possedere idonea referenza bancaria.

8. AVVALIMENTO
Disposizioni generali non pertinenti ai requisiti.
"""


class TestRequirements:
    def test_extracts_real_content_not_toc(self):
        reqs = rs.extract_requirements(_DISCIPLINARE)
        joined = " ".join(reqs).lower()
        assert reqs
        # table-of-contents dotted lines must be filtered out
        assert not any("....." in r for r in reqs)
        # actual requirement sentences are captured
        assert "fatturato" in joined
        assert "iscrizione" in joined or "camera di commercio" in joined

    def test_empty_text_returns_empty_list(self):
        assert rs.extract_requirements("") == []

    def test_caps_at_max(self):
        big = "\n".join(f"Il concorrente deve possedere il requisito numero {i}." for i in range(50))
        assert len(rs.extract_requirements(big)) <= rs._MAX_REQUIREMENTS
