#!/usr/bin/env python
"""Index documents from data directory into ChromaDB"""
from pathlib import Path
from src.nextpulse import config
from src.nextpulse.document_processor import DocumentProcessor
from src.nextpulse.vector_store import VectorStore


def main():
    print("=" * 60)
    print("📑 Indexing Documents")
    print("=" * 60)
    
    processor = DocumentProcessor(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP
    )
    vector_store = VectorStore()
    
    # Check data directory
    if not config.DATA_DIR.exists():
        print(f"\n⚠️  Data directory not found: {config.DATA_DIR}")
        print("   Add .pdf or .txt files to ./data/")
        return
    
    # Process documents
    chunks = processor.process_directory(str(config.DATA_DIR))
    
    if not chunks:
        print(f"\n⚠️  No PDF/TXT files found in {config.DATA_DIR}")
        return
    
    # Index
    print("\n🔄 Adding to vector store...")
    documents = [chunk[0] for chunk in chunks]
    metadatas = [chunk[1] for chunk in chunks]
    vector_store.add_documents(documents, metadatas)
    
    # Summary
    stats = vector_store.get_stats()
    print("\n✅ Done!")
    print(f"   Chunks: {len(chunks)}")
    print(f"   Total indexed: {stats['count']}")
    print("\n💡 Query with: python scripts/query_rag.py")


if __name__ == "__main__":
    main()
