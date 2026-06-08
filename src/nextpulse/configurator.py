"""Offer-draft configurator (bozza d'offerta) — grounded, non-binding.

Given a customer scenario (e.g. "Comune medio, vuole ZTL + controllo velocità"), it retrieves
the relevant product/spec/normative chunks from the company KB and asks the LLM to assemble a
STRUCTURED DRAFT offer: which solutions fit, why, with inline citations, and the normative
constraints that apply — strictly from the documents, never inventing prices/specs.

It is deliberately *agentic-lite*: it can fan out retrieval over several declared needs and
merge the distinct evidence, but every claim stays grounded and the output always closes with a
"bozza non vincolante — valida con il Bid Manager" disclaimer. Reuses the RAGChain helpers
(retrieve, build/format context, citation normalization, PII masking) so governance is identical.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from src.nextpulse import config

logger = logging.getLogger("nextpulse.configurator")

# Max distinct evidence chunks fed to the draft (keeps the prompt focused / bounded cost).
MAX_CONTEXT_CHUNKS = 8

CONFIGURATOR_SYSTEM_PROMPT = """\
Sei un Sales Engineer di Engine SpA (gruppo Zenita), specializzato in Traffic Enforcement \
(autovelox, ZTL/varchi, semafori intelligenti, analytics mobilità). Devi redigere una BOZZA di \
configurazione d'offerta per lo scenario cliente indicato, basandoti ESCLUSIVAMENTE sui DOCUMENTI \
qui sotto (schede prodotto, specifiche tecniche, normativa).

REGOLE (vincolanti):
1. GROUNDING: usa SOLO informazioni presenti nei documenti. Se un dato manca, scrivilo \
esplicitamente ("dettaglio non disponibile nei documenti — da verificare"), non inventarlo.
2. NIENTE PREZZI, sconti, quantità o parametri tecnici inventati. Niente sigle o normative non \
presenti nei documenti.
3. CITAZIONI: ogni documento inizia con il suo marcatore [n]. Riporta inline ESATTAMENTE [n] \
subito dopo l'affermazione che ne deriva. Scrivi SOLO il marcatore (niente "Fonte", niente nome file).
4. STRUTTURA la bozza ESATTAMENTE in queste sezioni (titoli in grassetto):
   - **Scenario**: sintesi dei bisogni del cliente.
   - **Soluzioni proposte**: prodotti/sistemi pertinenti dai documenti, ciascuno con una breve \
motivazione e la citazione [n]; raggruppa per esigenza quando utile.
   - **Vincoli normativi pertinenti**: riferimenti (decreti/Codice della Strada) presenti nei \
documenti, con citazione; se non presenti, dillo.
   - **Note e prossimi passi**: cosa resta da quantificare/verificare (es. sopralluogo, quantità, prezzi).
5. CHIUDI SEMPRE con questa riga, identica:
   "⚠ Bozza non vincolante: prezzi, quantità e conformità finali vanno validati dal Bid Manager."

DOCUMENTI:
{context_str}

SCENARIO CLIENTE: {scenario}
BOZZA:"""

# Honest fallback when the KB has nothing relevant to the scenario (grounding gate).
NO_CONTEXT_DRAFT = (
    "Non ho trovato nella documentazione aziendale elementi sufficienti per proporre una "
    "configurazione fondata per questo scenario. Riformula indicando prodotti/esigenze "
    "specifici, oppure coinvolgi il Bid Manager.\n\n"
    "⚠ Bozza non vincolante: prezzi, quantità e conformità finali vanno validati dal Bid Manager."
)


class OfferConfigurator:
    """Build a grounded, non-binding draft offer from a customer scenario."""

    def __init__(self, rag) -> None:
        # Holds a RAGChain (company-KB bound) and reuses its retrieval + grounding helpers.
        self.rag = rag

    def _gather(self, queries: List[str], k: Optional[int]):
        """Fan out retrieval over the scenario + each declared need; merge distinct chunks
        (dedup by source+text) and track the best dense cosine for the grounding gate."""
        docs: List[str] = []
        metas: List[dict] = []
        scores: List[float] = []
        seen = set()
        top_cos = 0.0
        for q in queries:
            q_docs, q_metas, q_scores, q_top = self.rag.retrieve(q, k=k)
            top_cos = max(top_cos, q_top)
            for d, m, s in zip(q_docs, q_metas, q_scores):
                key = (str(m.get("source")), d[:80])
                if key in seen:
                    continue
                seen.add(key)
                docs.append(d)
                metas.append(m)
                scores.append(s)
        # Keep the strongest distinct chunks (bounded prompt).
        order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)[:MAX_CONTEXT_CHUNKS]
        return ([docs[i] for i in order], [metas[i] for i in order],
                [scores[i] for i in order], top_cos)

    def configure(
        self, scenario: str, needs: Optional[List[str]] = None, k: Optional[int] = None
    ) -> dict:
        """Produce a draft offer for `scenario`. `needs` (optional) broadens retrieval with
        extra targeted queries (e.g. ["controllo velocità", "ZTL"]). Returns a dict with the
        draft text, the cited sources, and the same grounding signals as a normal query."""
        t0 = time.perf_counter()
        queries = [scenario] + [n for n in (needs or []) if n and n.strip()]
        docs, metas, scores, top_cos = self._gather(queries, k)

        if not docs or top_cos < config.SCORE_THRESHOLD:
            return {
                "scenario": scenario, "draft": NO_CONTEXT_DRAFT, "sources": [],
                "grounded": False, "top_score": top_cos,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }

        context_str = self.rag._build_context(docs, metas)
        sources = self.rag._format_sources(metas)
        system_prompt = CONFIGURATOR_SYSTEM_PROMPT.format(
            context_str=context_str, scenario=scenario
        )
        session = self.rag.pseudonymizer.session() if config.PII_MASKING_ENABLED else None
        try:
            raw = self.rag._complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": scenario},
                ],
                session=session, temperature=0.3, max_tokens=1200,
            )
        finally:
            if session is not None:
                session.close()
        draft = self.rag._normalize_citations(raw)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("configure grounded=True top_score=%.3f sources=%d latency_ms=%d",
                    top_cos, len(sources), latency_ms)
        return {
            "scenario": scenario, "draft": draft, "sources": sources,
            "grounded": True, "top_score": top_cos, "latency_ms": latency_ms,
        }
