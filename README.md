# NextPulse — AI Sales Assistant per Traffic Enforcement (Engine SpA)

Sistema **RAG** (Retrieval-Augmented Generation) che risponde alle domande del team
commerciale **solo** sulla base della documentazione aziendale, **cita sempre le fonti** e
**dice quando non sa**. Stack ibrido: vector DB locale + embedding locali + LLM via **OpenRouter**.

> **Documenti di progetto:** [REQUISITI.md](./docs/REQUISITI.md) · [MODELLO_DATI.md](./docs/MODELLO_DATI.md) · [KPI.md](./docs/KPI.md) · [PITCH.md](./docs/PITCH.md) · [BUSINESS_PROPOSAL.md](./docs/BUSINESS_PROPOSAL.md)
> **Evento:** Next Pulse — Workshop AI, 6-7 giugno 2026, Live Campus Chieti · **Livello target:** Avanzato (prototipo RAG)

---

## 1. Contesto

**Engine SpA** (gruppo **Zenita**) opera nel **Traffic Enforcement & Smart City**: controllo
velocità/autovelox, gestione ZTL, rilevazione passaggio con semaforo rosso, analytics per la
mobilità urbana. La vendita di queste soluzioni è complessa: il team Sales / Pre-Sales / Bid
Manager deve gestire documentazione tecnica distribuita, offerte storiche, requisiti di gara,
vincoli normativi (decreti MIT, Codice della Strada), configurazioni personalizzate e
stakeholder diversi — il tutto sotto forte **pressione sui tempi**.

Il risultato è un collo di bottiglia commerciale: informazioni sparse, rischio di risposte lente
o incomplete, e necessità di **governance** (grounding sulle fonti, gestione delle allucinazioni,
tracciabilità). NextPulse affronta esattamente questo: non "un chatbot", ma un **assistente
decisionale** per vendere meglio soluzioni complesse.

## 2. Requisiti (sintesi)

> Dettaglio completo, casi d'uso e matrice di tracciabilità in **[REQUISITI.md](./docs/REQUISITI.md)**.

**Obiettivi & KPI candidati**

| Obiettivo | KPI |
|-----------|-----|
| Ridurre il tempo di recupero info | tempo medio risposta: minuti → secondi |
| Ridurre errori/risposte incomplete | % risposte con fonte verificabile; tasso "non lo so" corretto |
| Supportare offerte/gare | richieste evase senza escalation al Bid Manager |
| Affidabilità/tracciabilità | % risposte con citazione; 0 prezzi/sigle/norme inventati |

**Utenti:** Sales, Pre-Sales, Bid Manager, Product expert, Customer Success.

**Requisiti funzionali chiave**
- **Ingestion** multi-formato (PDF, DOCX, CSV, XLSX, JSON, TXT) con metadati per la citazione.
- **Conversational Retrieval Chain**: riformulazione della query sulla chat history → retrieval top-K → generazione.
- **Grounding & anti-allucinazione**: rispondere solo dai documenti; fallback esplicito se l'informazione non c'è.
- **Citazione delle fonti** sempre presente; UI di chat con pannello fonti e stato della knowledge base.

**Governance (priorità alta per la valutazione):** affidabilità delle fonti, gestione delle
allucinazioni, tracciabilità, limiti dichiarati del sistema. I dati possono essere sintetici/modificati.

## 3. Modello dati (sintesi)

> Dettaglio completo (entità, schema metadati, config, invarianti) in **[MODELLO_DATI.md](./docs/MODELLO_DATI.md)**.

```
File grezzi (data/) → parsing per estensione → Documento → chunking → Chunk (testo + metadati)
   → embedding (locale, e5) → Qdrant ──(retrieval top-K)──► Contesto ──► Risposta (con fonti)
```

- **Chunk** (entità centrale, vive in Qdrant come *point*): `id` (UUID), **vettori** (dense e5 + sparse BM25),
  payload = testo + metadati di citazione (`source`, `chunk_id`, `doc_type`, `page`, `decreto`, `data_decreto`).
- **QueryResult** (contratto stabile di `RAGChain.query`): `{query, standalone_query, response,
  context[], sources[], model, grounded, ambiguous, top_score, role, confidence}`.
- **QueryLog** (audit/GDPR, SQLite): una riga per query con identificatori **opachi** (`user_id`,
  `session_id`) azzerati dal job notturno di anonimizzazione dopo 6 mesi (righe mai cancellate).
- **Sorgenti reali:** `KNOWLEDGE/` (≈539 file, prevalentemente PDF normativi + tabelle MIT).
- **Invarianti:** stesso modello di embedding in ingestion e query; ogni chunk ha una `source`;
  ID deterministici per re-index idempotente; tabelle preservate nel chunking.

## 4. Architettura

```
                         ┌─── INDEXING (offline, locale) ───┐
PDF/DOCX/CSV/XLSX/JSON/TXT (data/) → chunk → embed (e5-small, locale) → Qdrant (embedded, su disco)

                         ┌─── QUERY (online) ───────────────────────────────────────────┐
User query → condense (memoria conversazionale) → retrieve top-K (HYBRID dense e5 + BM25, RRF)
   → gate rilevanza (coseno < soglia → "non in doc") → gate ambiguità (giudice LLM: fonti in conflitto
   → discrezione) → build prompt (citazioni [Fonte: file, pag.]) → [MASKING PII locale] → OpenRouter LLM
   → [DE-MASKING locale] → risposta adattata al PROFILO attivo (Sales/Pre-Sales/Bid Manager) + confidence
   → fonti → audit log (SQLite, anonimizzato dopo 6 mesi)
```

| Fase | Dove | Costo |
|------|------|-------|
| Parsing & chunking | CPU locale (`pypdf`) | gratis |
| Embeddings | `sentence-transformers` (locale) | gratis |
| Vector storage & retrieval **hybrid** (dense + BM25) | `qdrant-client` (embedded locale, su disco) | gratis |
| Condense + generation | **OpenRouter** (API OpenAI-compatibile) | a token |

> **LLM via OpenRouter:** la generazione e la riformulazione passano da OpenRouter (compatibile con
> l'SDK OpenAI via `base_url`). Gli **embedding restano locali** (OpenRouter non espone un endpoint
> embeddings). Questo azzera il costo dell'indicizzazione e tiene i dati in locale per l'ingestion.

**Stato — piano completo (Fasi 0–7 + governance ambiguità):** ingestion multi-formato, embedding
`multilingual-e5-small`, **Qdrant** con retrieval **hybrid** (dense + BM25, RRF), chunking strutturale a
token con citazione per pagina, **doppia governance** (gate anti-allucinazione su score + **gate di
ambiguità** con giudice LLM → discrezione), **role-awareness** (3 profili + confidence), **privacy by design
GDPR** (audit log + anonimizzazione notturna + **pseudonimizzazione reversibile della PII** verso l'LLM, Art. 32),
**UI FastAPI + frontend Vite/TS/Tailwind**, **hardening** (indicizzazione incrementale, retry, logging) e
**69 test**. *(Rinviato: OCR scansioni — serve il language pack `ita`.)*

### Data Governance & privacy (GDPR)
- **Grounding & anti-allucinazione**: il gate deterministico rifiuta fuori dominio (nessuna generazione).
- **Tracciabilità**: ogni risposta cita file + pagina; il `QueryResult` espone `grounded`/`ambiguous`/`confidence`.
- **Role-awareness**: tono, terminologia e citazioni adattati al profilo attivo (Sales/Pre-Sales/Bid Manager).
- **Audit log**: una riga SQLite per query (`src/nextpulse/query_log.py`) con identificatori **opachi**.
- **Pseudonimizzazione reversibile (Art. 32)**: un layer locale (`src/nextpulse/pseudonymizer.py`) maschera la
  PII (nomi, email, IBAN, CIG/CUP, importi €, margini %, Comuni) con token `[PERSON_1]`… **prima** dell'invio a
  OpenRouter e la **re-identifica in locale** sulla risposta — l'LLM lavora *zero-knowledge*. Backend regex sempre
  attivo; **Microsoft Presidio** (NER) opzionale (`uv sync --extra pii`).
- **Data Anonymization**: il job notturno `scripts/anonymize_logs.py` azzera `user_id`/`session_id` sui log
  oltre **6 mesi** (`LOG_RETENTION_MONTHS`) — il dato resta statistico ma esce dal GDPR; righe **mai cancellate**,
  operazione **idempotente**. Stato visibile da `GET /api/privacy`.
  ```bash
  # dry-run (nessuna scrittura) e poi esecuzione
  uv run python scripts/anonymize_logs.py --dry-run
  uv run python scripts/anonymize_logs.py            # cron notturno: 30 2 * * *
  ```

## 5. Configurazione

Tutto in `.env` (vedi `.env.example` — fonte di verità in [MODELLO_DATI.md §5](./docs/MODELLO_DATI.md)):

| Variabile | Default | Note |
|-----------|---------|------|
| `OPENROUTER_API_KEY` | *(richiesta)* | key OpenRouter (`sk-or-…`); fallback `OPENAI_API_KEY` |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | endpoint OpenRouter |
| `CHAT_MODEL` | `openai/gpt-4o` | qualsiasi slug OpenRouter (es. `anthropic/claude-3.5-sonnet`) |
| `COLLECTION_NAME` | `documents` | nome collection Qdrant |
| `QDRANT_PATH` | `./qdrant_data` | store Qdrant embedded locale (`QDRANT_URL` per un server) |
| `CHUNK_MAX_TOKENS` / `CHUNK_MIN_TOKENS` | `480` / `200` | dimensione chunk in **token** (≤ finestra embedding) |
| `RETRIEVAL_K` | `5` | chunk recuperati per query |
| `SCORE_THRESHOLD` | `0.82` | sotto soglia → "non in documentazione" (gate anti-allucinazione) |
| `AMBIGUITY_JUDGE` | `1` | giudice LLM del conflitto tra fonti (→ discrezione) |
| `DATA_DIR` | `./data` | sorgenti da indicizzare (`./KNOWLEDGE` per il corpus) |
| `QUERY_LOG_ENABLED` | `1` | abilita l'audit log delle query (SQLite) |
| `QUERY_LOG_PATH` | `./query_log.db` | percorso del DB del log query |
| `LOG_RETENTION_MONTHS` | `6` | retention oltre cui il job notturno anonimizza (user_id/session_id → NULL) |
| `PII_MASKING_ENABLED` | `1` | pseudonimizzazione reversibile della PII verso l'LLM (Art. 32) |
| `PII_BACKEND` | `auto` | rilevatore PII: `auto` (Presidio se installato, altrimenti regex) \| `regex` \| `presidio` |
| `PII_SPACY_MODEL` | `it_core_news_lg` | modello spaCy del backend Presidio (`uv sync --extra pii` + `spacy download`) |

> `EMBEDDING_MODEL` è in `.env` (default `multilingual-e5-small`): cambiarlo richiede il re-index.

## 6. Quick Start

```bash
uv sync                                  # installa dipendenze + pytest (Python 3.10+)
cp .env.example .env                     # poi inserisci la tua OPENROUTER_API_KEY
cp KNOWLEDGE/<alcuni-file>.pdf data/     # o imposta DATA_DIR=./KNOWLEDGE

uv run python scripts/index_documents.py # indicizza (embedding locali, no API)

# UI principale: backend FastAPI + frontend Vite/TS/Tailwind
uv run uvicorn src.nextpulse.api:app --port 8000     # backend (API + serve web/dist se buildato)
cd web && npm install && npm run dev                 # frontend dev su :5173 (proxy → :8000)
# produzione single-origin: cd web && npm run build  → poi tutto da uvicorn su :8000

# governance GDPR: anonimizzazione notturna dei log (user_id/session_id → NULL oltre 6 mesi)
uv run python scripts/anonymize_logs.py --dry-run    # anteprima; poi senza --dry-run (cron: 30 2 * * *)

# alternative
streamlit run scripts/app.py             # vecchia UI Streamlit
uv run python scripts/query_rag.py --role bid_manager  # CLI (multi-turn, profilo selezionabile)
uv run pytest tests/ -q                  # 69 test (LLM mockato)

# (opzionale) abilita il NER di Microsoft Presidio per il masking PII di nomi/organizzazioni
uv sync --extra pii && uv run python -m spacy download it_core_news_lg
```

> Gli script Python includono lo shim `sys.path`: girano con `uv run` dalla root del progetto.

## 7. Valore business

> Analisi tecnica dettagliata, pitch script e metriche di impatto in [BUSINESS_PROPOSAL.md](./docs/BUSINESS_PROPOSAL.md).

NextPulse è uno strumento **interno B2B**: gli utenti sono i team commerciali di Engine SpA
(Sales, Pre-Sales, Bid Manager); il valore è **efficienza e win-rate**, non ricavo diretto.

| KPI | Pre-RAG | Post-NextPulse |
|-----|---------|----------------|
| Inaccuratezza / risposte non conformi | ~30% | **< 5%** |
| Risposte con fonte verificabile | ~assente | **100%** |
| Allucinazioni su query fuori dominio | possibili | **0** (gate deterministico) |
| Tempo di validazione normativa offerta | ~40 min | **< 3 min** |

**Rischi da dichiarare:** dipendenza dalla qualità/aggiornamento KB; dati potenzialmente sintetici;
necessità di verifica umana su prezzi e dati critici di gara; costo variabile API LLM.
Dettaglio in [REQUISITI.md §8](./docs/REQUISITI.md).

## 8. Roadmap

**Aperto / in backlog:**

| Priorità | Item | Blocco |
|----------|------|--------|
| Alta | OCR delle 19 scansioni non indicizzate | manca `tesseract-data-ita` (pacchetto di sistema) |
| Alta | Modello a pagamento per demo affidabile | es. `google/gemini-2.0-flash-001` vs free rate-limited |
| Media | Streaming risposte (SSE) | — |
| Media | Caching risposte / embedding frequenti | — |
| Media | Re-ranking cross-encoder | migliora precisione retrieval |
| Bassa | Enrichment metadati via manifest MIT | join decreto→titolo/data |
| Bassa | Live-fetch gazzette ufficiali | integrazione fonte esterna |
| Bassa | Capacità agentiche (configuratore offerta) | — |
| Bassa | Multilingua (mercati esteri gruppo Zenita) | — |

## 9. Struttura del progetto

```
NextPulse/
├── src/nextpulse/
│   ├── config.py              # .env + settings (OpenRouter, embedding, chunk, paths, log)
│   ├── document_processor.py  # parsing multi-formato + chunking
│   ├── vector_store.py        # Qdrant (embedded) hybrid dense+BM25 + sentence-transformers
│   ├── rag_chain.py           # Condense → Retrieve → Gate → Mask → Generate → Unmask (prompt IT, ruolo)
│   ├── pseudonymizer.py       # pseudonimizzazione reversibile PII (regex + Presidio opzionale)
│   ├── query_log.py           # audit log query (SQLite) + anonimizzazione GDPR
│   └── api.py                 # backend FastAPI (/api/status, /api/query, /api/roles, /api/privacy; serve web/dist)
├── role_manager.py            # modulo standalone: 3 profili (Sales/Pre-Sales/Bid Manager) + confidence
├── web/                       # frontend Vite + TypeScript + Tailwind (chat UI + selettore profilo)
│   ├── src/main.ts · src/style.css · index.html · package.json · vite.config.ts
├── scripts/
│   ├── index_documents.py     # indicizza i file in data/
│   ├── ingest_mit.py          # ingestion decreti MIT via ScraipingListingPagina/ (bridge)
│   ├── query_rag.py           # Q&A da terminale (multi-turn, --role)
│   ├── anonymize_logs.py      # job notturno GDPR: anonimizza i log oltre la retention
│   └── app.py                 # UI Streamlit alternativa (deprecata)
├── tests/test_rag.py          # 69 test (LLM mockato)
├── data/                      # documenti da indicizzare (gitignored)
├── qdrant_data/               # store Qdrant embedded (gitignored)
├── KNOWLEDGE/                 # corpus reale fornito (PDF normativi, CSV/XLSX/DOCX/JSON)
├── docs/
│   ├── REQUISITI.md · MODELLO_DATI.md · KPI.md          # requisiti, modello dati, metriche
│   └── PITCH.md · PITCH_SLIDES.md · BUSINESS_PROPOSAL.md  # materiali pitch
├── .env.example · pyproject.toml · README.md
```

## 10. Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| `OPENROUTER_API_KEY … not set` | crea `.env` da `.env.example` e inserisci la key |
| `No module named 'src'` (script CLI) | lancia con `PYTHONPATH=.` (la UI Streamlit lo gestisce già) |
| `No documents found` | metti file in `data/` (o `DATA_DIR=./KNOWLEDGE`), poi re-indicizza |
| Errori Qdrant | `rm -rf qdrant_data/` e re-indicizza |
| Prima query lenta | il modello embedding si scarica una volta (~30 s), poi è in cache |

## 11. Dipendenze principali

**Backend:** `qdrant-client` (vector DB) · `sentence-transformers` (embedding locali) · `openai`
(client → OpenRouter) · `pypdf`/`pandas`/`openpyxl`/`python-docx` (parsing) · `fastapi`+`uvicorn` (API)
· `streamlit` (UI alternativa) · `pytest` (test).
**Frontend** (`web/`): `vite` · `typescript` · `tailwindcss` v4.

## Licenza

MIT
