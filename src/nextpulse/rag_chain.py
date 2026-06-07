"""RAG chain — retrieve, reformulate with conversational memory, generate"""
import logging
import time
from typing import List, Optional, Tuple
from openai import OpenAI
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
3. TONE OF VOICE: Professionale, sintetico, strutturato (usa elenchi puntati).
4. CITAZIONI: Cita SEMPRE la fonte di ogni informazione usando l'etichetta [Fonte: ...] \
che precede ciascun documento qui sotto (con pagina/articolo se presenti) ed elenca le \
fonti utilizzate alla fine della risposta.

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

# LLM judge: decides whether the retrieved passages conflict (one-word answer).
CONFLICT_JUDGE_PROMPT = """\
Sei un revisore normativo rigoroso. Di seguito alcuni estratti recuperati per una domanda. \
Stabilisci se contengono una CONTRADDIZIONE DIRETTA tra fonti diverse sullo stesso punto specifico \
(es. due decreti che fissano valori, obblighi o scadenze DIVERSI per la stessa identica fattispecie).
NON è un conflitto: informazioni complementari, dettagli aggiuntivi, ripetizioni dello stesso \
contenuto, o estratti che trattano aspetti diversi dello stesso tema.
Rispondi con UNA sola parola: CONFLITTO solo se c'è una contraddizione diretta tra fonti diverse, \
altrimenti OK.

DOMANDA: {query}

ESTRATTI:
{context_str}

Risposta:"""


class RAGChain:
    """Retrieval-Augmented Generation chain with conversational memory"""

    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENROUTER_API_KEY / OPENAI_API_KEY not set — "
                "create a .env file or set the env var"
            )

        self.vector_store = VectorStore()
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
        self, query: str, k: Optional[int] = None
    ) -> Tuple[List[str], List[dict], List[float], float]:
        """Retrieve relevant chunks; returns (texts, metadatas, rrf_scores, max_cosine)"""
        k = k or config.RETRIEVAL_K
        return self.vector_store.search(query, k=k)

    # ── citation helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _format_sources(metas: List[dict]) -> List[str]:
        """Unique source document labels — one entry per file, not per page."""
        seen: set = set()
        labels = []
        for m in metas:
            src = str(m.get("source", "sconosciuto"))
            if src in seen:
                continue
            seen.add(src)
            page = m.get("page")
            labels.append(f"{src} (pag. {page})" if page else src)
        return sorted(labels)

    @staticmethod
    def _build_context(docs: List[str], metas: List[dict]) -> str:
        """Label each chunk with its source so the LLM can cite it precisely."""
        parts: List[str] = []
        for doc, m in zip(docs, metas):
            tag = str(m.get("source", "?"))
            if m.get("page"):
                tag += f", pag. {m['page']}"
            if m.get("section"):
                tag += f", {m['section']}"
            if m.get("decreto"):
                tag += f", decreto {m['decreto']}"
            parts.append(f"[Fonte: {tag}]\n{doc}")
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
            return self._run_pipeline(question, chat_history, k, rm, session, t0)
        finally:
            if session is not None:
                session.close()  # destroy the temporary map (zero residual PII)

    def _run_pipeline(self, question, chat_history, k, rm, session, t0) -> dict:
        # Step 1 — conversational memory: standalone query (masked for the LLM)
        standalone_query = self._reformulate_query(question, chat_history, session=session)

        # Step 2 — retrieve (hybrid); gate on dense cosine (stable scale)
        docs, metas, scores, top_score = self.retrieve(standalone_query, k=k)
        distinct = len({str(m.get("source")) for m in metas}) if metas else 0

        if not docs or top_score < config.SCORE_THRESHOLD:
            # Gate 1 (RF10) — nothing relevant → deterministic refusal, no generation. (🔴)
            grounded, ambiguous, confidence = False, False, "red"
            sources: List[str] = []
            response = rm.format_response("", [], "red") if rm else NO_CONTEXT_MESSAGE
        elif (config.AMBIGUITY_JUDGE and distinct >= 2
              and self._detect_conflict(standalone_query, docs, metas, session=session)):
            # Gate 2 (ambiguity) — conflicting sources → discretion / defer to a human. (🔴)
            # Only meaningful with ≥2 DISTINCT sources: chunks from one document cannot
            # conflict across provvedimenti, so the judge (and its LLM call) is skipped.
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
                system_prompt = SYSTEM_PROMPT.format(
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
            "top_score": top_score,
            "role": rm.current_key if rm else None,
            "confidence": confidence,
            "pii_masked": session.masked_count if session is not None else 0,
            "latency_ms": latency_ms,
        }
        logger.info(
            "query role=%s grounded=%s ambiguous=%s confidence=%s top_score=%.3f sources=%d pii_masked=%d latency_ms=%d",
            result["role"], grounded, ambiguous, confidence, top_score, len(sources),
            result["pii_masked"], result["latency_ms"],
        )
        return result
