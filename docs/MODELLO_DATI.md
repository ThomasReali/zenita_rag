# NextPulse — Modello Dati

> **Stato:** rev. 5 (modulo Bandi/Gare R.A.M. + hardening sicurezza API) · **Data:** 2026-06-07 · **Owner:** team NextPulse
> Documento di lavoro interno. Definisce le entità, i formati di input, lo schema dei chunk
> indicizzati e gli oggetti scambiati dalla pipeline RAG. **È l'unica fonte di verità per la
> configurazione (§5).** Compagno di [REQUISITI.md](./REQUISITI.md) e [BRIEF.md](./BRIEF.md).

## 1. Panoramica del flusso del dato

```
File grezzi (data/ → oggi vuota; corpus reale in KNOWLEDGE/)
   │  parsing per estensione → testo normalizzato (+ Markdown per le tabelle)
   ▼
Documento (testo + metadati a livello file)
   │  chunking strutturale/semantico
   ▼
Chunk (testo breve + metadati di citazione + id deterministico)
   │  embedding (vettore) + upsert
   ▼
Qdrant collection (point: id+vector+payload)  ──(retrieval top-K)──►  Contesto  ──►  Risposta (con fonti)
```

## 2. Sorgenti dati (input)

Dataset reale in `KNOWLEDGE/` (108 MB, 539 file) — campione di ciò che andrà in `data/`:

| Formato | Quantità | Esempi | Parser | Note di parsing |
|---------|----------|--------|--------|-----------------|
| PDF | ~530 | Decreti MIT, Codice della Strada, Linee guida ZTL, Schede | `pypdf` (+ eventuale OCR/tabelle in fase avanzata) | `extract_text()` può tornare `None` su PDF scansionati → guardia `or ""` obbligatoria |
| CSV | 2 | `Elenco autovelox MIT.csv` (sep `;`, con BOM) | `pandas` → Markdown | colonne: Codice Accertatore, Denominazione, Codice Catastale, Comune (provincia), Decreto, Data Decreto, Tipo, Marca, Modello |
| XLSX | 2 | fogli tecnici | `openpyxl`/`pandas` → Markdown | un file può avere più fogli |
| DOCX | 2 | FAQ normativa, quadro omologazione | `python-docx` | testo a paragrafi |
| JSON | 2 | dati strutturati | parser nativo | appiattire in testo/coppie chiave-valore |
| URL | 1 | link sito Engine | — | **non indicizzabile** (è un collegamento) |

**Attenzione `DATA_DIR`:** il pipeline legge `config.DATA_DIR` (default `./data`, oggi **vuota**).
Per indicizzare il corpus reale: copiare/symlink `KNOWLEDGE`→`data`, oppure `DATA_DIR=./KNOWLEDGE`.

**Regola tolleranza errori:** il dataset è "sporco" e potenzialmente sintetico/modificato. L'ingestion
deve essere tollerante: un file non parsabile va loggato e saltato, non interrompe il batch (RNF5).
✅ Implementato in Fase 1: `process_directory` ha try/except per-file + report `processed/skipped/failed` (0 crash sui 539 file).

## 3. Entità

### 3.1 Document (logico, a livello file)
Rappresenta un file sorgente prima del chunking.

| Campo | Tipo | Origine | Esempio |
|-------|------|---------|---------|
| `source` | str | nome file | `"Nuovo Codice della Strada.pdf"` |
| `path` | str | percorso relativo | `"data/MIT Decreti PDF/..pdf"` |
| `doc_type` | enum | da estensione/cartella | `pdf` \| `csv` \| `xlsx` \| `docx` \| `json` \| `txt` |
| `category` | enum (opz.) | euristica su nome/cartella (da implementare) | `normativa` \| `prodotto` \| `faq` \| `offerta` \| `elenco` |
| `raw_text` | str | output parser | testo normalizzato |
| `ingested_at` | str/ISO | runtime | `"2026-06-06"` |

### 3.2 Chunk (unità indicizzata) — **entità centrale**
È ciò che vive in Qdrant come **point**: `id` (UUID), **vettori** (`dense` e5 + `bm25` sparse), `payload` (= testo del chunk sotto
la chiave `_text` + i metadati elencati sotto).

| Campo (metadata) | Tipo | Obbligatorio | Scopo | Stato attuale |
|------------------|------|--------------|-------|---------------|
| `source` | str | ✅ | citazione (nome file) | ✅ presente |
| `chunk_id` | int | ✅ | ordine/posizione nel documento | ✅ presente |
| `doc_type` | str | ✅ | filtro/diagnostica | ✅ presente (Fase 1) |
| `category` | str | ➖ | filtraggio per tipo di richiesta | da aggiungere |
| `page` | int | ➖ (PDF) | citazione precisa | ⚠️ **non ottenibile oggi**: `load_pdf` appiattisce tutte le pagine prima del chunk → serve parsing per-pagina |
| `product` | str | ➖ | es. `T-EXCEED`, `Autovelox 106` | da estrarre |
| `decreto` | str | ➖ | es. `3758` | da estrarre |
| `data_decreto` | str | ➖ | es. `06/08/2014` | da estrarre |

> **Vincoli Qdrant:**
> - il `payload` accetta valori scalari **e** strutturati (liste/dict) → metadati più ricchi che con ChromaDB.
> - l'`id` di un point è **int o UUID**: usiamo un **UUID deterministico** (`uuid5` di
>   `source|chunk_id|text`) → `upsert` **idempotente** (RF6), verificato in Fase 1.
> - il testo del chunk vive nel payload (chiave `_text`) e viene ricostruito in fase di search.

### 3.3 Embedding
- **Modello** (`config.EMBEDDING_MODEL`, env-configurabile): **`intfloat/multilingual-e5-small`**
  (locale, CPU). Cambiarlo richiede il **re-index completo** del corpus.
- **Prefissi e5** (`EMBEDDING_PASSAGE_PREFIX`/`EMBEDDING_QUERY_PREFIX`): i documenti sono embeddati con
  `passage: `, le query con `query: ` — ometterli **degrada silenziosamente** il retrieval.
- Distanza: **cosine** (`VectorParams(distance=COSINE)`); dimensione vettore = `get_embedding_dimension()`
  (e5-small → 384). **Stesso modello obbligatorio** in ingestion e query.

### 3.4 QueryLog (riga di log query) — **audit + GDPR**
Una riga per ogni query servita, persistita in **SQLite** (`config.QUERY_LOG_PATH`, default `./query_log.db`).
Serve a analytics/audit (es. *"argomenti più richiesti dal profilo Sales nel 2024"*) **senza** trattenere
identità nel lungo periodo (`src/nextpulse/query_log.py`).

| Campo | Tipo | Note |
|-------|------|------|
| `id` | int (PK) | autoincrement |
| `created_at` | str/ISO-8601 UTC | timestamp della query |
| `user_id` | str \| **NULL** | identificatore client opaco — **PII** (azzerato dall'anonimizzazione) |
| `session_id` | str \| **NULL** | identificatore sessione opaco — **PII** (azzerato dall'anonimizzazione) |
| `role` | str (opz.) | `sales` \| `presales` \| `bid_manager` |
| `question` | str | testo della domanda (trattenuto come **dato statistico**) |
| `standalone_query` | str | query riformulata |
| `confidence` | str | `green` \| `yellow` \| `red` |
| `grounded` / `ambiguous` | int (0/1) | esito di governance |
| `top_score` | real | coseno denso del miglior chunk |
| `n_sources` | int | n. fonti citate |
| `model` | str | slug LLM |
| `anonymized_at` | str/ISO \| NULL | valorizzato quando gli identificatori vengono azzerati |

> **GDPR — Data Anonymization (RF22):** `user_id`/`session_id` sono **dato personale**. Un **job notturno**
> (`scripts/anonymize_logs.py` → `QueryLog.anonymize_older_than(6)`) esegue un `UPDATE` sulle righe più vecchie
> di **6 mesi** (`config.LOG_RETENTION_MONTHS`) impostandoli a `NULL`: il dato residuo resta utile per le
> statistiche ma **esce dal perimetro GDPR**. Le righe **non vengono mai cancellate**; l'operazione è
> **idempotente** (riga già anonimizzata → 0 modifiche). Il logging è **best-effort**: un errore di log
> **non** rompe mai la risposta.

### 3.5 Pseudonimizzazione reversibile (layer PII) — **GDPR Art. 32**
Layer locale "cuscinetto" tra Vector DB e LLM esterno (`src/nextpulse/pseudonymizer.py`). Prima di inviare
qualsiasi testo a OpenRouter, la PII viene **mascherata** con token tracciabili; la risposta viene
**re-identificata in locale**. La mappa `originale↔token` vive **solo in memoria** per la durata della richiesta
ed è distrutta a fine query (zero residuo).

| Concetto | Dettaglio |
|----------|-----------|
| Entità (regex) | `EMAIL`, `IBAN`, `CREDIT_CARD`, `FISCAL_CODE`, `VAT`, **`CIG`**, **`CUP`**, `PHONE`, `MONEY` (€), **`PERCENT`** (margini), `ORG` (Comune/Provincia…), `PERSON` (referente/sig./dott.…) |
| Entità (Presidio opz.) | `PERSON`, `ORG`, `LOCATION` via NER (spaCy/HF) |
| Token | `[<TIPO>_<n>]` (es. `[PERSON_1]`); stesso valore → stesso token nella richiesta |
| Reversibilità | `MaskingSession.unmask()` ripristina gli originali (tollerante a perturbazioni dell'LLM) |
| Backend | `auto` → Presidio se installato, altrimenti **regex** (sempre disponibile, stdlib) |

> **Flusso a 4 fasi:** 1) Rilevamento (NLP/regex locale sui chunk + domanda) · 2) Mascheramento (token + mappa
> in memoria) · 3) Elaborazione esterna *zero-knowledge* (l'LLM manipola i token) · 4) Re-identificazione locale +
> distruzione della mappa. Tutte le chiamate LLM (condense, giudice ambiguità, generazione) passano dal layer.

### 3.6 Bando / Tender (modulo Bandi/Gare R.A.M.) — **corpus separato**
Entità del modulo gare R.A.M. (`src/nextpulse/ram_scraper.py`). I bandi vivono in una **collection
Qdrant dedicata** — `bandi_ram` (costante `BANDI_COLLECTION`) — **separata** da `documents` (KB Engine
SpA), così i due domini non si mescolano mai nel retrieval. La collection riusa **lo stesso client Qdrant
e lo stesso embedder** del processo (l'embedded Qdrant blocca l'intera cartella per processo; un secondo
`SentenceTransformer` sarebbe spreco).

**Tender** (oggetto di business esposto alla UI da `GET /api/bandi`, prodotto dallo scraping):

| Campo | Tipo | Origine | Note |
|-------|------|---------|------|
| `id` | str | portale | id gara |
| `title` | str | portale | oggetto del bando |
| `cig` | str | portale | Codice Identificativo Gara |
| `tipologia` / `stato` | str | portale | tipo procedura · stato grezzo |
| `category` | enum | derivato da `stato` | `in_corso` \| `aggiudicazione` (raggruppamento UI) |
| `data_pubblicazione` / `data_scadenza` | str | portale | scadenze |
| `importo` | str | portale | importo complessivo gara |
| `rup` / `servizio` | str | portale | responsabile · ente/servizio |
| `detail_url` | str | portale | pagina di dettaglio (fonte dei PDF) |
| `documents[]` | list | enrichment | doc indicizzati `{label, url, chunks}` |
| `requirements[]` | list | estrazione | requisiti di partecipazione (RF25) |
| `chunks` | int | indicizzazione | n. chunk indicizzati per il bando |

**Chunk bando** (point in `bandi_ram`): stessa struttura del Chunk §3.2 (vettori dense+BM25, testo in
`_text`, id UUID5 deterministico) con **payload specifico**:

| Campo (metadata) | Tipo | Scopo |
|------------------|------|-------|
| `source` | str | `RAM_bando_<id>_<file>` (o `…_requisiti` per il chunk sintetico) — citazione |
| `chunk_id` | int | ordine nel documento |
| `doc_type` | str | `pdf` (documento di gara) \| `txt` (chunk requisiti sintetico) |
| `category` | str | `bando` (fisso per questo corpus) |
| `gara_category` | str | `in_corso` \| `aggiudicazione` |
| `tender_id` / `tender_title` | str | gara di appartenenza |
| `cig` | str | citazione/riferimento gara |
| `stato` | str | stato grezzo del portale |
| `doc_label` | str | etichetta del documento (es. *Disciplinare*, *Requisiti (estratti)*) |
| `source_url` | str | URL della pagina di dettaglio della gara |

> **Selezione documenti & robustezza:** per ogni bando si scaricano **solo** i documenti che portano
> requisiti (disciplinare/capitolato/bando/esito…, max 4), saltando i form boilerplate e i file macchina
> (`.xml`, `.p7m`, `.zip`…). Un PDF non scaricabile/parsabile viene loggato e saltato, non blocca il bando;
> un bando in errore non blocca lo scraping (stessa filosofia RNF5). Re-index **idempotente** (le `source`
> del bando vengono droppate prima del re-insert).

## 4. Oggetti runtime della pipeline RAG

### 4.1 ChatMessage
```jsonc
{ "role": "user" | "assistant", "content": "string" }
```
La history viene troncata (ultimi 6 messaggi, `rag_chain._build_chat_context`) per limitare i token
nella riformulazione. Nota: la CLI `query_rag.py` **non** passa la history (single-turn); solo
Streamlit usa la chain conversazionale.

### 4.2 QueryResult (output di `RAGChain.query`)
```jsonc
{
  "query": "domanda originale dell'utente",
  "standalone_query": "domanda riformulata, autosufficiente",
  "response": "risposta generata (IT, grounded)",
  "context": ["chunk testuale 1", "chunk testuale 2"],
  "sources": ["Nuovo Codice della Strada.pdf (pag. 116)", "Elenco autovelox MIT.csv"],
  "model": "google/gemma-4-26b-a4b-it:free",
  "grounded": true,
  "ambiguous": false,
  "top_score": 0.906,
  "role": "presales",
  "confidence": "green",
  "pii_masked": 0
}
```
Contratto prodotto da `src/nextpulse/rag_chain.py`, stabile: UI/CLI ci si appoggiano. **`grounded`**
indica se la risposta è stata generata (true) o è il fallback di governance (false); **`top_score`**
è il coseno denso del miglior chunk (gate RF10: < `SCORE_THRESHOLD` → fallback senza generazione).
**`ambiguous=true`** segnala il **gate di ambiguità** (RF19): un giudice LLM ha rilevato fonti in
conflitto → risposta di discrezione (cita le fonti, rimanda al Bid Manager, nessuna interpretazione).
Il retrieval è **hybrid** (dense e5 + BM25, fusione RRF); il gate di rilevanza usa il coseno (scala stabile).
Con un **profilo attivo** (RF20) il `QueryResult` porta anche **`role`** e **`confidence`** (🟢 verde / 🟡 giallo
/ 🔴 rosso): la risposta è adattata al ruolo (Sales/Pre-Sales/Bid Manager) e la confidenza riflette il gate
(rosso = fallback o discrezione). Il modulo standalone `role_manager.py` gestisce prompt, terminologia e formato fonti per ruolo.

### 4.3 Contratto API REST (implementato — `src/nextpulse/api.py`)
Backend FastAPI + frontend Vite/TS/Tailwind:
```
POST /api/query        { "question": str, "history": ChatMessage[], "k"?: int,
                         "role"?: str, "session_id"?: str, "user_id"?: str }  → QueryResponse (= QueryResult + role, confidence)
GET  /api/status       → { "documents": int (distinti), "chunks": int, "model": str }
GET  /api/roles        → [ { "key", "name", "terminology_level", "require_source_citation" } ]        (RF20)
GET  /api/privacy      → { "logging_enabled", "retention_months", "anonymization", "total", "identified", "anonymized", "oldest" }  (RF22)
— modulo Bandi/Gare R.A.M. —
GET  /api/bandi        → { "categories": [ { "key", "label", "tenders": Tender[] } ], "total": int }   (RF24, ultimo scrape)
GET  /api/bandi/scrape → text/event-stream: `data: {phase: listing|tender|done|error, …}`             (RF27, SSE)
POST /api/bandi/query  { "question": str, "history": ChatMessage[], "k"?: int }  → QueryResponse        (RF26, corpus `bandi_ram`)
```
Se `web/dist/` esiste, FastAPI serve anche la UI (single-origin). `session_id`/`user_id` sono identificatori
**opachi** (no PII intrinseca) usati per l'audit log e anonimizzati dopo la retention.

**Hardening sicurezza (RNF6):**
- **Validazione input (Pydantic `Field`)** su `/api/query` e `/api/bandi/query`: `question` 1–4000 char,
  messaggio history 1–8000 char, **max 20** messaggi, `k` ∈ **[1, 20]**, `role` ≤ 32, `session_id`/`user_id`
  ≤ 200 → input fuori limite respinto con **HTTP 422** (difesa da DoS / costo LLM incontrollato).
- **Errori non rivelano internals:** una eccezione nella pipeline → **HTTP 502** con messaggio generico
  ("servizio non momentaneamente disponibile"); l'eccezione/stack/dettaglio provider è solo nel **log
  server-side** (no information disclosure). `/api/bandi/query` → **503** se manca la chiave LLM.

## 5. Configurazione (unica fonte di verità)

Da `config.py` / `.env` (vedi `.env.example`):

| Variabile | Sorgente | Default | Effetto sul dato |
|-----------|----------|---------|------------------|
| `OPENROUTER_API_KEY` | env | *(richiesta)* | key OpenRouter; fallback `OPENAI_API_KEY` |
| `OPENAI_BASE_URL` | env | `https://openrouter.ai/api/v1` | endpoint LLM (OpenRouter) |
| `CHAT_MODEL` | env | `openai/gpt-4o` | slug OpenRouter per condense + generation |
| `EMBEDDING_MODEL` | env | `intfloat/multilingual-e5-small` | embedding locale; cambiarlo = re-index |
| `COLLECTION_NAME` | env | `documents` | nome collection Qdrant |
| `QDRANT_PATH` | env | `./qdrant_data` | store Qdrant embedded locale |
| `QDRANT_URL` | env | *(vuoto)* | se valorizzato, usa un server Qdrant invece del path locale |
| `CHUNK_MAX_TOKENS` / `CHUNK_MIN_TOKENS` | env | `480` / `200` | dimensione chunk in **token** (≤ finestra embedding); `CHUNK_SIZE`/`CHUNK_OVERLAP` legacy |
| `RETRIEVAL_K` | env | `5` | n. chunk recuperati per query |
| `SCORE_THRESHOLD` | env | `0.82` | coseno < soglia → fallback "non in documentazione" (gate RF10) |
| `AMBIGUITY_JUDGE` | env | `1` | abilita il giudice LLM del conflitto fra fonti (gate RF19) |
| `LLM_MAX_RETRIES` | env | `4` | retry con backoff su 429/5xx |
| `DATA_DIR` | env | `./data` | sorgenti da indicizzare (usare `./KNOWLEDGE` per il corpus) |
| `QUERY_LOG_ENABLED` | env | `1` | abilita il log query SQLite (audit/analytics) |
| `QUERY_LOG_PATH` | env | `./query_log.db` | percorso del DB SQLite del log query |
| `LOG_RETENTION_MONTHS` | env | `6` | finestra oltre cui il job notturno anonimizza (`user_id`/`session_id` → NULL) |
| `PII_MASKING_ENABLED` | env | `1` | abilita la pseudonimizzazione reversibile della PII verso l'LLM (Art. 32) |
| `PII_BACKEND` | env | `auto` | rilevatore PII: `auto` (Presidio se installato, altrimenti regex) \| `regex` \| `presidio` |
| `PII_SPACY_MODEL` | env | `it_core_news_lg` | modello spaCy usato dal backend Presidio (da scaricare a parte) |

## 6. Invarianti / regole di integrità

1. **Stesso modello di embedding** in ingestion e query (pena retrieval inutile).
2. **Ogni chunk ha `source`** — senza fonte non può essere citato (RF11).
3. **`id` deterministico** (UUID5 di `source|chunk_id|text`) → upsert idempotente (RF6). ✅ Fase 1.
4. **Nessun dato derivato/inventato** nei metadati: `product`/`decreto` solo se estratti dalla fonte.
5. **Tabelle preservate**: CSV/XLSX vanno a chunk come Markdown coerente, non spezzati a metà riga.
6. **Anonimizzazione GDPR del log:** dopo `LOG_RETENTION_MONTHS` (6) le righe del `query_log` perdono
   `user_id`/`session_id` (→ NULL) ma **non** vengono cancellate; `question` resta come dato statistico. Operazione idempotente.

## 7. Nota di design — dati tabellari vs RAG (RISK-5)

Il CSV autovelox MIT ha migliaia di righe: in un unico chunk Markdown gli embedding rispondono male,
spezzato a metà è peggio. Strategia consigliata:
- **Lookup strutturati** ("l'autovelox X è omologato nel comune Y?") → filtro **pandas/SQL** sul CSV.
- **RAG semantico** → riservato alla prosa (decreti, FAQ, schede prodotto).
- Minimo sindacale se si resta sul solo RAG: chunking **per gruppi di righe**, con header ripetuto.
