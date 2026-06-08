# NextPulse — Cronologia interventi

> Registro datato di interventi e miglioramenti. Date dai commit Git (fuso locale).
> Vedi anche [ARCHITETTURA.md](./ARCHITETTURA.md) (funzionamento) e
> [INTERVENTI_FUTURI.md](./INTERVENTI_FUTURI.md) (backlog).

---

## 2026-06-08 — Iterazione miglioramenti (performance, copertura KB, sicurezza, UX)

Sessione ampia: prima autonoma su un piano d'azione condiviso, poi interattiva con conferma
dell'utente sui punti aperti.

### Performance & UX
- **18:01 — Gate di intento**: classificatore LLM che distingue le richieste di dominio dai
  messaggi colloquiali/fuori tema. Off-topic → risposta semplice in **box pulita** (niente fonti,
  estratti o header grounded). Campo `off_topic`. Fail-safe a dominio su errore. `INTENT_GATE`.
- **15:04 — Fix color coding**: la barra di confidenza usava il `top_score` grezzo → una risposta
  **ambigua** (similarità alta) sembrava sicura. Ora barra + lozenge seguono la governance
  (🟢/🟡/🔴): ambiguo = **rosso** con barra bassa. Aggiunto `--color-signal-red`.
- **13:15/13:18 — Streaming risposte (SSE)**: nuovo `POST /api/query/stream` (meta → token → done);
  UI live; refactor `_decide` come fonte di verità unica per query bloccante e streaming.
- **13:00 — Cache risposte** (TTL+LRU per-istanza): domande identiche saltano retrieval+LLM
  (latenza ~0, costo azzerato, guard sui 429). Campo `cached`.

### Qualità retrieval
- **13:02 — Re-ranking cross-encoder** (opt-in `RERANK_ENABLED`): over-fetch + riordino dei
  candidati per fonti citate più precise. Default OFF (scarica un modello).
- **15:46 — Enrichment metadati MIT (D3)**: `scripts/enrich_metadata.py` aggancia il manifest
  ufficiale a **489 decreti** (titolo + URL `mit.gov.it`) via `set_payload` (no re-embedding).
  Le **citazioni diventano cliccabili** verso la fonte ufficiale (`source_links`).

### Copertura knowledge base
- **13:27 — OCR scansioni (D1)**: fallback OCR (Tesseract/`ita` + PyMuPDF) per i PDF immagine.
  Re-index → **19 decreti scansionati recuperati, +243 chunk** (KB 511 → 528 documenti,
  Qdrant 4569 → 4810 chunk). Opt-in `OCR_ENABLED`.

### Nuove funzionalità
- **14:12/14:18 — Configuratore d'offerta (D2)**: `OfferConfigurator` + `POST /api/configure` +
  **sezione UI "Configura Offerta"**. Bozza grounded, citata, **non vincolante**; fallback onesto
  se la KB è insufficiente.
- **13:06 — Mini eval-harness**: `scripts/eval_rag.py` misura grounding/citation/fallback/latenza.

### Sicurezza
- **15:57 — Login + ruolo verificato server-side (C2)**: `auth.py` (token HMAC stateless),
  `/api/login|logout|me`; con auth attiva il ruolo arriva dalla sessione, non dal client (RNF6).
  Opt-in `AUTH_ENABLED`, OFF di default.
- **13:05 — Rate-limiting per IP**: sliding-window in-memory sugli endpoint a costo LLM → HTTP 429.

### Tracking
- **12:57 — Allineamento**: RF16 (export Markdown) e RF17 (pannello Limiti) erano già implementati
  in UI ma segnati "da fare"; matrice aggiornata. Creato il piano d'azione della sessione.

---

## 2026-06-07 — Citazioni, ruoli, fix qualità risposte

- **22:50 — Riordino priorità ruoli**: ordine Pre-Sales → Sales → Bid Manager (fonte di verità il
  registry `ROLES`, propagato a `/api/roles`, UI e docs).
- **22:36 — Fix anti-troncamento**: `max_response_length` veniva passato come `max_tokens` con cap
  troppo basso → risposte tagliate a metà frase. Reso un tetto di sicurezza generoso; la brevità
  resta guidata dai system prompt.
- **15:11 — Citazioni inline numerate `[n]`** + legenda "Fonti citate", grassetto UI, dedup fonti;
  fix API 429 (quota LLM) esposto come HTTP 429.
- **13:57 — Fix RF19**: il gate di ambiguità non scatta più su query cross-dominio / fonti parallele
  non in conflitto (dominance guard + focus guard).
- **13:27 — Governance obsolescenza deterministica (RF28)** + tono adattivo + refactor bandi MIT.
- **11:43/11:52 — Modulo Bandi/Gare d'Appalto (Portale Appalti MIT)**: scraping live (Friendly-Captcha
  PoW) + chatbot RAG dedicato; hardening sicurezza API (validazione input, no info disclosure).
- **09:28 — Commit iniziale**: Sentinel RAG Sales Assistant (pipeline RAG, Qdrant hybrid, role-aware,
  privacy GDPR, UI FastAPI + Vite/TS/Tailwind).

---

> **Conteggio test:** 69 → 111 → 139 → 146 → 162 → **167** (crescita con ogni feature, LLM mockato).
> **Knowledge base:** 511 → **528 documenti** distinti · **4810 chunk** (Qdrant embedded).
