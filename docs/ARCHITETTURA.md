# NextPulse — Architettura e Funzionamento

> Documento di riferimento tecnico: **come funziona l'app**, le sue **caratteristiche** e lo
> **stack tecnologico**. Compagno di [REQUISITI.md](./REQUISITI.md) (cosa deve fare),
> [MODELLO_DATI.md](./MODELLO_DATI.md) (com'è strutturato il dato), [CHANGELOG.md](./CHANGELOG.md)
> (cronologia interventi) e [INTERVENTI_FUTURI.md](./INTERVENTI_FUTURI.md) (backlog).
>
> **Ultimo aggiornamento:** 2026-06-08

---

## 1. Cosa fa

**NextPulse** è un **AI Sales Assistant** per **Engine SpA** (gruppo Zenita), che vende sistemi di
**Traffic Enforcement & Smart City** (autovelox, ZTL/varchi, semafori, analytics mobilità) alla
Pubblica Amministrazione. È un **assistente conversazionale grounded (RAG)** che risponde **solo**
sulla base della documentazione aziendale e normativa, **cita sempre le fonti**, **dice quando non
sa** e **sa quando tacere** (conflitti normativi → discrezione).

L'app ha **tre sezioni** (command rail a sinistra):

1. **Assistente** — chatbot RAG sulla knowledge base aziendale (decreti MIT, Codice della Strada,
   schede prodotto, FAQ). Risposte grounded con citazioni numerate `[n]` e link alla fonte ufficiale.
2. **Gare d'Appalto · MIT** — scraping live del Portale Appalti del Ministero delle Infrastrutture
   e dei Trasporti (bandi in corso / in aggiudicazione) + chatbot dedicato ai documenti di gara.
3. **Configura Offerta** — dato uno scenario cliente, genera una **bozza d'offerta grounded** e
   non vincolante (soluzioni pertinenti + vincoli normativi + citazioni), da validare col Bid Manager.

---

## 2. Stack tecnologico

| Livello | Tecnologia | Note |
|---------|-----------|------|
| **LLM (generazione + classificatori)** | OpenAI API (`gpt-4o-mini`) via SDK `openai` | configurabile (`CHAT_MODEL`, `OPENAI_BASE_URL`); compatibile OpenRouter |
| **Embedding** | `sentence-transformers` · `intfloat/multilingual-e5-small` | **locali su CPU**, costo zero, dati non escono per l'indicizzazione |
| **Vector store** | **Qdrant** embedded (`qdrant-client`) | retrieval **hybrid** dense (e5) + sparse (BM25) con fusione **RRF** |
| **Re-ranking** (opt-in) | `sentence-transformers` CrossEncoder | precisione fonti; default OFF |
| **OCR** (opt-in) | Tesseract + `pytesseract` + `PyMuPDF` | recupera i PDF scansionati (lingua `ita`) |
| **Parsing documenti** | `pypdf`, `pandas`, `openpyxl`, `python-docx` | PDF/DOCX/CSV/XLSX/JSON/TXT |
| **Backend API** | **FastAPI** + **Uvicorn** | endpoint REST + SSE; serve `web/dist` in single-origin |
| **Frontend** | **Vite** + **TypeScript** + **Tailwind CSS v4** | nessun framework UI: vanilla TS modulare |
| **Privacy/PII** | regex + **Microsoft Presidio** (opzionale) | pseudonimizzazione reversibile (GDPR Art. 32) |
| **Audit/log** | **SQLite** (`query_log.db`, `governance_log.db`) | query log GDPR + log immutabile NIS2 |
| **Auth** (opt-in) | token HMAC-SHA256 stateless (stdlib) | ruolo verificato server-side |
| **Test** | `pytest` | **167 test** (LLM mockato) |
| **Runtime** | Python ≥ 3.10, gestione dipendenze `uv` | |

---

## 3. Pipeline di una query (online)

```
Domanda utente
  │
  ├─▶ [1] Condense — riformula in standalone query usando la chat history (LLM)
  │
  ├─▶ [2] GATE INTENTO — è una vera richiesta di dominio? (classificatore LLM)
  │         └─ NO (saluto/chiacchiera/fuori tema) ─▶ risposta colloquiale PULITA
  │                                                   (no retrieval, no fonti, no chrome) ✦ off_topic
  │         └─ SÌ ▼
  ├─▶ [3] Retrieve hybrid (dense e5 + BM25, RRF) → top-K  [+ re-ranking cross-encoder opt-in]
  │         └─ filtro deterministico per `status` (nasconde obsolete/poisoned/draft — RF28)
  │
  ├─▶ [4] GATE RILEVANZA (RF10) — coseno < soglia (0.82)?
  │         └─ SÌ ─▶ controllo OBSOLESCENZA: il match migliore è ABROGATO?
  │                   ├─ sì ─▶ avviso deterministico "abrogato, sostituito da…" ✦ obsolete (🔴)
  │                   └─ no ─▶ fallback "non presente in documentazione" (🔴 slate)
  │
  ├─▶ [5] GATE AMBIGUITÀ (RF19) — 2-3 fonti distinte, nessuna dominante, in conflitto? (giudice LLM)
  │         └─ SÌ ─▶ DISCREZIONE: cita le fonti divergenti, rimanda al Bid Manager (🔴 rosso)
  │
  └─▶ [6] GENERAZIONE grounded sul contesto etichettato [n]
            ├─ [MASKING PII locale] ──▶ LLM ──▶ [DE-MASKING locale]   (zero-knowledge)
            ├─ normalizzazione citazioni → marcatori `[n]` puliti
            ├─ adattamento al PROFILO attivo (Pre-Sales / Sales / Bid Manager) + confidenza
            └─ risposta + fonti + link ufficiali → audit log (SQLite)
```

Lo stesso pipeline alimenta sia `/api/query` (bloccante) sia `/api/query/stream` (SSE token-by-token):
la logica dei gate è centralizzata in `RAGChain._decide` (fonte di verità unica).

### Confidenza (color coding UI)
| Esito | Quando | UI |
|-------|--------|-----|
| 🟢 **verde** | risposta da **1 sola** fonte | "Grounded · fonte diretta", barra piena |
| 🟡 **giallo** | ≥2 fonti **complementari** | "Grounded · più fonti — verifica i dati critici" |
| 🔴 **rosso (ambiguo)** | 2-3 fonti **in conflitto** (RF19) | "Ambiguo · fonti in conflitto", barra bassa |
| ⚪ **slate** | nessuna fonte pertinente | "Fuori ambito" |
| ▫️ **plain** | messaggio fuori dominio/colloquiale | box pulita senza header/fonti |

---

## 4. Caratteristiche principali

### Affidabilità & Governance
- **Grounding & anti-allucinazione**: gate deterministico su coseno → rifiuta fuori dominio.
- **Disambiguazione prudente (RF19)**: giudice LLM rileva conflitti tra atti → discrezione, mai
  risolve il conflitto, mai deduce vigenza/abrogazione.
- **Obsolescenza & data-poisoning deterministica (RF28)**: ogni chunk ha uno `status`; il retrieval
  filtra obsolete/poisoned/draft; avviso "abrogato" costruito dai metadati (no LLM); job notturno
  + quarantena anti-poisoning; log immutabile (NIS2).
- **Gate di intento**: i messaggi fuori dominio non vengono "vestiti" da risposta grounded.
- **Tracciabilità**: ogni risposta cita file + pagina; **citazioni cliccabili** verso la fonte
  ufficiale `mit.gov.it` (enrichment dei metadati dal manifest MIT).
- **Limiti dichiarati** (RF17) e **export conversazione** Markdown (RF16) in UI.

### Role-awareness (RF20)
Tre profili in ordine di priorità: **Pre-Sales → Sales → Bid Manager**, ciascuno con system prompt,
livello terminologico (tecnico/cliente/legale), formato fonti e una **confidenza** (🟢/🟡/🔴).
Selezionabili da UI; con autenticazione attiva il ruolo è **verificato server-side**.

### Privacy by design (GDPR)
- **Pseudonimizzazione reversibile (Art. 32)**: la PII (nomi, email, IBAN, CIG/CUP, importi, Comuni)
  è mascherata **prima** dell'invio all'LLM e re-identificata in locale (zero-knowledge).
- **Audit log query** (SQLite) con identificatori opachi; **anonimizzazione notturna** (user/session
  → NULL oltre 6 mesi); stato da `GET /api/privacy`.

### Sicurezza (hardening)
- **Validazione input al bordo** (Pydantic) → HTTP 422 su payload abusivi.
- **Rate-limiting per IP** (sliding window) sugli endpoint a costo LLM → HTTP 429.
- **Login + ruolo verificato server-side** (opt-in, token HMAC, cookie httponly).
- **No information disclosure**: errori generici (502), dettaglio solo server-side.

### Performance & UX
- **Streaming SSE** delle risposte (token-by-token).
- **Cache risposte** (TTL+LRU) per domande ripetute → latenza ~0, costo azzerato, guard sui 429.
- **Re-ranking cross-encoder** opt-in per precisione del retrieval.
- **OCR** opt-in per i PDF scansionati.
- **Mini eval-harness** (`scripts/eval_rag.py`) per i KPI di governance.

---

## 5. Moduli backend (`src/nextpulse/`)

| File | Responsabilità |
|------|----------------|
| `config.py` | configurazione da `.env` (LLM, embedding, chunk, gate, privacy, auth, OCR…) |
| `document_processor.py` | parsing multi-formato + chunking strutturale a token + **OCR fallback** |
| `vector_store.py` | Qdrant embedded, retrieval **hybrid** dense+BM25 (RRF), `set_payload` governance/enrichment |
| `rag_chain.py` | pipeline: condense → **intento** → retrieve → gate rilevanza/obsolescenza/ambiguità → mask → generate → unmask; streaming; cache |
| `reranker.py` | cross-encoder re-ranking (opt-in, lazy) |
| `cache.py` | cache risposte TTL+LRU thread-safe |
| `ratelimit.py` | rate limiter sliding-window per IP |
| `auth.py` | token HMAC firmati + credential store (login leggero) |
| `pseudonymizer.py` | pseudonimizzazione reversibile PII (regex + Presidio opzionale) |
| `query_log.py` | audit log query (SQLite) + anonimizzazione GDPR |
| `governance_log.py` | log immutabile dei cambi di `status` (NIS2) |
| `bandi_scraper.py` | scraper Portale Appalti MIT (Friendly-Captcha PoW) + ingestion bandi |
| `configurator.py` | configuratore d'offerta grounded (bozza non vincolante) |
| `api.py` | backend FastAPI: tutti gli endpoint + serve il frontend |
| *(root)* `role_manager.py` | 3 profili + confidence (modulo standalone) |

### Endpoint API
- `GET /api/status` · `GET /api/roles` · `GET /api/privacy`
- `POST /api/query` · `POST /api/query/stream` (SSE) — chatbot principale
- `POST /api/configure` — configuratore d'offerta
- `GET /api/bandi` · `GET /api/bandi/scrape` (SSE) · `POST /api/bandi/query` — sezione gare
- `POST /api/login` · `POST /api/logout` · `GET /api/me` — auth (opt-in)

### Script (`scripts/`)
`index_documents.py` (indicizzazione incrementale) · `ingest_bandi.py` · `enrich_metadata.py`
(enrichment MIT) · `audit_obsolescence.py` · `quarantine_source.py` · `anonymize_logs.py`
(job GDPR notturno) · `eval_rag.py` (eval KPI) · `query_rag.py` (CLI).

---

## 6. Frontend (`web/`)

Vanilla **TypeScript** modulare (Vite + Tailwind v4), nessun framework:
- `src/main.ts` — chat principale (streaming, citazioni cliccabili, color coding, export, login).
- `src/bandi.ts` — sezione Gare d'Appalto (scraping live SSE + chatbot bandi).
- `src/config.ts` — sezione Configura Offerta.
- `src/style.css` — design system (colori segnale 🟢/🟡/🔴/slate, lozenge, conf-segs).

In sviluppo gira su `:5173` (proxy → `:8000`); in produzione `npm run build` → servito da Uvicorn.

---

## 7. Setup rapido

```bash
uv sync                                   # dipendenze (Python ≥ 3.10)
cp .env.example .env                      # inserisci OPENAI_API_KEY (o OPENROUTER_API_KEY)
uv run python scripts/index_documents.py  # indicizza (DATA_DIR=./KNOWLEDGE)

uv run uvicorn src.nextpulse.api:app --port 8000   # backend
cd web && npm install && npm run dev               # frontend :5173

# opzionali
uv sync --extra ocr      # OCR scansioni (+ Tesseract di sistema, lingua ita)  → OCR_ENABLED=1
uv sync --extra pii      # NER Presidio per il masking PII
uv run python scripts/enrich_metadata.py           # link ufficiali MIT nelle citazioni
uv run pytest tests/ -q                            # 167 test
```
