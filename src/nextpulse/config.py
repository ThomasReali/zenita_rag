"""Configuration for RAG system"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# OpenAI API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4-turbo")

# Embeddings (local model, no API cost)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ChromaDB
CHROMA_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR", "./chroma_data"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "documents")

# Document Processing
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))

# RAG
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

# Create directories
CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
