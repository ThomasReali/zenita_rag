# NextPulse — Brief di Sviluppo (modulare, a fasi)

> **Stato:** rev. 4 · **Data:** 2026-06-06 · **Owner:** team NextPulse
> Documento di lavoro **per Claude**. Piano operativo per costruire l'app in modo modulare.
> Si fonda su [REQUISITI.md](./REQUISITI.md) e [MODELLO_DATI.md](./MODELLO_DATI.md).
>
> ## ⛔ Regola di processo (vincolante)
> **Prima di iniziare OGNI fase, fermarsi e chiedere supervisione/approvazione all'utente.**
> A fine fase: breve resoconto di cosa è stato fatto, cosa è stato verificato, e cosa cambia
> rispetto al piano. Poi si attende il via per la fase successiva.

## Avanzamento

| Fase | Titolo | Stato |
|------|--------|-------|
| 0 | Setup, rischi & OpenRouter | ✅ **fatta** |
| 1 | Ingestion multi-formato + robustezza | ✅ **fatta** |
| 2 | **Vector store: migrazione ChromaDB → Qdrant** | ✅ **fatta** |
| 3 | Chunking & metadati di citazione | ✅ **fatta** |
| 4 | Pipeline RAG & governance (gate + citazioni + hybrid) | ✅ **fatta** |
| 5 | Interfaccia & demo (FastAPI + frontend TS/Tailwind) | ✅ **fatta** |
| 6 | Hardening & qualità (incrementale, retry, logging, test API) | ✅ **fatta** |
| 7 | Materiale di pitch (PITCH.md) | ✅ **fatta** |

## Stack attuale (implementato)

- **Ingestion:** parser multi-formato (PDF, TXT/MD, DOCX, CSV, XLSX, JSON) con routing,
  row-grouping delle tabelle, robustezza per-file, skip di scansioni e manifest.
- **Embedding:** `intfloat/multilingual-e5-small` (locale, CPU, prefissi `query:`/`passage:`).
- **Vector DB:** **Qdrant** (embedded locale); retrieval **hybrid dense e5 + BM25 (RRF)**, payload filtering nativo.
- **LLM:** via **OpenRouter** (OpenAI-compatibile, `base_url`), modello da `.env` (`CHAT_MODEL`).
- **Governance:** gate di rilevanza (coseno < soglia → "non in doc") + **gate di ambiguità** (giudice LLM
  → discrezione su fonti in conflitto); citazioni `[Fonte: file, pag.]`.
- **Interfacce:** **FastAPI** (`/api/*`) + **frontend Vite/TS/Tailwind** (primaria); Streamlit e CLI (alternative).
- **Test:** **40 verdi** (LLM mockato), Python 3.14, gestiti con `uv`.

---

## Fasi

### ✅ Fase 0 — Setup, rischi & OpenRouter *(FATTA)*
- Wiring **OpenRouter**: `config.OPENROUTER_API_KEY` (+fallback `OPENAI_API_KEY`), `OPENAI_BASE_URL`,
  `CHAT_MODEL` come slug OpenRouter; client `OpenAI(base_url=…)` in `rag_chain.py`. Embedding **locali**
  (OpenRouter non espone embeddings).
- `uv sync` su Python 3.14 (core + torch 2.12 + pytest); `.env` creato; `DATA_DIR` risolto.
- Baseline test verde; pipeline offline (parse→embed→search) verificata.
- **RISK-1 ✅ verificato (dopo Fase 2):** query reale end-to-end via OpenRouter → risposta grounded con fonti. `CHAT_MODEL` impostato su un modello di **chat** (`google/gemma-4-26b-a4b-it:free`); il precedente `riverflow` era un modello immagine → testo vuoto.

### ✅ Fase 1 — Ingestion multi-formato + robustezza *(FATTA)*
- `document_processor.py` riscritto: routing per estensione; tabelle CSV/XLSX a **row-group con
  header ripetuto**; **try/except per-file** + report `processed/skipped/failed`; guardia
  `extract_text() or ""`; decrypt PDF cifrati; skip di scansioni (PDF ~0 testo) e manifest metadati.
- `config.py`: embedding → **`multilingual-e5-small`** (env-configurabile) + prefissi e5.
- `vector_store.py`: **ID deterministici** (hash `source|chunk_id|text`) + **`upsert`** → re-index
  idempotente (RF6); embedding **a batch** (1000).
- Housekeeping: shim `sys.path` in `index_documents.py` e `query_rag.py` (bug "No module 'src'");
  rimosso il doppio caricamento del modello in `query_rag`; `load_text` in `utf-8-sig`.
- **Risultati verificati:** 32 test verdi; parsing dell'intera `KNOWLEDGE/` **senza crash** → 515 file
  processati (511 PDF, 2 DOCX, 1 CSV, 1 XLSX), **14.575 chunk**, 24 saltati con motivo (19 scansioni,
  4 manifest, 1 `.url`); **idempotenza** confermata; indice completo costruito (poi **rimosso** in
  vista della migrazione a Qdrant).
- **Decisione chiusa:** RISK-2 → `multilingual-e5-small` scelto e applicato.

### ✅ Fase 2 — Vector store: migrazione ChromaDB → Qdrant 🗄️ *(FATTA)*
**Perché:** Qdrant offre **payload filtering** nativo (e in prospettiva **hybrid search** dense+sparse)
che ChromaDB non ha — utile per filtrare per `doc_type`/`decreto`/`comune` e per i match esatti sui
riferimenti normativi. Migrato **prima** di arricchire i metadati (Fase 3) per non rifare il lavoro.

**Deploy scelto:** **embedded locale** (`QdrantClient(path="./qdrant_data")`, niente container),
coerente col principio locale-first; `QDRANT_URL` per puntare a un server quando servirà.

**Fatto:**
- Dipendenze: **+`qdrant-client 1.18`**, **−`chromadb`** (gira su Python 3.14).
- `vector_store.py` riscritto su Qdrant **a interfaccia invariata** (`add_documents`/`search`/`get_stats`);
  point = `id`(**UUID5** deterministico) + `vector` + `payload`(testo in `_text` + metadati); distance
  **Cosine**, dim da `get_embedding_dimension()`; **`search(where=…)`** per il filtro payload; `upsert`
  idempotente; embedding **a batch**.
- `config.py`: `QDRANT_PATH`/`QDRANT_URL` (al posto di `CHROMA_PERSIST_DIR`); rimosso l'import dei tipi
  ChromaDB da `rag_chain`/`index_documents`.
- Aggiornati **test** (fixture → `QDRANT_PATH`), **MODELLO_DATI**, **README**, **.gitignore** (`qdrant_data/`).

**Risultati verificati:**
- **32/32 test verdi**; re-index completo → **14.575 point** in Qdrant (515 file, 24 saltati).
- Retrieval semantico OK; **filtro payload** `doc_type=csv` restituisce solo chunk del CSV (validato).

### ✅ Fase 3 — Chunking & metadati di citazione 🧩 *(FATTA)*
**Obiettivo:** RF3/RF4/RF11 — chunk semanticamente coerenti (≈ un articolo/paragrafo), mai tagliati
in punti critici, **mai oltre la finestra di embedding**, con metadati di citazione.

**Chunking strutturale dimensionato alla finestra di embedding (token).** Sostituisce il taglio a
caratteri fissi di Fase 1. *Nota:* il target iniziale "450–500 **parole**" è stato raffinato in
**token** — e5-small vettorizza max 512 token (≈320 parole), quindi misurare in parole troncava la
coda dei chunk in embedding.
- **Misura in token** del modello (iniettata come `length_fn`): `max = 480`, `min = 200`
  (config `CHUNK_MAX_TOKENS`/`CHUNK_MIN_TOKENS`); margine sotto 512 per prefisso `passage:` + special token.
- **Confini di blocco** (dove è sicuro tagliare): inizio di `Art.`/`Articolo`/`Capo`/`Titolo`/`Allegato`/`Sezione`
  e i paragrafi; tabelle CSV/XLSX a row-group (header ripetuto), anch'esse a budget di token.
- **Logica + guardrail:** accumula frase per frase; taglia a un confine **solo se ≥ min**; **tetto
  rigido** che forza il taglio (alla frase) prima di superare `max`; le frasi singole oltre-max sono
  spezzate. Code sotto-min ammesse solo per ultimi chunk / documenti corti.

**Metadati di citazione** (payload Qdrant): `doc_type`, `category` (euristica nome/cartella), `page`
(parsing PDF **per-pagina**), `section` (articolo di apertura del chunk), `decreto`/`data_decreto`
estratti dal nome file.

**Risultati verificati:**
- **34/34 test verdi** (nuovi test sul chunker: tetto max, taglio al confine, confine ignorato sotto-min).
- Re-index → **4.607 chunk**; **token max 480, 0 chunk oltre 512** (zero troncamenti); medio ~385 token.
- Copertura metadati (campione): `page` sui PDF, `category` ~91% (corretta), `decreto` ~34%.

**Rinviato (non bloccante):** enrichment via manifest `manifest_download_mit.*` (join decreto→titolo/data),
confine FAQ "nuova domanda", `product` per le schede.

### ✅ Fase 4 — Pipeline RAG & governance 🛡️ *(FATTA)*
**Obiettivo:** consolidare RF7-RF12, RF17-RF18 sfruttando Qdrant.

**Fatto e verificato (governance core):**
- **Fallback deterministico (RF10):** `vector_store.search` espone lo **score** cosine; in `query`, se
  il top-score < `SCORE_THRESHOLD` (default **0.82**, tarato su e5) → messaggio "non in documentazione"
  **senza generare** (zero allucinazioni). Verificato live: query off-dominio (carbonara, score 0.78) →
  fallback, **nessuna chiamata LLM**.
- **Gate di ambiguità (RF19):** un **giudice LLM** valuta se le fonti recuperate sono in conflitto; se sì
  → **discrezione** (cita i provvedimenti distinti + rimando al Bid Manager, **niente interpretazione**).
  Fail-safe verso la discrezione se il giudice non risponde. Campo `ambiguous` nel `QueryResult` + badge UI.
- **Citazioni tracciabili:** il contesto passato all'LLM è etichettato `[Fonte: file, pag. X, art. Y,
  decreto Z]`; le `sources` includono la **pagina** (es. "Nuovo Codice della Strada.pdf (pag. 116)").
- **Memoria conversazionale anche nella CLI** (`query_rag.py` ora multi-turn).
- **System prompt** rifinito (grounding, fallback, tono Pre-Sales, citazione via `[Fonte: …]`).
- 35/35 test verdi (nuovi: fallback no-docs, fallback low-score).

**Hybrid search (fatto):** retrieval **dense e5 + sparse BM25** fusi con **RRF** (client-side). BM25
ottenuto **senza `fastembed`/`onnxruntime`** (rischiosi su Python 3.14): vettori sparse TF (token hashed
crc32) + **modifier IDF nativo di Qdrant**. Schema collection con vettori nominati (`dense` + `bm25`),
filtro payload applicato a entrambi i rami. Il **gate resta sul coseno denso** (scala stabile,
indipendente da RRF). Verificato su 4.607 point: il termine esatto "VELOMATIC" emerge dal CSV (merito BM25).
- (Opz., rinviato) re-ranking cross-encoder; domanda di chiarimento su query ambigue (UC2/UC4).

### ✅ Fase 5 — Interfaccia & demo 🖥️ *(FATTA — FastAPI + frontend TS/Tailwind)*
**Decisione presa:** **FastAPI + frontend Vite/TS/Tailwind** (al posto di Streamlit).

**Backend** (`src/nextpulse/api.py`): `GET /api/status` (documenti distinti, chunk, modello) e
`POST /api/query` (→ `QueryResponse`). RAGChain caricato una volta (lifespan), CORS per il dev server,
errori LLM (es. 429) propagati come **502**. Se `web/dist/` esiste, FastAPI serve anche la UI (single-origin).

**Frontend** (`web/`, Vite + TypeScript + Tailwind v4): chat con cronologia; per ogni risposta **badge
grounded/fallback**, **barra di confidenza** (dal `top_score`) e **pannello fonti con pagina**; sidebar
con **stato KB** (documenti distinti — **RF15 corretto**), modello, **domande di esempio**, reset.

**Verificato:**
- Backend live: `/api/status` → 515 documenti / 4607 chunk; query off-dominio → **gate** (grounded:false, niente LLM).
- `npm run build` pulito (tsc + vite); FastAPI serve UI + API single-origin (`/` 200, asset 200, `/api/*` ok).
- 35/35 test verdi (il backend non tocca la suite).

**Avvio:** `uvicorn src.nextpulse.api:app` (backend) + `cd web && npm run dev` (UI, proxy su :8000);
oppure `cd web && npm run build` e poi tutto servito da `uvicorn`.
**Rinviato:** export conversazione; anteprima del testo del chunk (oltre al nome fonte).

### ✅ Fase 6 — Hardening & qualità ⚙️ *(FATTA, parziale)*
**Fatto e verificato:**
- **Indicizzazione incrementale:** manifest content-hash (`.index_manifest.json`); solo i file
  nuovi/modificati vengono re-processati, i chunk obsoleti rimossi via `delete_by_source`; **progress**
  per-file. Verificato: 1° run indicizza, 2° run → "indice già aggiornato".
- **Retry su rate-limit:** client OpenAI con `max_retries=4` (backoff esponenziale su 429/5xx).
- **Logging strutturato:** una riga per query (`grounded`, `top_score`, n. fonti, `latency_ms`, modello).
  Verificato: `INFO nextpulse.rag | query grounded=False top_score=0.810 … latency_ms=524 …`.
- **Test backend API:** 3 test FastAPI (`/api/status`, `/api/query`, errore→502) con RAGChain stubbato → **38/38**.

**Rinviato (non bloccante):**
- **OCR** per le 19 scansioni: tesseract 5.5 + poppler presenti, ma **manca il language pack `ita`**
  (`tesseract-data-ita`, pacchetto di sistema) → con sola lingua `afr` darebbe spazzatura. Hook pronto da abilitare.
- Streaming risposte (SSE); caching risposte/embedding.

### ✅ Fase 7 — Materiale di pitch 🎤 *(FATTA)*
Creato **[PITCH.md](./PITCH.md)**: scaletta 5 min (i 7 punti della Scheda), **script demo** con 4 query
killer (grounded + citazioni con pagina, hybrid/BM25 su termine esatto, follow-up conversazionale, gate
onesto), tabella **KPI**, gestione obiezioni (dalla discovery del workshop), numeri di execution e
"perché possiamo vincere". 🎉 **Piano completo (Fasi 0–7).**

---

## Housekeeping

- ✅ Shim `sys.path` negli script; ✅ rimosso doppio load in `query_rag`; ✅ `pytest` in dev-deps; ✅ `load_text` utf-8-sig.
- ✅ Rimosso l'entry point rotto `nextpulse-ui` da `pyproject.toml` (si usano `uvicorn`/`streamlit` diretti).
- ✅ Fase 2: rimosso `chromadb`, ripuliti i riferimenti `CHROMA_*` (config/README/test/gitignore).
- ⬜ `.gitignore`: valutare `KNOWLEDGE/` (i file non-PDF sono committabili).

## Decisioni — stato finale

1. **RISK-1** — ✅ verifica live OK; `CHAT_MODEL=google/gemma-4-26b-a4b-it:free` (free → 429 possibili).
2. **Qdrant deploy** — ✅ embedded locale; `QDRANT_URL` per un server futuro.
3. **Hybrid search** — ✅ BM25 via **IDF nativo Qdrant** + RRF client-side (senza `fastembed`).
4. **UI** — ✅ **FastAPI + frontend Vite/TS/Tailwind**.
5. **Gate di ambiguità** — ✅ **giudice LLM** del conflitto → discrezione (RF19).

**Ancora aperte / roadmap:**
- **OCR** delle 19 scansioni: serve il language pack `tesseract-data-ita` (pacchetto di sistema).
- **Modello a pagamento** per demo affidabile (es. `google/gemini-2.0-flash-001`) vs free rate-limited.
- `.gitignore`: valutare `KNOWLEDGE/`. · Roadmap: streaming, caching, re-ranking, enrichment via manifest MIT.

## Definition of Done (per fase)
- Codice eseguibile, test verdi, nessuna regressione sul contratto `QueryResult`.
- Resoconto all'utente: fatto / verificato / scostamenti dal piano.
- **Approvazione esplicita prima di aprire la fase successiva.**
