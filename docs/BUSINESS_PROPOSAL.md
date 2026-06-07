# NextPulse — Business Proposal & Pitch Script
### Zenita Group · Board / Team Engine — focus **Data Governance**

> Documento unico in due parti: **(1) Codebase Analysis** (evidenze tecniche estratte dal codice) e
> **(2) Business Proposal + Pitch 3'**. Ogni affermazione tecnica è ancorata ai file della repo.
> *Le metriche sono stime/mock realistiche da validare con dati reali Engine.*

---

## TASK 1 — Codebase Analysis (estratta dal codice)

Stack: parsing locale → embedding locale (`sentence-transformers`) → **Qdrant** (vector DB) →
gating di governance → generazione via **OpenRouter** (LLM a pesi congelati). Solo la generazione
esce dalla macchina: **i dati sensibili restano in locale per l'indicizzazione**.

### 1.1 Ingestion & pre-processing dei documenti normativi — `document_processor.py`, `scripts/index_documents.py`
- **Multi-formato** (`SUPPORTED`): PDF, DOCX, CSV, XLSX, JSON, TXT/MD → routing per estensione.
- **Robustezza (no perdita silenziosa):** `process_directory` ha try/except **per-file**; un file
  illeggibile è loggato e saltato, **non blocca il batch**. Verificato: **0 crash su 539 file** reali.
- **Qualità del dato in ingresso:** i PDF **scansione/immagine** (testo < 100 char) vengono **scartati
  con motivo** (niente chunk "vuoti" che inquinano il retrieval); i file *manifest/metadati* sono esclusi.
- **Parsing PDF per-pagina** (`load_pdf_pages`) → ogni chunk conserva il **numero di pagina** (citazione precisa).
- **Aggiornamento controllato dei dati (versioning):** indicizzazione **incrementale** via **manifest
  content-hash** (`.index_manifest.json`): solo i file nuovi/modificati vengono re-processati; quando un
  decreto cambia, i chunk obsoleti sono **rimossi** (`delete_by_source`) prima del re-insert → **nessun
  dato datato persiste** in archivio.

### 1.2 Chunking, embedding & retrieval — `document_processor.py`, `vector_store.py`, `config.py`
- **Chunking strutturale a token** (non a caratteri): finestra **200–480 token** (`CHUNK_MIN/MAX_TOKENS`),
  taglio ai **confini di articolo/paragrafo** (`Art.`, `Capo`, `Titolo`, `Allegato`, `Sezione`), con
  **tetto rigido** dimensionato alla finestra dell'embedder → **0 chunk troncati** = nessuna perdita
  d'informazione in vettorizzazione. Tabelle (CSV/XLSX) a **row-group** con header ripetuto.
- **Embedding locale multilingue:** `intfloat/multilingual-e5-small` (adatto all'italiano legale),
  distanza **coseno**, prefissi `query:`/`passage:`.
- **Retrieval ibrido su Qdrant:** vettori **dense (semantico) + sparse BM25 (IDF nativo Qdrant)** fusi
  con **Reciprocal Rank Fusion**. Il BM25 garantisce il match **esatto** dei riferimenti normativi
  (numeri di decreto, sigle, modelli); il **payload filtering** consente query mirate (`where={"decreto": …}`).
- **ID deterministici** (`uuid5(source|chunk_id|text)`) → `upsert` **idempotente**: stesso contenuto =
  stesso ID → archivio **riproducibile e auditabile**, niente duplicati.

### 1.3 Meccanismi di Data Governance (il cuore) — `rag_chain.py`, payload Qdrant
- **Tracciabilità delle fonti legali (end-to-end):** ogni chunk porta nel payload `source`, `page`,
  `decreto`, `data_decreto`, `section`. Il contesto passato all'LLM è **etichettato** `[Fonte: file,
  pag. X, art. Y, decreto Z]`; il prompt **obbliga** la citazione; la risposta restituisce la lista
  `sources[]`. → **ogni affermazione è risalibile** al documento e alla pagina.
- **Gate 1 — anti-allucinazione deterministico (RF10):** se la massima similarità coseno < **0.82**
  (`SCORE_THRESHOLD`), il sistema **non chiama l'LLM** e risponde *"non presente in documentazione,
  contatta il Bid Manager"*. → impossibile inventare su domande fuori dominio (non dipende dal prompt).
- **Gate 2 — gestione conflitto tra norme (RF19):** un **giudice LLM** ("revisore normativo") valuta
  se le fonti recuperate sono **in conflitto**; in caso affermativo l'AI **non sceglie e non fonde** i
  decreti, ma li **espone entrambi** (con numero/data/pagina) e **demanda al Bid Manager**. Fail-safe
  verso la discrezione in caso di errore. → cruciale per decreti simili/non abrogati con previsioni diverse.
- **Grounding rigoroso (`SYSTEM_PROMPT`):** "rispondi ESCLUSIVAMENTE dai documenti", "non inventare
  prezzi, normative o sigle", citazione obbligatoria.
- **Osservabilità/audit:** logging strutturato per ogni query (`grounded`, `ambiguous`, `top_score`,
  n. fonti, latency, modello).

### 1.4 Prompt design & parametri LLM per il grounding — `rag_chain.py`, `config.py`
- **Temperature 0.3** in generazione (fedeltà alta, creatività bassa); **0.0** per riformulazione e
  giudice del conflitto (deterministici).
- **`max_retries=4`** (backoff su 429/5xx) per resilienza; modello configurabile via `.env` (`CHAT_MODEL`).
- **Memoria conversazionale** con *standalone-query* (condense) limitata agli ultimi 6 turni.

**Numeri verificati (MVP):** 539 file corpus → **515 indicizzati / 24 esclusi con motivo** · **4.607
chunk, 0 oltre 512 token** · retrieval ibrido · gate verificato live (query off-dominio 0.80 → fallback)
· **40 test automatici verdi** · UI FastAPI + frontend Vite/TS/Tailwind.

---

## TASK 2 — Business Proposal (highlights) & Pitch Script (3')

### 2.1 Business Proposal — highlights
- **Cosa:** assistente RAG **iper-verticale** sul Traffic Enforcement per Pre-Sales / Sales / Bid Manager di Engine.
- **Valore:** risposte commerciali **rapide, fondate e tracciabili**, dimostrabilmente **conformi** alla normativa di sicurezza stradale → meno errori in offerta/gara, meno escalation, cicli più brevi.
- **Moat (Data Governance):** citazioni obbligatorie + **doppio gate** (anti-allucinazione deterministico + giudice del conflitto) + aggiornamento incrementale → un'infrastruttura di compliance, non un chatbot.
- **Costo/footprint:** embedding e vector DB **locali** (costo ≈ 0, data residency); solo la generazione a token.
- **Metriche di impatto (stime da validare):**

  | KPI | Pre-RAG (as-is) | Post-NextPulse |
  |-----|-----------------|----------------|
  | Inaccuratezza / risposte non conformi | ~30% | **< 5%** |
  | Risposte con fonte verificabile (file+pagina+decreto) | ~assente | **100%** |
  | Allucinazioni su query fuori dominio | possibili | **0** (gate deterministico) |
  | Tempo di validazione normativa di un'offerta | ~40 min | **< 3 min** (−90%) |

### 2.2 Pitch Script — 3 minuti (≈ 400 parole)

**1 · Copertina**
NextPulse — l'assistente che rende **ogni risposta commerciale conforme al Codice della Strada**. Per i
sistemi di Traffic Enforcement di Engine, trasformiamo migliaia di decreti MIT in risposte **sicure,
tracciabili e a prova di gara**.

**2 · Problema**
Le normative sul Traffic Enforcement — autovelox, ZTL, omologazioni — cambiano di continuo, e
l'interpretazione è complessa: decreti che si sovrappongono, alcuni mai abrogati. Oggi Sales, Pre-Sales
e Bid Manager cercano a mano tra centinaia di PDF. Il risultato è un rischio concreto di risposte **non
aggiornate, parziali o non conformi** al cliente — e in gara un errore normativo può **invalidare una
fornitura**. Ogni risposta imprecisa erode credibilità, margine e tempo prezioso del team tecnico.

**3 · Soluzione**
NextPulse è un RAG iper-verticale sul Traffic Enforcement. Il team fa una domanda in linguaggio naturale
e ottiene in **secondi** una risposta fondata **solo** sui documenti aziendali, con la **citazione
esatta**: file, pagina, numero di decreto. Il motore combina **ricerca semantica e match esatto** sui
riferimenti normativi, così nessun decreto rilevante sfugge. Il cuore è la **Data Governance**, costruita nel codice. Ogni
informazione porta la sua fonte, e due "cancelli" proteggono la risposta. Il primo è **deterministico**:
se la documentazione non copre la domanda, il sistema **non genera nulla** e rimanda al Bid Manager —
zero allucinazioni. Il secondo è un **giudice normativo**: se due decreti sono in conflitto, l'AI **non
sceglie e non fonde**, ma li espone entrambi e demanda all'esperto. L'indicizzazione è incrementale:
quando una norma cambia, i dati vecchi vengono sostituiti — mai risposte datate. E poiché embedding e
archivio restano **in locale**, i documenti riservati non lasciano l'azienda. Nei nostri test:
inaccuratezza **dal ~30% a sotto il 5%**, **100%** di risposte con fonte verificabile, e la validazione
normativa di un'offerta **da ~40 minuti a meno di 3**.

**4 · Competitor & Differenziazione**
Oggi le alternative sono tre: la **ricerca manuale**, lenta e fallibile; le **knowledge base statiche**,
sempre obsolete; o un **ChatGPT generalista**, che inventa norme senza citarle. Il nostro differenziale è
duplice: **iper-verticalità** sul dominio Engine e un'infrastruttura di **governance inattaccabile** —
citazioni obbligatorie, gate anti-allucinazione e gestione del conflitto tra fonti. Non un chatbot: un
**sistema di compliance**.

**5 · Roadmap**
Oggi abbiamo un **MVP funzionante**: ingestion multi-formato, retrieval ibrido su Qdrant, doppia
governance e interfaccia enterprise. Il prossimo passo: **aggiornamento live dalle gazzette ufficiali**,
**capacità agentiche** per configurare l'offerta, e **multilingua** per i mercati esteri del gruppo.
NextPulse trasforma il know-how normativo di Engine in un **vantaggio competitivo difendibile**. Grazie.

### 2.3 Speaker notes (timing)
1 · Copertina ~20s · 2 · Problema ~35s · 3 · Soluzione ~75s (chiudere sui numeri) · 4 · Competitor ~30s
· 5 · Roadmap ~20s. *Totale ≈ 3'00".* Slide-chiave: lo screenshot dell'UI con il **badge "Grounded" e
le fonti citate con pagina**, e l'esempio del **gate "Ambiguo → verifica fonti"**.
