# NextPulse — Piano d'azione (sessione autonoma)

> **Avvio:** 2026-06-08 (sessione notturna autonoma) · **Branch:** `feat/sentinel-ui-docs`
> Riferimento operativo per il batch di miglioramenti deciso dopo il riordino ruoli + fix anti-troncamento.
> Legenda stato: ✅ fatto · 🟡 in corso · 📦 archiviato (richiede decisione/dipendenza esterna) · ⬜ da fare

## Obiettivo
Implementare in autonomia tutto ciò che è realizzabile senza intervento dell'utente.
Per ogni scelta dubbia → opzione **consigliata**. Ciò che richiede decisioni di prodotto o
dipendenze di sistema → **archiviato** e riportato all'utente.

## A. Quick win (basso sforzo, alto ritorno)
- ✅ **A1 — Allineamento tracking**: RF16 (export Markdown) e RF17 (pannello "Limiti del
  sistema") risultavano "rinviato/da fare" nella matrice di `REQUISITI.md` ma sono **già
  implementati** in `web/src/main.ts`. Aggiornata la matrice.
- ✅ **A2 — Caching risposte**: cache locale (TTL+LRU) keyed su (domanda normalizzata, ruolo, k,
  firma history). Azzera latenza/costo LLM sulle domande ripetute (demo) e mitiga i 429.
  Per-istanza `RAGChain` → KB principale e bandi restano separate. Flag `cached` nella risposta.
- ✅ **A3 — Streaming risposte (SSE)**: endpoint additivo `/api/query/stream` che trasmette i
  token della generazione (caso grounded) + evento finale con risposta post-processata
  (citazioni normalizzate, ruolo, de-masking PII). UI live. Endpoint `/api/query` invariato.

## B. Qualità retrieval/RAG (medio sforzo)
- ✅ **B1 — Re-ranking cross-encoder** (opt-in `RERANK_ENABLED=0`): recupera più candidati e li
  riordina con un cross-encoder prima del top-k. Migliora precisione fonti/grounding. Default OFF
  per non scaricare pesi a sorpresa; pipeline invariata quando spento.
- ✅ **B2 — Mini eval-harness** (`scripts/eval_rag.py`): set etichettato (in-dominio →
  grounded+citazione; fuori-dominio → fallback) con report grounding rate / citation rate /
  fallback-correctness / latenza media. Copre i KPI di `docs/KPI.md`.

## C. Sicurezza / Governance (backlog RNF6)
- ✅ **C1 — Rate-limiting per IP**: sliding-window in-memory su `/api/query`, `/api/query/stream`
  e `/api/bandi/query` (endpoint a costo LLM). `429` con messaggio chiaro. Configurabile.
- 📦 **C2 — Ruolo da identità autenticata server-side**: richiede scelta di prodotto su
  IdP/SSO/sessioni. Oggi `role` è selezionato dal client (governance, non sicurezza). *Archiviato.*

## D. Scommesse grosse / blocchi esterni — ARCHIVIATE
- 📦 **D1 — OCR delle ~19 scansioni**: bloccato da `tesseract-ocr` + language pack `ita`
  (pacchetto di sistema, installazione admin). *Archiviato: serve setup di sistema.*
- 📦 **D2 — Configuratore d'offerta agentico**: alto sforzo, comportamento generativo non
  deterministico → rischio per una demo "grounded". Va progettato con l'utente. *Archiviato.*
- 📦 **D3 — Enrichment metadati via manifest MIT** (join decreto→titolo/data): manca il file
  manifest sorgente con la mappatura. *Archiviato: serve il dato sorgente.*
- 📦 **D4 — Live-fetch gazzette ufficiali**: integrazione fonte esterna senza API stabile.
  *Archiviato.*
- 📦 **D5 — Modello LLM a pagamento per demo affidabile**: decisione di account/budget
  dell'utente (`CHAT_MODEL` + credito OpenRouter). *Archiviato: scelta dell'utente.*

## Note di esecuzione
- Tutto su `feat/sentinel-ui-docs`, un commit per unità logica, test ad ogni step.
- Nessuna modifica distruttiva; gli endpoint esistenti restano invariati (le novità sono additive).
- Default conservativi (rerank OFF) così l'app in esecuzione non cambia comportamento finché non si abilita.

---

## ☕ Report del mattino (sessione autonoma conclusa)

**Implementato e testato (tutto verde — 139 test, da 111):**
| # | Cosa | File chiave |
|---|------|-------------|
| A1 | Allineato tracking RF16/RF17 (erano già fatti in UI) | `docs/REQUISITI.md` |
| A2 | Cache risposte TTL+LRU per-istanza (`cached` nel result) | `src/nextpulse/cache.py` |
| A3 | Streaming SSE `POST /api/query/stream` + UI live | `rag_chain.py`, `api.py`, `web/src/main.ts` |
| B1 | Re-ranking cross-encoder opt-in (`RERANK_ENABLED=0`) | `src/nextpulse/reranker.py` |
| B2 | Mini eval-harness KPI governance | `scripts/eval_rag.py` |
| C1 | Rate-limiting per IP (→ 429) | `src/nextpulse/ratelimit.py` |

**Verifiche live (backend riavviato):** streaming reale 568 token + risposta completa 2232 char;
cache hit sulla 2ª chiamata (3 eventi, `cached=true`); suite `pytest` 139/139.

**✅ Fatto dopo conferma dell'utente (2026-06-08, sessione interattiva):**
- **D1 — OCR scansioni:** Tesseract 5.4 + lingua `ita` installati; fallback OCR attivo
  (`OCR_ENABLED=1`); re-index → **19 decreti scansionati recuperati, +243 chunk** (KB 511→528 doc,
  Qdrant 4569→4810 chunk). Verificato end-to-end (Direttiva Minniti citata da una risposta grounded).
- **D2 — Configuratore d'offerta (bozza):** `OfferConfigurator` + `POST /api/configure` + **sezione UI
  "Configura Offerta"**. Bozza grounded, citata, non vincolante; fallback onesto se KB insufficiente.
- **Modello LLM:** confermato uso della key **OpenAI diretta** (`gpt-4o-mini`, a pagamento) — nessun 429.

**📦 Archiviato — serve una tua decisione/dipendenza (nessuna azione presa):**
- **C2** Ruolo da identità autenticata (SSO/IdP) — scelta di prodotto.
- **D3** Enrichment metadati MIT — manca il file manifest sorgente (decreto→titolo/data).
- **D4** Live-fetch gazzette ufficiali — integrazione esterna senza API stabile.

**Per provare lo streaming:** `cd web; npm run dev` → http://localhost:5173 (backend già su :8000).
**Per misurare i KPI:** `uv run python scripts/eval_rag.py` (fa chiamate LLM reali).
