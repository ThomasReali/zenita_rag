"""Tests for the opt-in OCR fallback (scanned PDFs).

The OCR stack (pymupdf/pytesseract/PIL) and the Tesseract binary are NOT installed in CI;
fakes are injected into sys.modules so the logic is exercised offline.
"""
import sys
import types

import pytest

from src.nextpulse import config
from src.nextpulse.document_processor import DocumentProcessor


def _install_fake_ocr(monkeypatch, ocr_text="Testo OCR estratto in italiano."):
    # fake pytesseract
    pt = types.ModuleType("pytesseract")
    pt.image_to_string = lambda img, lang=None: ocr_text
    pt.pytesseract = types.SimpleNamespace(tesseract_cmd="")
    monkeypatch.setitem(sys.modules, "pytesseract", pt)

    # fake PIL.Image
    pil = types.ModuleType("PIL")
    image = types.ModuleType("PIL.Image")
    image.open = lambda buf: object()
    pil.Image = image
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image)

    # fake fitz (PyMuPDF)
    class _Pix:
        def tobytes(self, fmt):
            return b"PNG"

    class _Page:
        def get_pixmap(self, dpi=None):
            return _Pix()

    class _Doc:
        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fitz = types.ModuleType("fitz")
    fitz.open = lambda path: _Doc()
    monkeypatch.setitem(sys.modules, "fitz", fitz)


class TestOcrFill:
    def test_fills_only_weak_pages(self, monkeypatch):
        _install_fake_ocr(monkeypatch)
        monkeypatch.setattr(config, "OCR_PAGE_MIN_CHARS", 100)
        dp = DocumentProcessor()
        pages = [(1, "testo digitale " * 20), (2, "")]  # page 2 is a scan (empty)
        out = dp._ocr_fill("scansione.pdf", pages)
        assert out[0][1].startswith("testo digitale")        # digital page untouched
        assert "OCR estratto" in out[1][1]                   # scanned page filled by OCR
        assert out[1][0] == 2                                # page number preserved

    def test_no_weak_pages_is_noop(self, monkeypatch):
        _install_fake_ocr(monkeypatch)
        monkeypatch.setattr(config, "OCR_PAGE_MIN_CHARS", 5)
        dp = DocumentProcessor()
        pages = [(1, "abbastanza testo qui")]
        assert dp._ocr_fill("x.pdf", pages) == pages

    def test_graceful_when_deps_missing(self, monkeypatch):
        # Ensure the OCR imports fail → original (empty) pages returned unchanged.
        for mod in ("pytesseract", "fitz", "pymupdf"):
            monkeypatch.setitem(sys.modules, mod, None)
        monkeypatch.setattr(config, "OCR_PAGE_MIN_CHARS", 100)
        dp = DocumentProcessor()
        pages = [(1, "")]
        assert dp._ocr_fill("x.pdf", pages) == pages         # no crash, no fill
