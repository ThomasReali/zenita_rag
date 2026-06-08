"""RAG chain — retrieve, reformulate with conversational memory, generate"""
import hashlib
import json
import logging
import re
import time
from typing import List, Optional, Tuple
from openai import OpenAI
from src.nextpulse.cache import TTLCache
from src.nextpulse.vector_store import VectorStore
from src.nextpulse.pseudonymizer import Pseudonymizer
from src.nextpulse import config

logger = logging.getLogger("nextpulse.rag")

# ── Italian prompts for the Engine SpA hackathon ─────────────────────────────

CONDENSE_QUESTION_PROMPT = """\
Data la seguente cronologia di conversazione e la successiva domanda dell'utente, \
riformula la domanda in modo che sia comprensibile senza la cronologia. \
MANTIENI intatte tutte le parole chiave tecniche.
Non rispondere alla domanda, limitati a riformularla.

Cronologia:
{chat_context}

Domanda dell'utente: {query}
Domanda Riformulata:"""

SYSTEM_PROMPT = """\
Sei un Assistente Pre-Sales Senior per Engine SpA, azienda del gruppo Zenita \
leader nel Traffic Enforcement (Autovelox, ZTL, Semafori intelligenti).
Il tuo obiettivo è supportare il team commerciale a rispondere rapidamente e \
senza errori ai clienti.

REGOLE FONDAMENTALI (Pena fallimento della gara):
1. GROUNDING: Rispondi ESCLUSIVAMENTE utilizzando le informazioni presenti nei \
"DOCUMENTI AZIENDALI" forniti qui sotto.
2. NO HALLUCINATION: Se la risposta non è presente o i documenti sono vuoti, \
DEVI dire: "Questa informazione non è presente nella documentazione tecnica \
attuale. Ti suggerisco di contattare il Bid Manager." Non inventare prezzi, \
normative o sigle.
3. TONE OF VOICE: Professionale, sintetico, strutturato (usa elenchi puntati). ADATTA il \
registro alla domanda: a una domanda GENERICA o introduttiva rispondi in modo chiaro e \
discorsivo (inquadramento generale), senza appesantire con decreti, articoli o sigle se \
non servono.
4. CITAZIONI: ogni documento qui sotto INIZIA con il suo marcatore di citazione tra parentesi \
quadre (es. [1], [2]). Quando usi un'informazione di un documento, riporta inline ESATTAMENTE \
quel marcatore subito dopo la frase — es. "...va installata la segnaletica [2]." Scrivi SOLO \
il marcatore: NON scrivere la parola "Fonte", NON scrivere "[Fonte 1]", NON scrivere il nome \
del file, e NON aggiungere un elenco di fonti alla fine (la legenda numerata viene allegata \
automaticamente dal sistema). Su domande generiche, se l'utente non chiede riferimenti \
puntuali, puoi rispondere senza citazioni. Resta vincolato alla regola 1 (solo dai documenti).

DOCUMENTI AZIENDALI:
{context_str}

Domanda del Venditore: {standalone_query}
Risposta:"""

# Deterministic fallback when nothing relevant is retrieved (governance gate, RF10).
NO_CONTEXT_MESSAGE = (
    "Questa informazione non è presente nella documentazione tecnica attuale. "
    "Ti suggerisco di contattare il Bid Manager."
)

# Discretion response when the retrieved sources are judged in conflict (ambiguity gate):
# cite the sources, defer to a human — never resolve conflicting decrees automatically.
AMBIGUITY_MESSAGE = (
    "Su questo punto risultano più provvedimenti potenzialmente rilevanti, con possibili "
    "differenze. Per evitare interpretazioni errate non fornisco una sintesi definitiva: "
    "consulta le fonti qui sotto e verifica con il Bid Manager quale si applichi."
)

# Deterministic abrogation notice (no LLM): the most relevant provvedimento exists but the
# audit flagged it OBSOLETE. Instead of "non lo so", surface that it was superseded, with the
# replacing decree taken straight from metadata — exactly the behaviour the PA sale needs.
OBSOLETE_MESSAGE = (
    "Il provvedimento più pertinente alla tua domanda risulta ABROGATO o superato e "
    "NON è più in vigore: per questo non lo utilizzo per rispondere. Di seguito i "
    "riferimenti e l'eventuale provvedimento sostitutivo — verifica con il Bid Manager "
    "quale norma si applichi oggi."
)

# LLM judge: decides whether the retrieved passages conflict (one-word answer).
CONFLICT_JUDGE_PROMPT = """\
Sei un revisore normativo rigoroso. Di seguito alcuni estratti recuperati per una domanda. \
Stabilisci se contengono una CONTRADDIZIONE DIRETTA tra fonti diverse sullo stesso punto specifico \
(es. due decreti che fissano valori, obblighi o scadenze DIVERSI per la stessa identica fattispecie).

NON è un conflitto (rispondi OK in tutti questi casi):
- informazioni complementari, dettagli aggiuntivi, ripetizioni dello stesso contenuto, o estratti \
che trattano aspetti diversi dello stesso tema;
- più provvedimenti che approvano o disciplinano APPARECCHI, SISTEMI o FATTISPECIE DIVERSI \
(es. l'omologazione di più modelli di autovelox diversi, oppure sistemi di domini diversi come \
ZTL/varchi accessi, autovelox/velocità, semaforo rosso): COESISTONO e non si contraddicono;
- fonti che rispondono alla domanda da prospettive o ambiti differenti.

C'è conflitto SOLO se due fonti diverse stabiliscono regole INCOMPATIBILI per lo STESSO identico \
oggetto/situazione (non basta che parlino dello stesso tema generale).

Rispondi con UNA sola parola: CONFLITTO solo se c'è una contraddizione diretta come sopra, \
altrimenti OK.

DOMANDA: {query}

ESTRATTI:
{context_str}

Risposta:"""


class RAGChain:
    """Retrieval-Augmented Generation chain with conversational memory"""

    def __init__(self, vector_store: Optional[VectorStore] = None,
                 system_prompt_template: Optional[str] = None,
                 no_context_message: Optional[str] = None):
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENROUTER_API_KEY / OPENAI_API_KEY not set — "
                "create a .env file or set the env var"
            )

        # A caller can inject a vector store bound to a different collection (e.g. the
        # bandi/gare RAM corpus) and a domain-specific prompt, reusing the whole pipeline.
        self.vector_store = vector_store or VectorStore()
        self.system_prompt_template = system_prompt_template or SYSTEM_PROMPT
        self.no_context_message = no_context_message or NO_CONTEXT_MESSAGE
        self.client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            default_headers={"X-Title": "NextPulse Sales Assistant"},
            max_retries=config.LLM_MAX_RETRIES,  # exponential backoff on 429/5xx
        )
        self.model = config.CHAT_MODEL
        # Reversible pseudonymization layer (GDPR Art. 32): masks PII before any
        # text reaches OpenRouter, re-identifies it locally on the response.
        self.pseudonymizer = Pseudonymizer()
        # Per-instance response cache: identical questions skip the LLM (demo latency/cost).
        # Bound to THIS chain, so the bandi corpus never returns a company-KB answer.
        self._cache = (
            TTLCache(config.RESPONSE_CACHE_SIZE, config.RESPONSE_CACHE_TTL_SECONDS)
            if config.RESPONSE_CACHE_ENABLED else None
        )

    # ── LLM call (with optional PII masking) ──────────────────────────────────

    def _complete(self, messages: List[dict], *, session=None, **kwargs) -> str:
        """Call the LLM. If a masking `session` is given, every message content is
        pseudonymized before sending and the reply is re-identified on return —
        the provider only ever sees tokens, never real PII (zero-knowledge)."""
        if session is not None:
            messages = [{**m, "content": session.mask(m["content"])} for m in messages]
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, **kwargs
        )
        raw = resp.choices[0].message.content or ""
        return session.unmask(raw) if session is not None else raw

    # ── retrieval ────────────────────────────────────────────────────────────

    def retrieve(
        self, query: str, k: Optional[int] = None, *, apply_status_filter: bool = True
    ) -> Tuple[List[str], List[dict], List[float], float]:
        """Retrieve relevant chunks; returns (texts, metadatas, rrf_scores, max_cosine).

        By default the deterministic status filter hides chunks the obsolescence/poisoning
        audit flagged (config.EXCLUDED_STATUSES). Pass apply_status_filter=False for the
        second pass that builds the abrogation notice (it needs to *see* the obsolete chunk)."""
        k = k or config.RETRIEVAL_K
        exclude = (
            config.EXCLUDED_STATUSES
            if (apply_status_filter and config.STATUS_FILTER_ENABLED)
            else ()
        )
        if config.RERANK_ENABLED:
            # Over-fetch from the recall-oriented hybrid retrieval, then let the cross-encoder
            # pick the precise top-k. The dense-cosine gate signal (4th return value) is the max
            # over the larger candidate set — still a valid, if not stricter, gate input.
            from src.nextpulse import reranker
            fetch = max(config.RERANK_CANDIDATES, k)
            docs, metas, scores, top_cos = self.vector_store.search(
                query, k=fetch, exclude_status=exclude
            )
            docs, metas, scores = reranker.rerank(query, docs, metas, scores, top_k=k)
            return docs, metas, scores, top_cos
        return self.vector_store.search(query, k=k, exclude_status=exclude)

    # ── citation helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _normalize_citations(text: str) -> str:
        """Force inline citations to the bare numeric form [N]. gpt-style models tend to wrap
        the marker with 'Fonte:'/page noise (es. '(Fonte: [1], p. 2)' or '[Fonte 1]'); we strip
        that deterministically so the answer carries only [1]/[2], matching the legend."""
        # (Fonte: [1], p. 2) / (Fonte [1][2]) → the bare markers it contains
        text = re.sub(
            r"\(\s*font[ei]?[:\s]*((?:\[\d+\][\s,;]*)+)[^)]*\)",
            lambda m: " ".join(re.findall(r"\[\d+\]", m.group(1))),
            text, flags=re.I,
        )
        # [Fonte: 1] / [Fonte 1] → [1]
        text = re.sub(r"\[\s*font[ei]?[:\s]+(\d+)\s*\]", r"[\1]", text, flags=re.I)
        # bare "Fonte: [1]" (no parentheses) → [1]; require the colon so prose like
        # "più fonti [1]" (where 'fonti' is a real word) is left untouched.
        text = re.sub(r"\bfont[ei]?:\s*(\[\d+\])", r"\1", text, flags=re.I)
        # tidy: collapse runs of spaces and re-attach punctuation pushed off by the strip
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
        return text

    @staticmethod
    def _ordered_sources(metas: List[dict]) -> List[str]:
        """Distinct source names in FIRST-APPEARANCE order. This order defines the citation
        numbering shared by the context labels ([Fonte N]) and the 'Fonti citate' legend, so
        an inline [N] always points to the right entry."""
        out: List[str] = []
        for m in metas:
            src = str(m.get("source", "sconosciuto"))
            if src not in out:
                out.append(src)
        return out

    @staticmethod
    def _format_sources(metas: List[dict]) -> List[str]:
        """The numbered 'Fonti citate' legend — one entry per file, in citation order. Chunks
        from the same document on different pages are merged: the file name appears once and
        its pages are listed together (es. 'X.pdf (pagg. 5, 11, 18)'). The order is NOT sorted:
        it matches the inline [N] markers produced from _build_context."""
        order = RAGChain._ordered_sources(metas)
        pages_by_src: dict = {s: [] for s in order}
        for m in metas:
            src = str(m.get("source", "sconosciuto"))
            page = m.get("page")
            if page is not None and page not in pages_by_src[src]:
                pages_by_src[src].append(page)
        labels = []
        for src in order:
            pages = pages_by_src[src]
            if not pages:
                labels.append(src)
                continue
            try:
                ordered = sorted(pages, key=lambda p: int(p))
            except (TypeError, ValueError):
                ordered = pages
            tag = "pag. " if len(ordered) == 1 else "pagg. "
            labels.append(f"{src} ({tag}{', '.join(str(p) for p in ordered)})")
        return labels

    @staticmethod
    def _build_context(docs: List[str], metas: List[dict]) -> str:
        """Prefix each chunk with its source NUMBER ([Fonte N]) so the model cites inline as
        [N]. The numbering is first-appearance order and matches the 'Fonti citate' legend
        (_format_sources), so [N] in the answer maps to legend entry N. Chunks from the same
        document share the same N."""
        idx = {s: i + 1 for i, s in enumerate(RAGChain._ordered_sources(metas))}
        parts: List[str] = []
        for doc, m in zip(docs, metas):
            n = idx.get(str(m.get("source", "sconosciuto")), "?")
            parts.append(f"[{n}]\n{doc}")
        return "\n\n".join(parts)

    @staticmethod
    def _ambiguity_block(metas: List[dict]) -> str:
        """Bulleted list of the distinct provvedimenti (decreto/data/pagina) for discretion."""
        seen, lines = set(), []
        for m in metas:
            src = str(m.get("source", "?"))
            if src in seen:
                continue
            seen.add(src)
            tag = src
            if m.get("decreto"):
                tag += f", decreto {m['decreto']}"
            if m.get("data_decreto"):
                tag += f" del {m['data_decreto']}"
            if m.get("page"):
                tag += f" (pag. {m['page']})"
            lines.append(f"• {tag}")
        return "\n".join(lines)

    @staticmethod
    def _obsolete_block(metas: List[dict]) -> str:
        """Bulleted list of the OBSOLETE provvedimenti, built purely from metadata.

        Only `status == "obsolete"` is surfaced: a superseded law is informative
        ("abrogato da Z"). `poisoned`/`draft` chunks are deliberately NOT listed —
        they stay invisible and fall through to the generic refusal."""
        seen, lines = set(), []
        for m in metas:
            if m.get("status") != "obsolete":
                continue
            src = str(m.get("source", "?"))
            if src in seen:
                continue
            seen.add(src)
            tag = src
            if m.get("decreto"):
                tag += f", decreto {m['decreto']}"
            if m.get("data_decreto"):
                tag += f" del {m['data_decreto']}"
            extra = []
            if m.get("validity_end"):
                extra.append(f"in vigore fino al {m['validity_end']}")
            if m.get("replaced_by"):
                extra.append(f"sostituito da {m['replaced_by']}")
            if extra:
                tag += f" ({'; '.join(extra)})"
            lines.append(f"• {tag}")
        return "\n".join(lines)

    def _obsolete_notice(
        self, standalone_query: str, k: Optional[int]
    ) -> Optional[Tuple[str, List[str]]]:
        """When the filtered retrieval found nothing relevant, check whether the best
        UNFILTERED match is an obsolete provvedimento. If so, return a deterministic
        (no-LLM) abrogation notice + its source labels; otherwise None (generic refusal)."""
        if not config.OBSOLETE_NOTICE_ENABLED:
            return None
        docs, metas, _scores, top_score = self.retrieve(
            standalone_query, k=k, apply_status_filter=False
        )
        if not docs or top_score < config.SCORE_THRESHOLD:
            return None
        block = self._obsolete_block(metas)
        if not block:
            return None  # the relevant hidden chunk is poisoned/draft, not obsolete
        flagged = [m for m in metas if m.get("status") == "obsolete"]
        return OBSOLETE_MESSAGE + "\n\n" + block, self._format_sources(flagged)

    @staticmethod
    def _dominant_source(metas: List[dict], scores: List[float]) -> bool:
        """True if ONE source clearly leads the fused (RRF) ranking over every other source.

        When a single provvedimento dominates retrieval, the answer is firmly anchored to it:
        there is no genuine ambiguity to arbitrate, so the RF19 conflict judge is skipped.
        This stops RF19 from misfiring when several parallel, non-contradictory provvedimenti
        are retrieved together. A flat ranking (no clear leader) is NOT dominant → judge runs."""
        best_by_source: dict = {}
        for m, s in zip(metas, scores):
            src = str(m.get("source"))
            if s > best_by_source.get(src, float("-inf")):
                best_by_source[src] = s
        if len(best_by_source) < 2:
            return True  # a single source cannot conflict with itself
        top1, top2 = sorted(best_by_source.values(), reverse=True)[:2]
        return top2 <= 0 or top1 >= top2 * (1.0 + config.AMBIGUITY_DOMINANCE_GAP)

    def _detect_conflict(self, standalone_query: str, docs: List[str], metas: List[dict],
                         session=None) -> bool:
        """LLM judge: do the retrieved sources conflict? Fail-safe to True (discretion)."""
        prompt = CONFLICT_JUDGE_PROMPT.format(
            query=standalone_query, context_str=self._build_context(docs, metas)
        )
        try:
            out = self._complete(
                [{"role": "user", "content": prompt}],
                session=session, temperature=0.0, max_tokens=4,
            )
            return "CONFLITTO" in out.upper()
        except Exception:
            return True  # fail-safe: prefer discretion (legal caution)

    # ── conversational-memory helpers ────────────────────────────────────────

    @staticmethod
    def _build_chat_context(chat_history: List[dict]) -> str:
        """Format recent chat history as a readable transcript."""
        if not chat_history:
            return "(Nessuna cronologia precedente)"

        lines: List[str] = []
        for msg in chat_history[-6:]:  # keep the last 6 messages to bound tokens
            role = "Venditore" if msg["role"] == "user" else "Assistente"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def _reformulate_query(
        self, query: str, chat_history: List[dict], session=None
    ) -> str:
        """Use the LLM to produce a standalone question incorporating history.

        Masking is transparent: the prompt is pseudonymized for the provider and
        the returned standalone query is re-identified, so retrieval still embeds
        the real entities."""
        if not chat_history:
            return query  # no history → no reformulation needed

        chat_context = self._build_chat_context(chat_history)
        prompt = CONDENSE_QUESTION_PROMPT.format(
            chat_context=chat_context, query=query
        )
        content = self._complete(
            [{"role": "user", "content": prompt}], session=session, temperature=0.0,
        )
        return content.strip() if content else query

    # ── main entry point ─────────────────────────────────────────────────────

    def query(
        self,
        question: str,
        chat_history: Optional[List[dict]] = None,
        k: Optional[int] = None,
        role: Optional[str] = None,
    ) -> dict:
        """
        Execute the full RAG pipeline. If `role` is given (sales/presales/bid_manager),
        the system prompt and the final answer are adapted to that role (role_manager),
        and a `confidence` (green/yellow/red) is returned.
        """
        chat_history = chat_history or []
        t0 = time.perf_counter()

        # Cache lookup: identical (question, role, k, history) → reuse the prior answer,
        # skipping retrieval + LLM entirely. Returns a shallow copy flagged cached=True.
        cache_key = self._cache_key(question, chat_history, k, role)
        if self._cache is not None and cache_key is not None:
            hit = self._cache.get(cache_key)
            if hit is not None:
                out = dict(hit)
                out["cached"] = True
                out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                return out

        # Optional role layer — never breaks the pipeline if unavailable.
        rm = None
        if role:
            try:
                from role_manager import RoleManager, ROLES
                if role in ROLES:
                    rm = RoleManager(state_path=None)
                    rm.set_role(role, persist=False)
            except Exception:
                rm = None

        # Reversible pseudonymization session — ephemeral PII map, wiped in finally.
        session = self.pseudonymizer.session() if config.PII_MASKING_ENABLED else None
        try:
            result = self._run_pipeline(question, chat_history, k, rm, session, t0)
        finally:
            if session is not None:
                session.close()  # destroy the temporary map (zero residual PII)

        if self._cache is not None and cache_key is not None:
            self._cache.set(cache_key, dict(result))
        return result

    @staticmethod
    def _cache_key(
        question: str, chat_history: List[dict], k: Optional[int], role: Optional[str]
    ) -> Optional[str]:
        """Stable key over the inputs that determine the answer. History is included
        (it drives the standalone query); the question is whitespace-normalized so trivial
        formatting differences still hit. Returns None if the inputs are unhashable."""
        try:
            payload = {
                "q": " ".join(question.split()).lower(),
                "k": k,
                "role": role,
                "h": [
                    (str(m.get("role", "")), " ".join(str(m.get("content", "")).split()))
                    for m in chat_history[-6:]  # same window the condenser actually uses
                ],
            }
            blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            return hashlib.sha256(blob.encode("utf-8")).hexdigest()
        except Exception:
            return None

    def _run_pipeline(self, question, chat_history, k, rm, session, t0) -> dict:
        # Step 1 — conversational memory: standalone query (masked for the LLM)
        standalone_query = self._reformulate_query(question, chat_history, session=session)

        # Step 2 — retrieve (hybrid); gate on dense cosine (stable scale)
        docs, metas, scores, top_score = self.retrieve(standalone_query, k=k)
        distinct = len({str(m.get("source")) for m in metas}) if metas else 0
        obsolete = False

        if not docs or top_score < config.SCORE_THRESHOLD:
            # Gate 1 (RF10) — nothing relevant among the *current* (active) documents.
            # Before refusing, run the deterministic obsolescence check: if the only
            # relevant match was hidden because it is ABROGATO, say so (with the
            # replacing decree) instead of "non lo so". (🔴)
            notice = self._obsolete_notice(standalone_query, k)
            if notice is not None:
                response, sources = notice
                grounded, ambiguous, confidence, obsolete = False, False, "red", True
            else:
                grounded, ambiguous, confidence = False, False, "red"
                sources: List[str] = []
                response = rm.format_response("", [], "red") if rm else self.no_context_message
        elif (config.AMBIGUITY_JUDGE and 2 <= distinct <= config.AMBIGUITY_MAX_DISTINCT
              and not self._dominant_source(metas, scores)
              and self._detect_conflict(standalone_query, docs, metas, session=session)):
            # Gate 2 (ambiguity) — conflicting sources → discretion / defer to a human. (🔴)
            # RF19 targets a FOCUSED contradiction between few provvedimenti on the same point.
            # The judge (and its LLM call) is skipped when:
            #  - a single source supplies all chunks (distinct < 2 — cannot self-conflict);
            #  - retrieval is fragmented across many distinct sources (distinct > MAX_DISTINCT):
            #    a broad/under-specified query, not a contradiction (e.g. a cross-topic question
            #    pulling in several parallel autovelox decrees) → answer grounded, don't defer;
            #  - one source DOMINATES the fused ranking (no real ambiguity to arbitrate).
            grounded, ambiguous, confidence = False, True, "red"
            sources = self._format_sources(metas)
            # Discretion (RF19) is a distinct outcome from "no source": always cite the
            # conflicting provvedimenti and defer to the Bid Manager, for EVERY role — the
            # role-specific red template would otherwise hide the sources.
            response = AMBIGUITY_MESSAGE + "\n\n" + self._ambiguity_block(metas)
        else:
            # Generate on labeled, cited context. 🟢 single source · 🟡 combined sources.
            grounded, ambiguous = True, False
            confidence = "yellow" if distinct >= 2 else "green"
            sources = self._format_sources(metas)
            context_str = self._build_context(docs, metas)
            if rm:
                system_prompt = rm.get_system_prompt() + "\n\nDOCUMENTI AZIENDALI:\n" + context_str
                max_tokens = rm.get_current_role().max_response_length
            else:
                system_prompt = self.system_prompt_template.format(
                    context_str=context_str, standalone_query=standalone_query
                )
                max_tokens = None
            raw = self._complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                session=session, temperature=0.3, max_tokens=max_tokens,
            )
            raw = self._normalize_citations(raw)  # force bare [N] inline citations
            response = rm.format_response(raw, metas, confidence) if rm else raw

        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = {
            "query": question,
            "standalone_query": standalone_query,
            "response": response,
            "context": docs,
            "sources": sources,
            "model": self.model,
            "grounded": grounded,
            "ambiguous": ambiguous,
            "obsolete": obsolete,
            "top_score": top_score,
            "role": rm.current_key if rm else None,
            "confidence": confidence,
            "pii_masked": session.masked_count if session is not None else 0,
            "latency_ms": latency_ms,
            "cached": False,
        }
        logger.info(
            "query role=%s grounded=%s ambiguous=%s obsolete=%s confidence=%s top_score=%.3f sources=%d pii_masked=%d latency_ms=%d",
            result["role"], grounded, ambiguous, obsolete, confidence, top_score, len(sources),
            result["pii_masked"], result["latency_ms"],
        )
        return result
