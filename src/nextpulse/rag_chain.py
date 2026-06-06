"""RAG chain - retrieve and generate responses"""
from typing import List
from openai import OpenAI
from src.nextpulse.vector_store import VectorStore
from src.nextpulse import config


class RAGChain:
    """Retrieval-Augmented Generation chain"""
    
    def __init__(self):
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set")
        
        self.vector_store = VectorStore()
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.CHAT_MODEL
    
    def retrieve(self, query: str, k: int = None) -> List[str]:
        """Retrieve relevant documents"""
        k = k or config.RETRIEVAL_K
        docs, _ = self.vector_store.search(query, k=k)
        return docs
    
    def query(self, question: str, system_prompt: str = None) -> dict:
        """Execute RAG: retrieve context → generate response"""
        # Retrieve context
        context_docs = self.retrieve(question)
        context = "\n\n".join(context_docs)
        
        # Default system prompt
        if system_prompt is None:
            system_prompt = """You are a helpful assistant that answers questions based on provided documents.
Use the context to answer accurately. If the answer is not in the context, say so."""
        
        # Call OpenAI
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
            ],
            temperature=0.7,
        )
        
        return {
            "query": question,
            "response": response.choices[0].message.content,
            "context": context_docs,
            "model": self.model,
        }
