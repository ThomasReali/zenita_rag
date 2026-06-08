# NextPulse — Interventi futuri (backlog)

> Cose rimaste in sospeso e idee di miglioramento. Vedi [CHANGELOG.md](./CHANGELOG.md) per
> ciò che è già stato fatto e [ARCHITETTURA.md](./ARCHITETTURA.md) per il funzionamento.
>
> **Aggiornato:** 2026-06-08

---

## 🔴 Da fare a breve (azioni dell'utente)

- **Rigenerare la API key OpenAI** ⚠️ — la chiave è stata incollata in chat ed è in chiaro nel
  `.env` (commento alla riga 2). Va considerata **compromessa**: rigenerala su platform.openai.com
  e aggiorna `OPENAI_API_KEY`.

## 🟡 Sicurezza / hardening (estensioni)

- **Auth di produzione**: oggi il login (opt-in) usa password in chiaro in `AUTH_USERS` e un
  `AUTH_SECRET` di default. Per la produzione: password **hashate**, secret robusto, eventuale
  integrazione **SSO/IdP** reale. (La base server-side c'è già — modulo `auth.py`.)
- **Rate-limiting distribuito**: l'attuale limiter è in-memory per processo; un deploy multi-nodo
  richiederebbe un backend condiviso (es. Redis).

## 🔵 Qualità retrieval / dati

- **Enrichment metadati per le altre cartelle**: l'enrichment MIT copre i 489 decreti canonici
  (`MIT Decreti PDF/`). Documenti in altre cartelle con naming diverso (Minniti, Salvini, circolari
  ANAS, linee guida ZTL) non hanno ancora il link ufficiale → estendere la mappatura o un secondo
  manifest.
- **Re-ranking di default**: valutare se attivare `RERANK_ENABLED` per la demo (più precisione, ma
  +latenza e download modello). Oggi opt-in.
- **OCR sui PDF dei bandi**: il fallback OCR è cablato sulla KB principale (`load_pdf_pages`);
  estenderlo a `process_pdf_bytes` (path scraper bandi) se servisse su gare scansionate.

## 🟠 Funzionalità

- **Streaming nella sezione Bandi e nel Configuratore**: oggi lo streaming SSE è solo sul chatbot
  principale (`/api/query/stream`); estenderlo a `/api/bandi/query` e `/api/configure`.
- **Caching anche per bandi/configuratore**: la cache risposte è per-istanza `RAGChain`; verificare
  la copertura sui chatbot secondari.

## 📦 Archiviato — bloccato da dipendenze esterne

- **Live-fetch gazzette ufficiali (D4)**: integrazione con Gazzetta Ufficiale / Normattiva per
  verificare vigenza/abrogazione in tempo reale. **Bloccato**: Normattiva non espone un'API REST
  pubblica stabile → solo scraping fragile, rischioso per una demo. Il gancio è già predisposto
  (`NORMATTIVA_AUDIT_ENABLED`, oggi OFF) per una eventuale Fase 2.

## 💡 Idee (lungo termine)

- **Capacità agentiche** oltre la bozza: configuratore che propone quantità/listino (richiede dati
  di prezzo strutturati e validazione umana).
- **Multilingua** per i mercati esteri del gruppo Zenita.
- **Cross-encoder + caching embedding** per ridurre ulteriormente latenza/costo.
- **Modello LLM a pagamento più capace** per query normative complesse (oggi `gpt-4o-mini`).
