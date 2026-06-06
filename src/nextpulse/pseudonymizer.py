"""Reversible Pseudonymization (two-way tokenization) — GDPR Art. 32.

A LOCAL "buffer" layer that sits between the Vector DB and the external LLM
(OpenRouter). It masks PII before any text leaves the machine and restores it
on the way back, so the provider performs zero-knowledge processing:

    1. Detection  — a local NLP/regex engine finds PII in the retrieved chunks
                    (and in the user question): names, e-mail, IBAN, codice
                    fiscale, P.IVA, CIG/CUP, importi €, marginalità %, Comuni…
    2. Masking    — each value is replaced by a traceable placeholder token
                    ("[PERSON_1]", "[ORG_1]", …); the original↔token map lives
                    only in memory, for the duration of the request.
    3. External   — the masked prompt is sent to OpenRouter; the LLM manipulates
                    tokens, never the real data.
    4. De-masking — the response is re-identified locally from the in-memory map,
                    which is then destroyed.

Backends:
    • RegexDetector    — always available (stdlib only), covers the structured
                         PII above (incl. Engine-specific CIG/CUP/margins).
    • PresidioDetector — optional; if `presidio-analyzer` + a spaCy model are
                         installed it adds NER for PERSON/ORG/LOCATION. Selected
                         automatically when `PII_BACKEND=auto` (the default).

The map is per-request and ephemeral: `MaskingSession.close()` (or the context
manager) wipes it. Masking never raises into the pipeline — on any detector
error a chunk is sent through unchanged is avoided by failing closed at the
call site; here detection failures simply yield no spans.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.nextpulse import config

logger = logging.getLogger("nextpulse.pii")


@dataclass(frozen=True)
class PIISpan:
    start: int
    end: int
    entity: str
    value: str


# ── Regex recognizers (always available) ──────────────────────────────────────
@dataclass(frozen=True)
class _Rule:
    entity: str
    pattern: re.Pattern
    group: int = 0


_U = "A-ZÀ-Ý"   # uppercase incl. Italian accents
_L = "a-zà-ÿ"   # lowercase incl. Italian accents

_DEFAULT_RULES: List[_Rule] = [
    _Rule("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    _Rule("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    _Rule("CREDIT_CARD", re.compile(r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{1,4}\b")),
    _Rule("FISCAL_CODE", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b")),
    _Rule("VAT", re.compile(r"(?i)\b(?:partita\s+iva|p\.?\s?iva|vat)[\s:]*((?:IT)?\d{11})\b"), 1),
    _Rule("VAT", re.compile(r"\bIT\d{11}\b")),
    _Rule("CIG", re.compile(r"(?i)\bcig[\s:n.°]*([0-9A-Z]{10})\b"), 1),
    _Rule("CUP", re.compile(r"(?i)\bcup[\s:n.°]*([0-9A-Z]{15})\b"), 1),
    _Rule("PHONE", re.compile(r"(?i)\b(?:tel\.?|telefono|cell\.?|cellulare|mobile|fax)[\s:]*(\+?\d[\d\s.\-]{6,}\d)"), 1),
    _Rule("PHONE", re.compile(r"\+39\s?\d[\d\s.\-]{6,}\d")),
    _Rule("MONEY", re.compile(r"(?i)(?:€\s?\d[\d.,]*|\beur\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:€|eur|euro)\b)")),
    _Rule("PERCENT", re.compile(r"\b\d{1,3}(?:[.,]\d+)?\s?%")),
    _Rule("ORG", re.compile(rf"(?i)\b(?:comune|provincia|citt[àa]|regione)\s+di\s+[{_U}][{_L}'’]+(?:\s+[{_U}][{_L}'’]+)*")),
    _Rule("PERSON", re.compile(rf"(?i)\b(?:referente|referenti|sig\.ra|sig\.|dott\.ssa|dottoressa|dott\.|ing\.|geom\.|arch\.|avv\.)[\s:]*([{_U}][{_L}'’]+(?:\s+[{_U}][{_L}'’]+){{1,2}})"), 1),
]


class RegexDetector:
    """Stdlib-only PII detector — structured identifiers + Engine-specific fields."""

    name = "regex"

    def __init__(self, extra_rules: Optional[List[_Rule]] = None) -> None:
        self._rules = list(_DEFAULT_RULES) + list(extra_rules or [])

    def detect(self, text: str) -> List[PIISpan]:
        spans: List[PIISpan] = []
        for rule in self._rules:
            for m in rule.pattern.finditer(text):
                grp = rule.group if (rule.group == 0 or (m.lastindex and rule.group <= m.lastindex)) else 0
                val = m.group(grp)
                if not val or not val.strip():
                    continue
                start, end = m.span(grp)
                spans.append(PIISpan(start, end, rule.entity, val))
        return spans


class PresidioDetector:
    """Optional NER backend (Microsoft Presidio + spaCy). Adds PERSON/ORG/LOCATION.

    Lazily imports Presidio; raising if unavailable so `Pseudonymizer` can fall
    back to the regex detector. Also runs the regex rules so the structured /
    Engine-specific PII is covered regardless of the NER model.
    """

    name = "presidio"

    # Presidio entity_type → our token prefix (None = ignore)
    _MAP = {
        "PERSON": "PERSON", "LOCATION": "LOCATION", "GPE": "LOCATION", "NORP": "ORG",
        "ORGANIZATION": "ORG", "ORG": "ORG", "EMAIL_ADDRESS": "EMAIL", "IBAN_CODE": "IBAN",
        "CREDIT_CARD": "CREDIT_CARD", "IT_FISCAL_CODE": "FISCAL_CODE", "IT_VAT_CODE": "VAT",
        "PHONE_NUMBER": "PHONE", "IT_IDENTITY_CARD": "ID", "DATE_TIME": None, "NRP": "ORG",
    }

    def __init__(
        self,
        spacy_model: str,
        language: str = "it",
        regex_detector: Optional[RegexDetector] = None,
        min_score: float = 0.4,
    ) -> None:
        from presidio_analyzer import AnalyzerEngine  # noqa: F401  (raises if missing)
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": language, "model_name": spacy_model}],
        })
        nlp_engine = provider.create_engine()
        self._engine = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[language])
        self._lang = language
        self._min = min_score
        self._regex = regex_detector or RegexDetector()

    def detect(self, text: str) -> List[PIISpan]:
        spans = list(self._regex.detect(text))
        try:
            results = self._engine.analyze(text=text, language=self._lang)
        except Exception:  # NER failure must not break the pipeline
            results = []
        for r in results:
            if r.score < self._min:
                continue
            ent = self._MAP.get(r.entity_type, r.entity_type)
            if not ent:
                continue
            spans.append(PIISpan(r.start, r.end, ent, text[r.start:r.end]))
        return spans


# ── Per-request masking session (the reversible map) ──────────────────────────
@dataclass
class MaskingSession:
    """Holds the ephemeral original↔token map for ONE request."""

    detector: object
    _orig_to_token: Dict[str, str] = field(default_factory=dict)
    _token_to_orig: Dict[str, str] = field(default_factory=dict)
    _counters: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # context manager → guarantees the map is wiped
    def __enter__(self) -> "MaskingSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _token_for(self, entity: str, value: str) -> str:
        if value in self._orig_to_token:
            return self._orig_to_token[value]
        self._counters[entity] += 1
        token = f"[{entity}_{self._counters[entity]}]"
        self._orig_to_token[value] = token
        self._token_to_orig[token] = value
        return token

    def mask(self, text: str) -> str:
        """Replace detected PII with tokens (reusing tokens for repeated values)."""
        if not text:
            return text
        try:
            spans = self.detector.detect(text)
        except Exception:
            return text
        if not spans:
            return text
        spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
        out: List[str] = []
        cursor = 0
        for sp in spans:
            if sp.start < cursor:           # overlaps a region already masked
                continue
            out.append(text[cursor:sp.start])
            out.append(self._token_for(sp.entity, sp.value))
            cursor = sp.end
        out.append(text[cursor:])
        return "".join(out)

    def unmask(self, text: str) -> str:
        """Restore original values from tokens. Tolerant to minor LLM perturbations."""
        if not text or not self._token_to_orig:
            return text
        # exact pass — longest tokens first so [X_1] can't clobber [X_10]
        for token in sorted(self._token_to_orig, key=len, reverse=True):
            text = text.replace(token, self._token_to_orig[token])
        # tolerant pass — handle "[ PERSON_1 ]" / stray whitespace inside brackets
        if "[" in text:
            norm = {re.sub(r"\s+", "", t): o for t, o in self._token_to_orig.items()}
            text = re.sub(
                r"\[[^\]\n]{0,40}\]",
                lambda m: norm.get(re.sub(r"\s+", "", m.group(0)), m.group(0)),
                text,
            )
        return text

    @property
    def masked_count(self) -> int:
        return len(self._token_to_orig)

    @property
    def mapping(self) -> Dict[str, str]:
        """Read-only copy (token → original) for diagnostics/tests. Never log in prod."""
        return dict(self._token_to_orig)

    def close(self) -> None:
        """Destroy the temporary map (point 4 of the flow)."""
        self._orig_to_token.clear()
        self._token_to_orig.clear()
        self._counters.clear()


class Pseudonymizer:
    """Engine that builds detectors once and hands out per-request sessions."""

    def __init__(
        self,
        backend: Optional[str] = None,
        spacy_model: Optional[str] = None,
        language: str = "it",
    ) -> None:
        backend = (backend or getattr(config, "PII_BACKEND", "auto") or "auto").lower()
        self.backend_name, self._detector = self._build(backend, spacy_model, language)

    def _build(self, backend: str, spacy_model: Optional[str], language: str):
        if backend == "regex":
            return "regex", RegexDetector()
        if backend in ("auto", "presidio"):
            try:
                model = spacy_model or getattr(config, "PII_SPACY_MODEL", "it_core_news_lg")
                det = PresidioDetector(model, language=language, regex_detector=RegexDetector())
                logger.info("Pseudonymizer: backend Presidio attivo (modello %s).", model)
                return "presidio", det
            except Exception as e:  # not installed / model missing → regex fallback
                if backend == "presidio":
                    logger.warning("Presidio non disponibile (%s) — fallback al detector regex locale.", e)
                else:
                    logger.info("Presidio non installato — uso il detector regex locale per il masking PII.")
                return "regex", RegexDetector()
        return "regex", RegexDetector()

    def session(self) -> MaskingSession:
        """A fresh, ephemeral masking session for one request."""
        return MaskingSession(self._detector)
