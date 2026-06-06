#!/usr/bin/env python
"""Query the RAG system interactively"""
import sys
from src.nextpulse.vector_store import VectorStore
from src.nextpulse.rag_chain import RAGChain


def main():
    print("=" * 60)
    print("🔍 RAG Query Interface")
    print("=" * 60)
    
    try:
        # Initialize
        vector_store = VectorStore()
        rag = RAGChain()
        
        # Check if documents are indexed
        stats = vector_store.get_stats()
        if stats["count"] == 0:
            print("\n⚠️  No documents indexed!")
            print("   Run: python scripts/index_documents.py")
            return
        
        print(f"\n✅ Ready! Indexed: {stats['count']} chunks")
        print("   Type 'exit' to quit\n")
        
        # Interactive loop
        while True:
            query = input("❓ Question: ").strip()
            
            if query.lower() in ['exit', 'quit', 'q']:
                print("\n👋 Done!")
                break
            
            if not query:
                continue
            
            result = rag.query(query)
            
            print(f"\n💬 Response:")
            print("-" * 60)
            print(result["response"])
            print("-" * 60)
            print(f"📚 Context: {len(result['context'])} documents\n")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
