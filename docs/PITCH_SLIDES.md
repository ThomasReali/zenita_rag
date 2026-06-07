# NextPulse — Pitch Deck (slide-by-slide)
### Board Zenita Group · Team Engine — durata totale ≈ 3'00"

> 8 slide. Il **testo a schermo è minimale** (la board legge in fretta) — il contenuto si dice a voce.
> Le **note relatore** ricostruiscono lo script approvato (≈400 parole). Palette navy, font come l'UI
> (Fraunces per i titoli). *Le metriche sono stime/mock da validare.*

---

## Slide 1 — Copertina  ·  ~20"

**Sullo schermo**
- **NextPulse**
- *L'assistente che rende ogni risposta commerciale conforme al Codice della Strada.*
- Sales Assistant per il **Traffic Enforcement** · Engine SpA — Zenita Group

**Visual:** sfondo navy, wordmark in Fraunces + glyph "pulse" azure; in basso il sottotitolo. Pulito, autorevole.

**Note relatore**
> Buongiorno. Vi presento **NextPulse**: l'assistente che rende **ogni risposta commerciale conforme al
> Codice della Strada**. Per i sistemi di Traffic Enforcement di Engine, trasformiamo migliaia di decreti
> MIT in risposte **sicure, tracciabili e a prova di gara**.

---

## Slide 2 — Il problema  ·  ~35"

**Sullo schermo**
- *Normative che cambiano, interpretazione complessa*
- Decreti che si sovrappongono — alcuni **mai abrogati**
- Oggi: ricerca **manuale** tra centinaia di PDF
- Rischio: risposte **non aggiornate / parziali / non conformi** → **gara a rischio**

**Visual:** "before" — pila disordinata di PDF/decreti con un triangolo di warning rosso.

**Note relatore**
> Le normative sul Traffic Enforcement — autovelox, ZTL, omologazioni — cambiano di continuo, e
> l'interpretazione è complessa: decreti che si sovrappongono, alcuni mai abrogati. Oggi Sales, Pre-Sales
> e Bid Manager cercano a mano tra centinaia di PDF. Il risultato è un rischio concreto di risposte **non
> aggiornate, parziali o non conformi** — e in gara un errore normativo può **invalidare una fornitura**.
> Ogni risposta imprecisa erode credibilità, margine e tempo del team.

---

## Slide 3 — La soluzione: valore per Engine  ·  ~25"

**Sullo schermo**
- *Domanda in linguaggio naturale → risposta fondata e citata, in secondi*
- Per **Pre-Sales · Sales · Bid Manager**
- Risposta **solo** dai documenti aziendali, con **citazione esatta**: file · pagina · decreto
- Offerte **100% compliant**, in modo dimostrabile

**Visual:** screenshot reale dell'UI — bolla risposta con badge **"Grounded"** verde e **"Fonti citate (pag. …)"** aperto.

**Note relatore**
> NextPulse è un RAG **iper-verticale** sul Traffic Enforcement. Il team fa una domanda in linguaggio
> naturale e ottiene **in secondi** una risposta fondata **solo** sui documenti aziendali, con la
> **citazione esatta**: file, pagina, numero di decreto. Il motore combina **ricerca semantica e match
> esatto** sui riferimenti normativi, così nessun decreto rilevante sfugge.

---

## Slide 4 — Tech & Data Governance (il cuore)  ·  ~45"

**Sullo schermo**
- Pipeline: **Ingestion multi-formato → chunking strutturale → retrieval ibrido (Qdrant) → 2 Gate → risposta citata**
- 🛡️ **Gate 1 — anti-allucinazione (deterministico):** niente fonte ⇒ niente risposta
- 🛡️ **Gate 2 — giudice del conflitto:** decreti divergenti ⇒ discrezione, rimando all'esperto
- 🔄 Aggiornamento **incrementale** · 🔒 dati **in locale**

**Visual:** diagramma a blocchi della pipeline con i due "scudi" evidenziati; in calce "tracciabilità: ogni chunk → file/pagina/decreto".

**Note relatore**
> Il cuore è la **Data Governance**, costruita nel codice. Ogni informazione porta la sua fonte, e **due
> cancelli** proteggono la risposta. Il primo è **deterministico**: se la documentazione non copre la
> domanda, il sistema **non genera nulla** e rimanda al Bid Manager — zero allucinazioni. Il secondo è un
> **giudice normativo**: se due decreti sono in conflitto, l'AI **non sceglie e non fonde**, ma li espone
> entrambi e demanda all'esperto. L'indicizzazione è incrementale: quando una norma cambia, i dati vecchi
> vengono sostituiti — mai risposte datate. E poiché embedding e archivio restano **in locale**, i
> documenti riservati non lasciano l'azienda.

---

## Slide 5 — Impatto  ·  ~15"

**Sullo schermo** *(stime da validare)*

| KPI | Oggi | Con NextPulse |
|-----|------|---------------|
| Inaccuratezza / non conformità | ~30% | **< 5%** |
| Risposte con fonte verificabile | ~assente | **100%** |
| Allucinazioni fuori dominio | possibili | **0** |
| Validazione normativa di un'offerta | ~40 min | **< 3 min** |

**Visual:** due numeri grandi in evidenza: **40 min → < 3 min** e **30% → < 5%**.

**Note relatore**
> I numeri. Nei nostri test: inaccuratezza **dal ~30% a sotto il 5%**, **100%** di risposte con fonte
> verificabile, e la validazione normativa di un'offerta **da circa 40 minuti a meno di 3**.

---

## Slide 6 — Competitor & differenziazione  ·  ~30"

**Sullo schermo**

| Approccio | Limite |
|-----------|--------|
| Ricerca manuale | lenta, fallibile |
| Knowledge base statiche | sempre obsolete |
| ChatGPT generalista | inventa norme, **senza citare** |
| **NextPulse** | **iper-verticale + governance inattaccabile** |

- *Non un chatbot: un **sistema di compliance**.*

**Visual:** tabella di confronto con ✗ rossi sulle alternative e ✓ navy su NextPulse.

**Note relatore**
> Oggi le alternative sono tre: la **ricerca manuale**, lenta e fallibile; le **knowledge base statiche**,
> sempre obsolete; o un **ChatGPT generalista**, che inventa norme senza citarle. Il nostro differenziale
> è duplice: **iper-verticalità** sul dominio Engine e un'infrastruttura di **governance inattaccabile** —
> citazioni obbligatorie, gate anti-allucinazione, gestione del conflitto tra fonti. **Non un chatbot: un
> sistema di compliance.**

---

## Slide 7 — Roadmap  ·  ~20"

**Sullo schermo**
- **As-Is (MVP, funzionante):** ingestion multi-formato · retrieval ibrido (Qdrant) · doppia governance · UI enterprise · 40 test
- **To-Be:** **live-fetch** dalle gazzette ufficiali · **capacità agentiche** (config offerta) · **multilingua** (mercati esteri del gruppo)

**Visual:** timeline a due colonne As-Is → To-Be (freccia navy).

**Note relatore**
> Oggi abbiamo un **MVP funzionante**: ingestion multi-formato, retrieval ibrido su Qdrant, doppia
> governance e interfaccia enterprise. Il prossimo passo: **aggiornamento live dalle gazzette ufficiali**,
> **capacità agentiche** per configurare l'offerta, e **multilingua** per i mercati esteri del gruppo.

---

## Slide 8 — Closing  ·  ~10"

**Sullo schermo**
- **NextPulse** — il know-how normativo di Engine come **vantaggio competitivo difendibile**.
- *Prossimo passo proposto: pilota su un dominio (Velocità o ZTL).*
- Grazie.

**Visual:** wordmark navy centrato, un richiamo all'UI sullo sfondo.

**Note relatore**
> NextPulse trasforma il know-how normativo di Engine in un **vantaggio competitivo difendibile**. Grazie.

---

### Cheat-sheet timing
| Slide | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|-------|---|---|---|---|---|---|---|---|
| Sec.  | 20 | 35 | 25 | 45 | 15 | 30 | 20 | 10 |

**Totale ≈ 3'00".** Slide-chiave da non saltare: **4 (governance)** e **5 (numeri)**. Tieni l'UI aperta
su `localhost:8000` per un'eventuale **demo live di 20"**: una query in-dominio (badge *Grounded* + fonti)
e una off-dominio (gate onesto).
