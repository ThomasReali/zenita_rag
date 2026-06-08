# NextPulse — Documento dei Requisiti

> **Stato:** bozza riveduta (rev. 4 — modulo Bandi/Gare MIT + hardening sicurezza API) · **Data:** 2026-06-07 · **Owner:** team NextPulse
> Documento di lavoro interno. Descrive **cosa** deve fare l'AI Sales Assistant per Engine SpA
> e i casi d'uso che deve coprire. Compagno di [MODELLO_DATI.md](./MODELLO_DATI.md) (com'è
> strutturato il dato — **unica fonte di verità per la configurazione**) e [BRIEF.md](./BRIEF.md)
> (come lo costruiamo, a fasi).

## 0. Contesto in una riga

Engine SpA (gruppo Zenita) vende sistemi di **Traffic Enforcement & Smart City** (controllo
velocità/autovelox, gestione ZTL, rilevazione semaforo rosso, analytics mobilità). Il team
commerciale perde tempo a recuperare informazioni tecniche, normative, offerte e casi cliente
sparse su fonti eterogenee, sotto forte pressione sui tempi. NextPulse è un **assistente
conversazionale grounded** (RAG) che risponde **solo** sulla base della documentazione aziendale,
**cita sempre le fonti** e **dice quando non sa**.

- **Evento:** Next Pulse — Workshop AI, 6-7 giugno 2026, Live Campus Chieti.
- **Livello target:** Avanzato (prototipo tecnico RAG funzionante + demo live + spiegazione).
- **Vincolo trasversale:** il prototipo conta, ma conta di più il *processo* (problem solving,
  prioritizzazione, valore business, governance).

## 1. Obiettivi del prodotto

| # | Obiettivo | Come si misura (KPI candidato) |
|---|-----------|--------------------------------|
| O1 | Ridurre il tempo di recupero informazioni commerciali/tecniche | Tempo medio risposta: minuti → secondi |
| O2 | Ridurre errori e risposte incomplete al cliente | % risposte con fonte verificabile; tasso "non lo so" corretto |
| O3 | Supportare la preparazione di offerte e gare | N. richieste evase senza escalation al Bid Manager |
| O4 | Guidare la configurazione delle soluzioni | N. configurazioni proposte coerenti coi vincoli normativi |
| O5 | Garantire affidabilità e tracciabilità (governance) | % risposte con citazione; 0 prezzi/sigle/norme inventati |

## 2. Utenti e stakeholder

| Utente | Bisogno primario | Esempio di domanda |
|--------|------------------|--------------------|
| **Sales** | Risposte rapide e sicure davanti al cliente | "Il T-EXCEED è omologato per la velocità istantanea?" |
| **Pre-Sales** | Dettaglio tecnico, configurazioni, confronto prodotti | "Differenza tra rilevatore mobile e postazione fissa?" |
| **Bid Manager** | Requisiti di gara, vincoli normativi, riferimenti decreti | "Quali decreti MIT regolano l'approvazione autovelox?" |
| **Product expert** | Verifica/aggiornamento knowledge base | (lato ingestion, non solo query) |
| **Customer success** | Casi cliente, FAQ, post-vendita | "C'è un caso analogo per un Comune medio?" |

Stakeholder non-utenti: il **cliente finale** (Comune/PA) di cui si prepara l'offerta; i **mentor**
della challenge (Anceschi, Pastore, Guida) come fonte di requisiti via discovery.

## 3. Requisiti funzionali

### RF — Ingestion (offline)
- **RF1** Indicizzare documenti multi-formato: **PDF, DOCX, CSV, XLSX, JSON, TXT** (il dataset reale
  è 539 file in `KNOWLEDGE/`, di cui ~530 PDF + tabelle MIT; il file `.url` è un collegamento e
  **non è un documento indicizzabile**).
- **RF2** Routing del parser per estensione; CSV/XLSX convertiti in Markdown tabellare leggibile.
- **RF3** Chunking che **non spezzi** tabelle e blocchi tecnici (semantico/strutturale, non solo a
  caratteri fissi).
- **RF4** Ogni chunk porta metadati per la citazione (vedi MODELLO_DATI): `source`, `chunk_id`,
  tipo documento, e quando disponibile prodotto/decreto/data/pagina.
- **RF5** Persistenza locale in vector store. *[ChromaDB PersistentClient; migrazione a **Qdrant** pianificata in Fase 2]*
- **RF6** Re-indicizzazione **idempotente** (ID chunk deterministici). ✅ Implementato in Fase 1
  (hash `source|chunk_id|text` + `upsert`).

### RF — Query (online)
- **RF7** **Conversational Retrieval Chain**: riformulare la query in una *standalone query* usando
  la chat history (Standalone Query Generation via LLM). *[presente in Streamlit; assente nella CLI]*
- **RF8** Recupero top-K per similarità semantica dal vector store. *[già presente]*
- **RF9** Generazione **grounded**: la risposta usa esclusivamente i chunk recuperati. *[già presente]*
- **RF10** **Anti-allucinazione**: se l'informazione non è nei documenti, rispondere con un fallback
  esplicito ("non presente nella documentazione, contatta il Bid Manager"); mai inventare prezzi,
  sigle, normative. *Vedi C-4: rendere il fallback deterministico via soglia di distanza.*
- **RF11** **Citazione delle fonti** sempre presente (nome file + se possibile pagina/sezione).
- **RF12** Tono Pre-Sales: professionale, sintetico, strutturato a elenchi. *[già nel system prompt]*

### RF — Interfaccia
- **RF13** UI di chat con cronologia conversazione. *[già presente in `scripts/app.py`]*
- **RF14** Pannello fonti: mostrare i documenti/chunk usati per ogni risposta. *[già presente —
  espander "Fonti utilizzate"; manca l'anteprima del chunk]*
- **RF15** Indicatore stato knowledge base. ⚠️ Oggi mostra il **conteggio chunk** etichettato come
  "Documenti indicizzati": il sistema conosce solo i chunk, non i documenti distinti. Da relabel o
  da calcolare i `source` distinti.
- **RF16** (Nice-to-have) reset conversazione, esempi di domande, export conversazione per il Bid Manager.

### RF — Governance (priorità alta per la valutazione)
- **RF17** Limiti dichiarati del sistema (cosa non risponde) visibili all'utente.
- **RF18** I dati possono essere sintetici/modificati: trattare le fonti come "verificabili ma non
  garantite", invitando alla verifica quando il dato è critico (prezzi, gara).
- **RF19** **Disambiguazione prudente (rischio legale):** se più provvedimenti/decreti risultano in
  conflitto (rilevato da un **giudice LLM**), rispondere **con discrezione** — citare le fonti distinte
  (decreto/data/pagina) e **rimandare al Bid Manager**, **senza** interpretare né risolvere il conflitto.
  Mai dedurre lo stato di vigenza/abrogazione. *(campo `ambiguous=true` nel `QueryResult`)*
- **RF20** **Role-awareness:** tre profili — in ordine di priorità crescente (dal più basso al più alto):
  **Pre-Sales**, **Sales**, **Bid Manager** — selezionabili da un controllo dedicato in UI. Ogni profilo ha
  system prompt, livello terminologico (tecnico/cliente/legale) e formato fonti propri; la risposta espone una
  **confidenza** (🟢 verde / 🟡 giallo / 🔴 rosso). L'ordine del registry `ROLES` è la fonte di verità: `GET
  /api/roles` lo itera e la UI rende il selettore nello stesso ordine. *(modulo standalone `role_manager.py`;
  campi `role`/`confidence` nel `QueryResult`)*
- **RF21** **Audit log delle query:** una riga per query (SQLite) con identificatori **opachi** (`user_id`,
  `session_id`), ruolo, esito di governance, confidenza, n. fonti. Logging **best-effort**: non blocca mai la risposta.
- **RF22** **GDPR — Data Anonymization:** **job notturno** che azzera (`UPDATE … SET user_id=NULL, session_id=NULL`)
  le righe di log più vecchie di **6 mesi** → il dato resta statistico ma esce dal perimetro GDPR; righe **mai
  cancellate**, operazione **idempotente**. Stato esposto da `GET /api/privacy`. *(`scripts/anonymize_logs.py`)*
- **RF23** **Pseudonimizzazione reversibile (GDPR Art. 32):** un **layer locale** tra Vector DB e LLM esterno
  maschera la PII (nomi, email, IBAN, codice fiscale, P.IVA, **CIG/CUP**, importi €, **marginalità %**, Comuni)
  con token tracciabili (`[PERSON_1]`…) **prima** dell'invio a OpenRouter e la **re-identifica in locale** sulla
  risposta (elaborazione *zero-knowledge*). Backend regex sempre attivo, **Microsoft Presidio** (NER) opzionale.
  *(modulo `pseudonymizer.py`; mappa effimera distrutta a fine richiesta; campo `pii_masked` nel `QueryResult`)*

### RF — Bandi / Gare d'appalto (modulo Portale Appalti MIT)
> Modulo aggiuntivo e **autonomo** rispetto alla KB Engine SpA: assiste l'**Ufficio Gare** di
> del Ministero delle Infrastrutture e dei Trasporti a leggere requisiti, scadenze, importi e
> condizioni dei bandi pubblicati sul Portale Appalti MIT (stato «In corso» e «In aggiudicazione»). Corpus tenuto **separato** dalla
> documentazione aziendale (collection Qdrant dedicata) così i due domini non si mescolano mai.

- **RF24** **Scraping & ingestion bandi:** estrarre i bandi dal Portale Appalti MIT (stato «In corso» e «In aggiudicazione»)
  (web service JSON), **categorizzarli** in *Bandi in corso* / *Bandi in aggiudicazione* (mappatura
  dallo `stato` del portale), scaricare per ogni bando **solo** i documenti che portano requisiti
  (disciplinare, capitolato, bando, esito/verbale — boilerplate come DGUE/privacy/modello-offerta
  scartati; max 4 doc/bando), riusare lo stesso chunking strutturale a token e **indicizzare** in una
  **collection Qdrant dedicata** (`bandi_mit`, separata da `documents`). Re-index **idempotente** (drop
  delle source del bando prima del re-insert). *(`src/nextpulse/bandi_scraper.py`; `scripts/ingest_bandi.py`)*
- **RF25** **Estrazione requisiti di partecipazione:** per ogni bando, euristica che individua il
  blocco "requisiti …" (idoneità professionale / capacità economico-finanziaria / tecnico-professionale /
  ordine generale) e ne ricava un elenco; viene creato un **chunk sintetico "Requisiti (estratti)"**
  recuperabile, così il chatbot li può esporre anche quando sono sparsi su più PDF.
- **RF26** **Chatbot bandi grounded:** chatbot RAG **ristretto al solo corpus bandi** (system prompt
  dedicato alle gare MIT; fallback esplicito "informazione non presente nei documenti di gara
  indicizzati"). Riusa l'intera pipeline RAG (retrieval hybrid, gate, citazioni). *(`POST /api/bandi/query`)*
- **RF27** **Avanzamento scraping in tempo reale (SSE):** l'operazione di scraping emette eventi di
  progresso (`listing`/`tender`/`done`/`error`) via **Server-Sent Events**; la UI mostra spinner +
  avanzamento live e renderizza ogni bando man mano che viene indicizzato. *(`GET /api/bandi/scrape`,
  `GET /api/bandi`; frontend `web/src/bandi.ts`)*
- **RF28** **Governance obsolescenza & data-poisoning (audit DETERMINISTICO, no-LLM):** ogni chunk porta
  uno `status` (`active`/`obsolete`/`poisoned`/`draft`). Il retrieval lo filtra in modo deterministico
  (`must_not` su `EXCLUDED_STATUSES`, back-compatible coi chunk legacy senza status). Se il match più
  pertinente è **abrogato**, la chain restituisce un **avviso costruito dai metadati** (`replaced_by`/
  `validity_end`) invece del generico "non lo so" (`obsolete=true`, no LLM). Un **job notturno ibrido**
  (`scripts/audit_obsolescence.py`: master file gestionale + Normattiva best-effort/Fase 2) flippa lo
  `status` via `set_payload` (nessun re-embedding); la **quarantena anti-poisoning**
  (`scripts/quarantine_source.py`) nasconde (`poisoned`) o **elimina fisicamente** (`--delete`, per il
  diritto all'oblio GDPR Art. 17). Ogni cambio è tracciato in un **log immutabile** (`governance_log.py`,
  NIS2). *(`src/nextpulse/{vector_store,rag_chain,governance_log}.py`)*

## 4. Requisiti non funzionali

- **RNF1** Esecuzione **locale** sul portatile (CachyOS); LLM via **API OpenAI**. ⚠️ Dipende da
  RISK-1 (la challenge fornisce una *licenza ChatGPT*, che **non è** una API key) — vedi §8.
- **RNF2** Embedding locali su CPU (modello multilingue IT-friendly) — costo zero, nessun dato
  sensibile fuori dalla macchina per l'indicizzazione.
- **RNF3** Latenza risposta accettabile per demo live (pochi secondi).
- **RNF4** Setup riproducibile (`.env`, dipendenze pinnate) per "collegare i cavi" il giorno della demo.
- **RNF5** Robustezza a dataset "sporco": un file illeggibile non deve bloccare l'intera ingestion.
  ✅ Implementato in Fase 1 (try/except per-file + report; 0 crash sui 539 file).
- **RNF6** **Hardening sicurezza API.** ✅ Implementato.
  - **Validazione input al bordo:** ogni richiesta `/api/query` e `/api/bandi/query` ha limiti rigidi
    (lunghezza `question` ≤ 4000, messaggio history ≤ 8000, max 20 messaggi, `k` ∈ [1, 20], id opachi
    ≤ 200) → payload abusivi/sovradimensionati respinti con **HTTP 422** (difesa da DoS / costo LLM
    incontrollato). *(vincoli Pydantic `Field` in `src/nextpulse/api.py`)*
  - **No information disclosure:** gli errori della pipeline non espongono mai eccezione/stack/dettagli
    del provider al client → messaggio generico HTTP **502**, dettaglio solo nel log server-side.
  - *Iniezioni:* l'audit log SQLite usa **solo query parametrizzate** (nessuna concatenazione di input
    → SQL injection non applicabile); il vector store non costruisce query da stringhe utente.
  - *Aperto (backlog):* rate-limiting per IP/sessione e ruolo da **identità autenticata** server-side
    (oggi il `role` è selezionato dal client → governance, non sicurezza — vedi §6).

## 5. Casi d'uso

> Formato: Attore · Precondizione · Flusso · Esito atteso · Edge case

### UC1 — Risposta tecnica grounded con citazione
- **Attore:** Sales. **Pre:** KB indicizzata.
- **Flusso:** chiede "Che tipo di rilevatore è il modello X?" → riformulazione → retrieval → risposta.
- **Esito:** risposta sintetica + fonte (es. `Elenco autovelox MIT.csv` / scheda prodotto).
- **Edge:** modello non presente → fallback anti-allucinazione (RF10).

### UC2 — Domanda di follow-up (memoria conversazionale)
- **Attore:** Pre-Sales. **Pre:** turno precedente su un prodotto.
- **Flusso:** "quanto costa?" → la chain riformula in "quanto costa il prodotto X?" → retrieval mirato.
- **Esito:** la risposta tiene conto del contesto senza che l'utente ripeta il soggetto.
- **Edge:** soggetto ambiguo → l'assistente chiede una domanda di chiarimento.

### UC3 — Requisito di gara / normativa
- **Attore:** Bid Manager. **Pre:** decreti MIT + Codice della Strada indicizzati.
- **Flusso:** "Quali requisiti di omologazione per autovelox in strada urbana?"
- **Esito:** risposta con riferimento al/i decreto/i e citazione del file (idealmente pagina).
- **Edge:** norma non coperta dalla KB → fallback + suggerimento di verifica.

### UC4 — Confronto / configurazione soluzione
- **Attore:** Pre-Sales. **Flusso:** "Per un Comune medio che vuole ZTL + velocità, cosa propongo?"
- **Esito:** elenco soluzioni pertinenti dalla documentazione, con fonti; nessuna garanzia di prezzo.
- **Edge:** richiesta che mescola dati certi e stime → separare ciò che è documentato da ciò che va verificato.

### UC5 — Knowledge base vuota / fonte mancante
- **Attore:** qualsiasi. **Flusso:** query senza chunk pertinenti recuperati.
- **Esito:** messaggio onesto "informazione non presente", nessuna invenzione.

### UC6 — Ingestion di nuovo materiale
- **Attore:** Product expert. **Flusso:** aggiunge file in `data/`, lancia indicizzazione.
- **Esito:** nuovi chunk disponibili alla query; conteggio KB aggiornato in UI.
- **Edge:** file corrotto/non supportato → skippato con log, ingestion prosegue (RNF5).

### UC7 — Bandi/gare MIT: scraping e interrogazione (modulo bandi)
- **Attore:** Ufficio Gare. **Pre:** Portale Appalti MIT raggiungibile.
- **Flusso:** avvia lo scraping dei bandi → vede l'avanzamento live (SSE) e i bandi indicizzati
  raggruppati (in corso / aggiudicazione) → chiede "quali sono i requisiti di partecipazione del
  bando X?" → retrieval sul corpus bandi → risposta con requisiti + fonte (CIG/bando).
- **Esito:** requisiti/scadenze/importi sintetizzati e **citati**; risposte solo dai documenti di gara.
- **Edge:** informazione non nei bandi indicizzati → fallback "non presente nei documenti di gara";
  un PDF non scaricabile/parsabile non blocca il bando né lo scraping (robustezza, cfr. RNF5).

## 6. Fuori scope (per l'hackathon) → alimenta "rischi e limiti" del pitch

- Autenticazione/SSO e permessi *per utente* (la **role-awareness** RF20 introduce i 3 profili e gli
  identificatori opachi per l'audit, ma **non** è autenticazione: il profilo è selezionato, non verificato).
- Deploy cloud (Azure non obbligatorio; si gira in locale + API).
- Fine-tuning del modello (RAG = pesi congelati).
- Generazione automatica dell'offerta finale (al massimo bozza/supporto).

## 7. Matrice di tracciabilità (RF ↔ UC ↔ Fase)

> Fasi rinumerate dopo l'inserimento di **Fase 2 — Migrazione a Qdrant** (vedi BRIEF).

| RF | Copre UC | Fase | Stato |
|----|----------|------|-------|
| RF1, RF2 | UC6 | 1 | ✅ fatto |
| RF3, RF4, RF11 | UC1, UC3 | 3 | ✅ fatto (chunking a token + page/decreto/section) |
| RF5, RF8 | tutti | 0 → 2 → 4 | ✅ Qdrant; retrieval **hybrid** (dense e5 + BM25, RRF) in Fase 4 |
| RF6 | UC6 | 1 | ✅ fatto (ID deterministici) |
| RF7 | UC2 | 4 | ✅ fatto (Streamlit + CLI) |
| RF9, RF12 | UC1, UC3, UC4 | 4 | ✅ fatto (citazioni `[Fonte: …]` con pagina) |
| RF10, RF18 | UC4, UC5 | 4 | ✅ fatto (gate deterministico su score < 0.82) |
| RF19 | UC4 | 4 | ✅ fatto (gate ambiguità: giudice LLM → discrezione + rimando al Bid Manager) |
| RF13, RF14, RF15 | UC1, UC6 | 5 | ✅ fatto (FastAPI + frontend TS/Tailwind; RF15 = documenti distinti) |
| RF16 | — | 5/6 | ✅ fatto (reset + domande di esempio + **export conversazione in Markdown**; `web/src/main.ts` → `exportConversation`) |
| RF17 | UC5 | 4/7 | ✅ fatto (pannello **"Limiti del sistema"** in UI: pulsante + lista `LIMITS`; `web/src/main.ts`) |
| RF20 | UC1, UC3 | extra | ✅ fatto (3 profili + selettore UI + `confidence`; `role_manager.py`) |
| RF21, RF22 | tutti | extra | ✅ fatto (query log SQLite + job notturno di anonimizzazione GDPR; `/api/privacy`) |
| RF23 | tutti | extra | ✅ fatto (pseudonimizzazione reversibile PII; backend regex + Presidio opzionale) |
| RF24, RF25 | UC7 | bandi | ✅ fatto (scraper Portale Appalti MIT + collection `bandi_mit` + estrazione requisiti; `bandi_scraper.py`) |
| RF26, RF27 | UC7 | bandi | ✅ fatto (chatbot bandi grounded `/api/bandi/query` + scraping SSE `/api/bandi/scrape`) |
| RF28 | UC4, UC5 | extra | ✅ fatto (audit obsolescenza/poisoning **deterministico**: filtro `status`, avviso "abrogato" no-LLM, job ibrido + quarantena, `governance_log` NIS2) |
| RNF6 | tutti | sicurezza | ✅ fatto (validazione input → 422, no leak errori; rate-limit/auth ruolo in backlog) |

## 8. Assunzioni & rischi aperti

| ID | Assunzione / rischio | Impatto | Mitigazione |
|----|----------------------|---------|-------------|
| **RISK-1** ✅ | La challenge fornisce "licenza ChatGPT"; il progetto usa **OpenRouter** (chiave personale). | — | **Verificato:** query live end-to-end OK (risposta grounded + fonti). `CHAT_MODEL` = modello di chat (un modello immagine dava testo vuoto). I modelli `:free` possono dare 429. |
| **RISK-2** ✅ | (risolto) Embedding inglese inadatto all'italiano legale. | — | **Fatto:** adottato `multilingual-e5-small` con prefissi e5 (Fase 1). |
| **RISK-3** ✅ | (risolto) `DATA_DIR=./data` vuota; corpus in `KNOWLEDGE/`. | — | **Fatto:** in Fase 0 si usa `DATA_DIR=./KNOWLEDGE` (o copia in `data/`). |
| **RISK-4** 🟡 | Dati potenzialmente sintetici/modificati per riservatezza. | Risposte "vere" ma non garantite. | RF18: citazione + invito alla verifica sui dati critici. Valutare `KNOWLEDGE/` in `.gitignore`. |
| **RISK-5** 🟡 | Dati tabellari (CSV/XLSX, migliaia di righe) mal serviti dagli embedding. | Retrieval impreciso su lookup puntuali. | Parz. mitigato in Fase 1 (chunk a row-group); **filtro payload Qdrant** in Fase 2/3; lookup pandas opzionale. |
| **RISK-6** ✅ | L'audit log delle query trattiene identificatori utente/sessione (**PII**) nel tempo. | Conformità GDPR. | **Fatto (RF22):** job notturno di **Data Anonymization** (user_id/session_id → NULL oltre 6 mesi); righe mai cancellate; retention configurabile. |
| **RISK-7** ✅ | I chunk inviati all'LLM esterno (OpenRouter) possono contenere **PII** (nomi, IBAN, importi, CIG…). | Dato personale fuori dal perimetro aziendale. | **Fatto (RF23):** **pseudonimizzazione reversibile** locale (Art. 32) — masking prima dell'invio, re-identificazione locale; Presidio opzionale per il NER. |

## 9. Criteri di successo della challenge (da tenere a vista)

Problem solving · execution · comunicazione · teamwork · pensiero critico · gestione obiezioni ·
qualità analisi · chiarezza pitch · coerenza soluzione · valore concreto. Il pitch (5 min) deve
coprire: problema → contesto/utenti → soluzione → demo → valore business → rischi/limiti → roadmap.
