"""Vector store using ChromaDB with local embeddings"""
from typing import List, Optional, Tuple
import chromadb
from chromadb.api.types import Metadata
from sentence_transformers import SentenceTransformer
from src.nextpulse import config


class VectorStore:
    """Local vector store with ChromaDB and sentence-transformers"""

    def __init__(self):
        self.embedder = SentenceTransformer(config.EMBEDDING_MODEL)

        self.client = chromadb.PersistentClient(
            path=str(config.CHROMA_PERSIST_DIR),
        )
        self.collection = self.client.get_or_create_collection(
            name=config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def add_documents(
        self,
        texts: List[str],
        metadatas: List[Metadata],
        ids: Optional[List[str]] = None,
    ):
        """Add documents with embeddings to the store"""
        if ids is None:
            import time
            ts = int(time.time() * 1000)
            ids = [f"doc_{ts}_{i}" for i in range(len(texts))]

        embeddings = self.embedder.encode(
            texts, convert_to_tensor=False
        ).tolist()

        self.collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(
        self, query: str, k: Optional[int] = None
    ) -> Tuple[List[str], List[Metadata]]:
        """Search for similar documents, returns (texts, metadatas)"""
        k = k or config.RETRIEVAL_K
        query_embedding = self.embedder.encode(
            query, convert_to_tensor=False
        ).tolist()

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
        )

        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []

        return docs, metas

    def get_stats(self) -> dict:
        return {
            "count": self.collection.count(),
            "collection": config.COLLECTION_NAME,
        }
