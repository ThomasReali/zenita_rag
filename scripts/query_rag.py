#!/usr/bin/env python
"""Query the RAG system interactively.

Usage:
    python scripts/query_rag.py                 # uses the persisted role (default: presales)
    python scripts/query_rag.py --role sales    # set + persist the active role, then start
"""
import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' / 'role_manager' import standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from role_manager import RoleManager  # noqa: E402
from src.nextpulse.rag_chain import RAGChain  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="NextPulse — Query RAG (CLI)")
    parser.add_argument(
        "--role",
        choices=["sales", "presales", "bid_manager"],
        help="imposta e persiste il ruolo attivo prima di qualsiasi altra operazione",
    )
    args = parser.parse_args()

    role_mgr = RoleManager()
    if args.role:
        role_mgr.set_role(args.role)  # persisted before any other operation
    active_role = role_mgr.current_key

    print("=" * 60)
    print(f"🔍 Interfaccia Query RAG · profilo: {role_mgr.get_current_role().name} ({active_role})")
    print("=" * 60)

    try:
        rag = RAGChain()
        stats = rag.vector_store.get_stats()
        if stats["count"] == 0:
            print("\n⚠️  Nessun documento indicizzato!")
            print("   Esegui: python scripts/index_documents.py")
            return

        print(f"\n✅ Pronto! Indicizzati: {stats['count']} chunk")
        print("   Scrivi 'exit' per uscire\n")

        history = []  # conversational memory (multi-turn)
        while True:
            query = input("❓ Domanda: ").strip()
            if query.lower() in ("exit", "quit", "q"):
                print("\n👋 Fine!")
                break
            if not query:
                continue

            result = rag.query(query, chat_history=history, role=active_role)
            print("\n💬 Risposta:")
            print("-" * 60)
            print(result["response"])
            print("-" * 60)
            print(f"📚 Fonti: {', '.join(result['sources']) or '—'} · "
                  f"confidence: {result.get('confidence', '—')}\n")
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": result["response"]})

    except Exception as e:
        print(f"\n❌ Errore: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
