# NextPulse RAG — Hackathon Boilerplate

Retrieval-Augmented Generation system for the hackathon: local vector DB + local embeddings + OpenAI LLM generation.

## Architecture

```
PDF/TXT files  →  chunk  →  embed (all-MiniLM-L6-v2, local)  →  ChromaDB (local disk)
                                                                       ↓
                               User query  →  embed  →  retrieve top-K  →  prompt + OpenAI API  →  response
```

| Phase | Where | Cost |
|-------|-------|------|
| PDF parsing & chunking | Local CPU | Free |
| Embeddings | `sentence-transformers` (all-MiniLM-L6-v2, ~90 MB) | Free |
| Vector storage & retrieval | ChromaDB (SQLite on disk) | Free |
| Final generation | OpenAI API (`gpt-4-turbo`) | Token-based |

## Project Structure

```
NextPulse/
├── src/nextpulse/
│   ├── __init__.py
│   ├── config.py              # .env loader + settings
│   ├── document_processor.py  # PDF/TXT parsing + chunking
│   ├── vector_store.py        # ChromaDB + sentence-transformers
│   └── rag_chain.py           # Retrieve → prompt → OpenAI
├── scripts/
│   ├── index_documents.py     # Index all files from data/
│   └── query_rag.py           # Interactive Q&A
├── data/                      # Drop your PDFs/TXTs here
├── chroma_data/               # ChromaDB persistence (auto-created)
├── .env.example               # Template for your API key
├── .env                       # Your actual config (gitignored)
├── pyproject.toml
└── README.md
```

## Quick Start

### 1. Install

```bash
cd /home/thomas/Sync/NextPulse
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

(First embedding run downloads `all-MiniLM-L6-v2` — ~30 s, one-time.)

### 2. Set your OpenAI key

```bash
cp .env.example .env
# Edit .env → add OPENAI_API_KEY=sk-...
```

### 3. Add documents

```bash
cp path/to/your/*.pdf data/
```

### 4. Index

```bash
python scripts/index_documents.py
```

### 5. Query

```bash
python scripts/query_rag.py
```

Type `exit` to quit.

## Configuration (.env)

```ini
OPENAI_API_KEY=sk-...          # required
CHAT_MODEL=gpt-4-turbo         # or gpt-3.5-turbo
CHUNK_SIZE=500                 # characters per chunk
CHUNK_OVERLAP=50               # overlap between chunks
RETRIEVAL_K=5                  # top-K documents to retrieve
CHROMA_PERSIST_DIR=./chroma_data
DATA_DIR=./data
```

## Programmatic Usage

```python
from src.nextpulse.vector_store import VectorStore
from src.nextpulse.rag_chain import RAGChain

vs = VectorStore()
rag = RAGChain()

# Index
chunks = [("some text", {"source": "doc1"})]
texts = [c[0] for c in chunks]
metas = [c[1] for c in chunks]
vs.add_documents(texts, metas)

# Query
result = rag.query("What is X?")
print(result["response"])
print(result["context"])  # retrieved chunks
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `OPENAI_API_KEY not set` | Create `.env` from `.env.example` |
| `No module 'src'` | Run from project root, venv activated |
| `No documents found` | Put PDFs/TXTs in `data/`, re-index |
| ChromaDB errors | `rm -rf chroma_data/` then re-index |
| Slow first query | Model downloads once (~30 s) |

## Dependencies

- `chromadb` — vector DB
- `sentence-transformers` — local embeddings (all-MiniLM-L6-v2)
- `openai` — LLM API client
- `pypdf` — PDF parsing
- `python-dotenv` — `.env` loading

## License

MIT
