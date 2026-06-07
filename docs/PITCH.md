# NextPulse — Pitch (5 minuti)

> **AI Sales Assistant per il Traffic Enforcement di Engine SpA (gruppo Zenita).**
> Non "un chatbot": un assistente **grounded** che risponde solo sui documenti aziendali,
> **cita sempre la fonte (file + pagina)** e **dice quando non sa**.
> Compagno operativo di [REQUISITI.md](./REQUISITI.md) · [MODELLO_DATI.md](./MODELLO_DATI.md) · [BRIEF.md](./BRIEF.md).

---

## Scaletta (i 7 punti della Scheda Challenge)

### 1. Problema
Il team Sales/Pre-Sales/Bid Manager perde tempo a recuperare informazioni tecniche e normative
**sparse** su decreti MIT, Codice della Strada, FAQ, schede ed elenchi. Sotto pressione sui tempi,
il rischio è una **risposta lenta, incompleta o sbagliata** al cliente/in gara — che costa credibilità
e opportunità. Una risposta normativa errata su un'omologazione può invalidare una sanzione: il **costo
dell'errore è alto**.

### 2. Contesto e utenti
- **Engine SpA** (Zenita): autovelox, ZTL, semaforo rosso, analytics mobilità.
- **Utenti:** Sales (risposte rapide e sicure), Pre-Sales (dettaglio tecnico/config), **Bid Manager**
  (requisiti di gara, riferimenti ai decreti), Customer Success.
- **Dato reale fornito:** `KNOWLEDGE/` — **539 file, 108 MB**, prevalentemente PDF normativi + tabelle MIT.

### 3. Soluzione
RAG **locale-first** con governance al centro:
- **Ingestion multi-formato** (PDF, DOCX, CSV, XLSX, JSON) robusta: 515 file indicizzati, 24 saltati
  *con motivo esplicito* (19 scansioni a testo nullo, 4 manifest, 1 link) — **0 crash** su dati "sporchi".
- **Chunking strutturale** a confini di articolo/paragrafo, dimensionato ai **token** del modello →
  **4.607 chunk, 0 troncati** in embedding; ogni chunk porta `page`/`decreto`/`category`.
- **Retrieval hybrid**: denso `multilingual-e5-small` (semantica) + **BM25** (termini esatti) fusi con
  **RRF**, su **Qdrant** (embedded, locale) con payload filtering.
- **Governance**: **gate deterministico** (sotto soglia di similarità → "non in documentazione",
  *senza generare*) + **citazioni `[Fonte: file, pag.]`** + **gate di ambiguità** (giudice LLM → discrezione).
- **Role-awareness**: 3 profili (**Sales / Pre-Sales / Bid Manager**) con tono, terminologia e formato fonti
  dedicati + **confidence** 🟢🟡🔴 per ogni risposta — selezionabili dalla dashboard.
- **Privacy by design (GDPR)**: ogni query è loggata per l'audit con identificatori **opachi**; un **job
  notturno** anonimizza i log oltre **6 mesi** (`user_id`/`session_id` → NULL) — il dato resta statistico,
  **esce dal perimetro GDPR**, **nessuna cancellazione**.
- **Pseudonimizzazione reversibile (Art. 32)**: un layer **locale** maschera la PII (nomi, IBAN, CIG, importi,
  margini, Comuni) in token `[PERSON_1]`… **prima** di OpenRouter e la **re-identifica in locale** → l'LLM lavora
  *zero-knowledge*, i dati reali **non escono mai** dall'azienda (Presidio-ready).
- **LLM** via OpenRouter (pesi congelati); **embedding locali** → costo indicizzazione ≈ 0, dati in casa.
- **UI**: FastAPI + frontend Vite/TS/Tailwind (chat, fonti, badge grounded, barra di confidenza, selettore profilo).

### 4. Demo (script live — ~90s)
Avvio: `uvicorn src.nextpulse.api:app --port 8000` + `cd web && npm run dev` → `localhost:5173`.

| # | Query | Cosa mostra |
|---|-------|-------------|
| 1 | *"Quali requisiti per l'omologazione degli autovelox?"* | Risposta **grounded** in elenco puntato + **fonti con pagina** (Quadro normativo / decreto MIT) |
| 2 | *"VELOMATIC"* (o *"Autovelox 106"*) | **Hybrid/BM25**: fa emergere il modello esatto dall'`Elenco autovelox MIT.csv` |
| 3 | *"e per la gestione della ZTL?"* (follow-up) | **Memoria conversazionale**: riformula sfruttando il turno precedente |
| 4 | *"Qual è la ricetta della carbonara?"* | **Gate onesto**: badge "nessuna fonte", *"contatta il Bid Manager"* — **niente invenzione** |
| 5 *(se c'è tempo)* | Cambio profilo **Sales → Bid Manager** sulla stessa domanda | **Role-awareness**: stessa fonte, tono/terminologia diversi + badge **confidence** |

> Da evidenziare: ogni risposta è **tracciabile** (file + pagina) e il sistema **rifiuta** di rispondere
> fuori dominio. Questo è il punto: *fiducia*. Chiudere con **Data Governance**: audit log + anonimizzazione
> notturna (GDPR) → *"i dati di utilizzo restano per le statistiche, ma le identità escono dal perimetro GDPR"*.

### 5. Valore business
| KPI | Baseline → con NextPulse |
|-----|--------------------------|
| Tempo di recupero di un'informazione | minuti di ricerca manuale → **secondi** |
| Risposte con fonte verificabile | spesso assente → **≈100%** (citazione file+pagina) |
| Allucinazioni / risposte inventate | rischio reale → **0** in demo (gate deterministico) |
| Escalation al Bid Manager per info di base | da ridurre → **−% richieste ripetitive** |
| Qualità/velocità in gara | win-rate e cicli di vendita migliorati |

**Impatto:** meno tempo perso, meno errori costosi, più capacità del team a parità di organico;
una **knowledge base unica e verificabile** su fonti oggi distribuite.

### 6. Rischi e limiti (li dichiariamo — è governance)
- **Dati sintetici/modificati** per riservatezza → le fonti sono *verificabili ma non garantite*; su
  dati critici (prezzi, gara) il sistema invita alla verifica.
- **19 scansioni** (PDF immagine) non ancora indicizzate: serve l'**OCR** (tesseract presente, manca il
  language pack `ita`) — hook pronto.
- **Soglia del gate** (0.82) tarata su e5/questo corpus → da ri-calibrare se cambia modello/dominio.
- **Modello LLM free** soggetto a rate-limit (429) → per la produzione, modello a pagamento economico.
- Citazione per **articolo/comma** best-effort (non sempre estratta); `decreto` dal nome file (~34%).

### 7. Roadmap evolutiva
- **OCR** delle scansioni (con `tesseract-data-ita`) → +19 documenti.
- **Enrichment via manifest MIT**: join `decreto → titolo/data/URL` per citazioni complete.
- **Streaming** risposte + **caching**; **re-ranking** cross-encoder per la precisione.
- **Configuratore** di soluzioni (ZTL+velocità per Comune medio) e bozza-offerta assistita.
- **Autenticazione/SSO** sopra ai 3 profili già presenti (oggi selezionati, non verificati); dashboard
  privacy che legge `/api/privacy`; deploy su **Qdrant server**/cloud quando serve la scala.

---

## Gestione obiezioni (dalla discovery del workshop)
- *"Perché dovrei fidarmi dell'output?"* → Risponde **solo** dai documenti, **cita file e pagina**, e
  **rifiuta** quando non ha fonti (gate deterministico, non solo prompt). Tracciabile e verificabile.
- *"E se i dati sono sbagliati/sintetici?"* → Mostriamo la fonte: l'umano verifica in 1 click; sui dati
  critici l'assistente lo dice esplicitamente.
- *"È l'ennesimo chatbot?"* → No: hybrid retrieval su corpus reale + governance + UI con confidenza/fonti.
- *"E la privacy / il GDPR sui log di utilizzo?"* → **Privacy by design**: identificatori opachi, audit log e
  **anonimizzazione notturna** (user_id/session_id → NULL oltre 6 mesi). Le metriche restano, le identità escono dal perimetro GDPR.
- *"E i dati che mandate all'LLM esterno?"* → **Pseudonimizzazione reversibile** (Art. 32): la PII è mascherata in
  locale **prima** dell'invio e re-identificata al ritorno. OpenRouter vede solo token: i dati reali non lasciano l'azienda.

## Numeri che dimostrano l'execution
539 file analizzati · 515 indicizzati (0 crash) · **4.607 chunk, 0 troncati** · hybrid dense+BM25 su
Qdrant · doppia governance (gate anti-allucinazione + ambiguità) · **3 profili ruolo + confidence** ·
**audit log + anonimizzazione GDPR** · **pseudonimizzazione reversibile PII (Art. 32)** · **69 test verdi** ·
UI FastAPI + TS/Tailwind · tutto **locale** (embedding) + LLM via OpenRouter.

## Perché possiamo vincere (Obiettivo finale della Scheda)
Comprensione del problema (governance > demo appariscente) · **prioritizzazione** (prima fondamenta:
ingestion robusta, chunking corretto, anti-allucinazione) · **visione di prodotto** (assistente
decisionale, non chatbot) · **equilibrio innovazione/fattibilità** (hybrid e governance reali, ma stack
leggero e locale che gira su un portatile).
