"""RAG chain — retrieve, reformulate with conversational memory, generate"""
from typing import List, Optional, Tuple
from openai import OpenAI
from chromadb.api.types import Metadata
from src.nextpulse.vector_store import VectorStore
from src.nextpulse import config

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
4. CITAZIONI: Indica sempre la fonte da cui hai preso l'informazione alla fine \
della risposta.

DOCUMENTI AZIENDALI:
{context_str}

Domanda del Venditore: {standalone_query}
Risposta:"""


class RAGChain:
    """Retrieval-Augmented Generation chain with conversational memory"""

    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY not set — create a .env file or set the env var"
            )

        self.vector_store = VectorStore()
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.CHAT_MODEL

    # ── retrieval ────────────────────────────────────────────────────────────

    def retrieve(
        self, query: str, k: Optional[int] = None
    ) -> Tuple[List[str], List[Metadata]]:
        """Retrieve relevant chunks; returns (texts, metadatas)"""
        k = k or config.RETRIEVAL_K
        docs, metas = self.vector_store.search(query, k=k)
        return docs, metas

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
        self, query: str, chat_history: List[dict]
    ) -> str:
        """Use the LLM to produce a standalone question incorporating history."""
        if not chat_history:
            return query  # no history → no reformulation needed

        chat_context = self._build_chat_context(chat_history)
        prompt = CONDENSE_QUESTION_PROMPT.format(
            chat_context=chat_context, query=query
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = response.choices[0].message.content
        return content.strip() if content else query

    # ── main entry point ─────────────────────────────────────────────────────

    def query(
        self,
        question: str,
        chat_history: Optional[List[dict]] = None,
        k: Optional[int] = None,
    ) -> dict:
        """
        Execute the full RAG pipeline:

        1. Reformulate the question using conversational memory (if history)
        2. Retrieve relevant chunks with the standalone query
        3. Generate the final answer with the Engine SpA system prompt
        """
        chat_history = chat_history or []

        # Step 1 — conversational memory: standalone query
        standalone_query = self._reformulate_query(question, chat_history)

        # Step 2 — retrieve
        docs, metas = self.retrieve(standalone_query, k=k)
        context_str = "\n\n".join(docs)

        # Step 3 — generate
        system_prompt = SYSTEM_PROMPT.format(
            context_str=context_str
            or "(Nessun documento aziendale disponibile)",
            standalone_query=standalone_query,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.3,
        )

        answer = response.choices[0].message.content or ""

        # Extract unique sources from metadata
        sources = sorted({
            str(m.get("source", "sconosciuto")) for m in metas
        }) if metas else []

        return {
            "query": question,
            "standalone_query": standalone_query,
            "response": answer,
            "context": docs,
            "sources": sources,
            "model": self.model,
        }
