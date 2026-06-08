# NextPulse — Business Proposal

### Zenita Group · Board / Team Engine — focus **Data Governance**

> Caso di business sintetico. Per il **funzionamento tecnico e lo stack** vedi
> [ARCHITETTURA.md](./ARCHITETTURA.md); per il **deck e lo script del pitch** vedi
> [PITCH_SLIDES.md](./PITCH_SLIDES.md).
> *Le metriche sono stime/mock realistiche da validare con dati reali Engine.*

---

## 1. Cosa proponiamo

Un **AI Sales Assistant grounded (RAG) iper-verticale** sul Traffic Enforcement per **Pre-Sales /
Sales / Bid Manager** di Engine: risposte commerciali **rapide, fondate e tracciabili**,
dimostrabilmente **conformi** alla normativa di sicurezza stradale.

Non un chatbot, ma un **sistema di compliance**: ogni risposta cita la fonte (file · pagina ·
**link ufficiale `mit.gov.it`**), e una **tripla governance** protegge l'output —
anti-allucinazione deterministica, gestione del conflitto tra decreti (discrezione) e gate di
intento (le richieste fuori dominio non vengono "vestite" da risposta normativa).

## 2. Valore per Engine

- **Meno tempo perso**: dalla ricerca manuale tra centinaia di PDF a una risposta in **secondi**.
- **Meno errori costosi**: una risposta normativa errata su un'omologazione può **invalidare una
  sanzione/fornitura** in gara — qui ogni claim è risalibile e verificabile.
- **Meno escalation**: il team evade autonomamente le richieste di base; il Bid Manager interviene
  solo dove serve (conflitti, dati critici).
- **Moat difendibile**: il know-how normativo di Engine diventa un asset interrogabile e governato.
- **Costo/footprint contenuti**: embedding e vector DB **locali** (costo ≈ 0, data residency); solo
  la generazione esce a token verso l'LLM, e **con PII pseudonimizzata** (zero-knowledge).

## 3. Metriche di impatto (stime da validare)

| KPI | Pre-RAG (as-is) | Post-NextPulse |
|-----|-----------------|----------------|
| Inaccuratezza / risposte non conformi | ~30% | **< 5%** |
| Risposte con fonte verificabile (file + pagina + link) | ~assente | **100%** |
| Allucinazioni su query fuori dominio | possibili | **0** (gate deterministico) |
| Tempo di validazione normativa di un'offerta | ~40 min | **< 3 min** (−90%) |

> KPI operativi monitorabili in continuo (grounding rate, fallback, confidence, ambiguità, uso per
> ruolo): vedi [KPI.md](./KPI.md).

## 4. Stato e roadmap

- **Oggi (funzionante):** ingestion multi-formato + OCR · retrieval ibrido su Qdrant · tripla
  governance · streaming · configuratore d'offerta · citazioni con link ufficiale MIT ·
  login/ruolo server-side · privacy by design (GDPR) · **167 test**. Dettaglio in
  [CHANGELOG.md](./CHANGELOG.md).
- **Prossimi passi:** live-fetch dalle gazzette ufficiali · capacità agentiche evolute · multilingua
  per i mercati esteri del gruppo. Backlog in [INTERVENTI_FUTURI.md](./INTERVENTI_FUTURI.md).

**Prossimo passo proposto:** pilota su un dominio (Velocità o ZTL) con dati reali Engine per
validare i KPI sopra.
