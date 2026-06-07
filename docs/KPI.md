# NextPulse — KPI di sistema

Sei metriche operative per monitorare qualità, copertura e utilizzo del sistema RAG.
Tutte interrogabili via SQLite (`query_log.db`) o dall'endpoint `/api/kpi` (da implementare).

---

## 1. Grounding Rate

**"Il sistema risponde davvero dai documenti?"**

Percentuale di domande che hanno ricevuto una risposta reale (non un rifiuto). È il KPI principale:
un valore alto indica che il sistema è utile e la knowledge base copre le domande degli utenti.
Un calo segnala documenti mancanti o un aumento di query fuori dominio.

```sql
SELECT ROUND(100.0 * SUM(grounded) / COUNT(*), 1) AS grounding_rate_pct FROM query_log;
```

**Target:** > 80 %

---

## 2. Fallback Rate

**"Quante volte il sistema ha detto 'non lo so'?"**

Percentuale di query in cui il gate anti-allucinazione ha rifiutato la generazione perché il
top-score coseno era sotto soglia (`SCORE_THRESHOLD = 0.82`). Un valore basso è normale;
un valore alto indica che la knowledge base manca di documenti su argomenti frequenti.

```sql
SELECT ROUND(100.0 * SUM(CASE WHEN grounded = 0 THEN 1 ELSE 0 END) / COUNT(*), 1)
    AS fallback_rate_pct FROM query_log;
```

**Target:** < 20 %

---

## 3. Average Top Score

**"Quanto è sicuro il sistema quando trova qualcosa?"**

Media del punteggio coseno del chunk più rilevante sulle query che hanno ricevuto risposta.
Un valore vicino alla soglia minima (0.82) indica retrieval "per un pelo"; sopra 0.87 indica
retrieval preciso e fonti pertinenti.

```sql
SELECT ROUND(AVG(top_score), 3) AS avg_top_score
FROM query_log WHERE grounded = 1;
```

**Target:** > 0.87

---

## 4. Confidence Distribution

**"Con quanta certezza risponde il sistema?"**

Distribuzione percentuale dei livelli di confidenza: `green` (fonte singola, risposta certa),
`yellow` (più fonti, risposta composita), `red` (rifiuto o ambiguità rilevata).
Un alto rosso indica KB incompleta o domande troppo complesse.

```sql
SELECT confidence, COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM query_log GROUP BY confidence ORDER BY confidence;
```

**Target:** verde + giallo > 80 %

---

## 5. Ambiguity Rate

**"Quante volte fonti diverse si contraddicono?"**

Percentuale di query in cui il gate di ambiguità (giudice LLM) ha rilevato un conflitto tra
decreti. Un'impennata improvvisa segnala che sono stati aggiunti decreti sovrapposti o non
abrogati che richiedono revisione umana.

```sql
SELECT ROUND(100.0 * SUM(ambiguous) / COUNT(*), 1) AS ambiguity_rate_pct FROM query_log;
```

**Target:** < 10 %

---

## 6. Query Distribution per Ruolo

**"Chi usa il sistema e quanto?"**

Numero di query per profilo (Pre-Sales, Sales, Bid Manager) nel periodo. Se un profilo non usa
il sistema significa che non lo trova rilevante per le sue domande o che la KB non copre il
suo dominio — guida le decisioni su cosa aggiungere alla knowledge base.

```sql
SELECT role, COUNT(*) AS n_queries,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM query_log GROUP BY role ORDER BY n_queries DESC;
```

---

## Note implementative

- `latency_ms` è già nel log dalla Fase 6 — usabile per metriche di performance.
- `pii_masked` non è ancora nel log SQLite: aggiungere la colonna e popolarla in `rag_chain.py`
  se si vuole tracciare la frequenza di mascheramento PII.
- Per un endpoint `/api/kpi` esporre queste quattro query aggregate + serie temporale per trend.
