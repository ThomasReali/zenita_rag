#!/usr/bin/env python
"""Index documents from the data directory into Qdrant — incremental.

Only new/changed files (by content hash, tracked in a manifest) are re-processed; chunks
of changed/removed files are dropped first. Force a full rebuild by deleting the manifest
and the Qdrant store.
"""
import hashlib
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' import works standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse import config  # noqa: E402
from src.nextpulse.document_processor import DocumentProcessor  # noqa: E402
from src.nextpulse.vector_store import VectorStore  # noqa: E402


def _file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def main():
    print("=" * 60)
    print("📑 Indicizzazione documenti (incrementale)")
    print("=" * 60)

    if not config.DATA_DIR.exists():
        print(f"\n⚠️  Cartella dati non trovata: {config.DATA_DIR}")
        print("   Aggiungi file in ./data/ oppure imposta DATA_DIR=./KNOWLEDGE")
        return

    vector_store = VectorStore()

    # size chunks by the embedding model's tokens so they never exceed its window
    def token_len(text: str) -> int:
        return len(
            vector_store.embedder.tokenizer(text, add_special_tokens=False)["input_ids"]
        )

    processor = DocumentProcessor(
        min_size=config.CHUNK_MIN_TOKENS,
        max_size=config.CHUNK_MAX_TOKENS,
        length_fn=token_len,
    )

    # ── changed / removed files via content-hash manifest ──────────────────────
    files = processor.candidate_files(str(config.DATA_DIR))
    rel_to_path = {str(p.relative_to(config.DATA_DIR)): p for p in files}
    current = {rel: _file_hash(p) for rel, p in rel_to_path.items()}

    manifest_path = config.INDEX_MANIFEST
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            manifest = {}

    changed = [rel for rel, h in current.items() if manifest.get(rel) != h]
    removed = [rel for rel in manifest if rel not in current]

    print(f"\n🔎 {len(files)} file candidati | nuovi/modificati: {len(changed)} | "
          f"rimossi: {len(removed)} | invariati: {len(current) - len(changed)}")

    if not changed and not removed:
        print("\n✅ Indice già aggiornato — nessuna modifica.")
        return

    for rel in removed:
        vector_store.delete_by_source(Path(rel).name)

    total_chunks, skipped, failed = 0, 0, 0
    for i, rel in enumerate(changed, start=1):
        path = rel_to_path[rel]
        source = path.name
        vector_store.delete_by_source(source)  # drop the previous version's chunks
        try:
            chunks = processor.process_document(str(path))
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(changed)}] ❌ {source[:48]} :: {type(e).__name__}")
            continue
        if not chunks:
            skipped += 1
            print(f"  [{i}/{len(changed)}] ⏭️  {source[:48]} (nessun testo — scansione?)")
            continue
        vector_store.add_documents([c[0] for c in chunks], [c[1] for c in chunks])
        total_chunks += len(chunks)
        print(f"  [{i}/{len(changed)}] ✅ {source[:48]} → {len(chunks)} chunk")

    manifest_path.write_text(json.dumps(current, ensure_ascii=False))

    stats = vector_store.get_stats()
    print(f"\n✅ Fatto! chunk aggiunti: {total_chunks} | saltati: {skipped} | "
          f"falliti: {failed} | totale in Qdrant: {stats['count']}")
    print("\n💡 Avvia: uvicorn src.nextpulse.api:app --port 8000")


if __name__ == "__main__":
    main()
