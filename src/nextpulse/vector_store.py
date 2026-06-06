"""Vector store using ChromaDB with local embeddings"""
from typing import List
import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer
from src.nextpulse import config


class VectorStore:
    """Local vector store with ChromaDB and sentence-transformers"""
    
    def __init__(self):
        # Load embedding model
        self.embedder = SentenceTransformer(config.EMBEDDING_MODEL)
        
        # Initialize ChromaDB
        chroma_settings = ChromaSettings(
            is_persistent=True,
            persist_directory=str(config.CHROMA_PERSIST_DIR),
            anonymized_telemetry=False,
        )
        self.client = chromadb.Client(chroma_settings)
        self.collection = self.client.get_or_create_collection(
            name=config.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
    
    def add_documents(self, texts: List[str], metadatas: List[dict], ids: List[str] = None):
        """Add documents with embeddings to the store"""
        if ids is None:
            ids = [f"doc_{i}" for i in range(len(texts))]
        
        embeddings = self.embedder.encode(texts, convert_to_tensor=False).tolist()
        
        self.collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas
        )
    
    def search(self, query: str, k: int = None) -> tuple[List[str], List[dict]]:
        """Search for similar documents"""
        k = k or config.RETRIEVAL_K
        query_embedding = self.embedder.encode(query, convert_to_tensor=False).tolist()
        
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k
        )
        
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        
        return docs, metas
    
    def get_stats(self) -> dict:
        return {
            "count": self.collection.count(),
            "collection": config.COLLECTION_NAME,
        }
