"""
RAG MCP server for Claude Code.

Provides local vector search over documents and text using ChromaDB
and Ollama embeddings. Claude handles reasoning and synthesis —
this server handles chunking, embedding, and retrieval.

Tools:
  - create_collection   : initialize a new vector DB at a given path
  - add_document        : chunk, embed, and index a PDF or DOCX file
  - add_text            : chunk, embed, and index raw text (web pages, notes, API output)
  - search              : semantic search returning ranked chunks with source metadata
  - collection_status   : document and chunk counts for a collection
  - list_collections    : show all known collections in a registry

Install into Claude Code (from repo root):
  ./install.sh

Or manually:
  claude mcp add rag --scope user -- uv run \\
    --project /path/to/mcps/rag \\
    fastmcp run /path/to/mcps/rag/rag_mcp.py
"""

import io
import json
import logging
import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

import chromadb
import docx
import fitz  # PyMuPDF
import httpx
from fastmcp import FastMCP
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

mcp = FastMCP("rag")

# ── constants ────────────────────────────────────────────────────────────────

# Registry: a single SQLite file that tracks all known collections
# Lives alongside the MCP script so it persists across sessions
REGISTRY_PATH = Path(__file__).parent / "collections_registry.db"

DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


# ── registry ─────────────────────────────────────────────────────────────────

def _init_registry():
    with sqlite3.connect(REGISTRY_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                name TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                description TEXT
            )
        """)
        conn.commit()


def _register_collection(name: str, path: str, description: str = ""):
    _init_registry()
    with sqlite3.connect(REGISTRY_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO collections (name, path, created_utc, description) VALUES (?, ?, ?, ?)",
            (name, path, datetime.now(timezone.utc).isoformat(), description)
        )
        conn.commit()


def _get_collection_path(name: str) -> str | None:
    _init_registry()
    with sqlite3.connect(REGISTRY_PATH) as conn:
        row = conn.execute(
            "SELECT path FROM collections WHERE name = ?", (name,)
        ).fetchone()
    return row[0] if row else None


def _all_collections() -> list[dict]:
    _init_registry()
    with sqlite3.connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            "SELECT name, path, created_utc, description FROM collections ORDER BY created_utc DESC"
        ).fetchall()
    return [{"name": r[0], "path": r[1], "created_utc": r[2], "description": r[3]} for r in rows]


# ── chroma helpers ────────────────────────────────────────────────────────────

def _chroma_collection(collection_name: str):
    path = _get_collection_path(collection_name)
    if not path:
        raise ValueError(f"Collection '{collection_name}' not found. Create it first with create_collection.")
    client = chromadb.PersistentClient(path=path)
    return client.get_or_create_collection(name=collection_name)


# ── embedding ─────────────────────────────────────────────────────────────────

def _embed(texts: list[str], model: str, ollama_url: str) -> list[list[float]]:
    """Embed a list of texts via Ollama."""
    embeddings = []
    with httpx.Client(timeout=120.0) as client:
        for text in texts:
            resp = client.post(
                f"{ollama_url}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            resp.raise_for_status()
            embeddings.append(resp.json()["embedding"])
    return embeddings


# ── extraction ────────────────────────────────────────────────────────────────

def _extract_pdf(path: Path) -> str:
    doc = fitz.open(str(path))
    return "\n".join(page.get_text("text") for page in doc)


def _extract_docx(path: Path) -> str:
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


def _extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _extract(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    elif ext == ".docx":
        return _extract_docx(path)
    elif ext in (".txt", ".md", ".tex", ".csv"):
        return _extract_text(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Supported: pdf, docx, txt, md, tex, csv.")


# ── chunking ──────────────────────────────────────────────────────────────────

def _chunk(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    return splitter.split_text(text)


# ── indexing ──────────────────────────────────────────────────────────────────

def _index_chunks(
    collection_name: str,
    chunks: list[str],
    source: str,
    metadata: dict,
    embed_model: str,
    ollama_url: str,
) -> int:
    col = _chroma_collection(collection_name)
    embeddings = _embed(chunks, model=embed_model, ollama_url=ollama_url)
    doc_id = str(uuid.uuid4())[:8]

    ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metas = [{"source": source, "chunk_index": i, **metadata} for i, _ in enumerate(chunks)]

    col.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metas)
    return len(chunks)


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_collection(
    name: str,
    path: str,
    description: str = "",
) -> str:
    """
    Initialize a new local vector database collection.
    Creates the ChromaDB storage at the given path and registers it by name.
    Call this once per project before adding documents.

    Args:
        name: Unique name for this collection (e.g. "quantum-risk-paper", "my-notes").
        path: Absolute path to the directory where the vector DB will be stored.
              Will be created if it doesn't exist (e.g. "/Users/you/project/rag_db").
        description: Optional description of what this collection contains.
    """
    db_path = Path(path).expanduser().resolve()
    db_path.mkdir(parents=True, exist_ok=True)

    # Initialize ChromaDB and create the collection
    client = chromadb.PersistentClient(path=str(db_path))
    client.get_or_create_collection(name=name)

    _register_collection(name=name, path=str(db_path), description=description)

    return json.dumps({
        "status": "created",
        "name": name,
        "path": str(db_path),
        "description": description,
    }, indent=2)


@mcp.tool()
def add_document(
    collection_name: str,
    file_path: str,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> str:
    """
    Extract, chunk, embed, and index a document into a collection.
    Supported formats: PDF, DOCX, TXT, MD, TEX, CSV.
    Embeddings are generated locally via Ollama — no data leaves your machine.

    Args:
        collection_name: Name of the collection to add the document to.
        file_path: Absolute path to the file to index.
        embed_model: Ollama embedding model to use (default: nomic-embed-text).
                     Run `ollama pull nomic-embed-text` if not already available.
        ollama_url: Ollama API base URL (default: http://localhost:11434).
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {path}"})

    try:
        text = _extract(path)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not text.strip():
        return json.dumps({"error": f"No text extracted from {path.name}. File may be scanned — try OCR first."})

    chunks = _chunk(text)
    metadata = {
        "filename": path.name,
        "filepath": str(path),
        "filetype": path.suffix.lower().lstrip("."),
        "indexed_utc": datetime.now(timezone.utc).isoformat(),
    }

    try:
        n = _index_chunks(
            collection_name=collection_name,
            chunks=chunks,
            source=path.name,
            metadata=metadata,
            embed_model=embed_model,
            ollama_url=ollama_url,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except httpx.ConnectError:
        return json.dumps({"error": f"Could not connect to Ollama at {ollama_url}. Is it running?"})

    return json.dumps({
        "status": "indexed",
        "collection": collection_name,
        "source": path.name,
        "chunks_indexed": n,
        "embed_model": embed_model,
    }, indent=2)


@mcp.tool()
def add_text(
    collection_name: str,
    text: str,
    source: str,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    extra_metadata: str = "",
) -> str:
    """
    Chunk, embed, and index raw text into a collection.
    Use this for content that isn't a file: web pages, API responses,
    Mistral OCR output, notes, or any string you want to make searchable.

    Args:
        collection_name: Name of the collection to add to.
        text: The raw text content to index.
        source: A label for this content used in search results (e.g. "arxiv:2501.01234",
                "https://example.com", "meeting-notes-2025-06-27").
        embed_model: Ollama embedding model to use (default: nomic-embed-text).
        ollama_url: Ollama API base URL (default: http://localhost:11434).
        extra_metadata: Optional JSON string of additional metadata to store
                        (e.g. '{"author": "Nel", "date": "2025-06-27"}').
    """
    if not text.strip():
        return json.dumps({"error": "Text is empty."})

    metadata = {
        "indexed_utc": datetime.now(timezone.utc).isoformat(),
    }
    if extra_metadata:
        try:
            metadata.update(json.loads(extra_metadata))
        except json.JSONDecodeError:
            return json.dumps({"error": "extra_metadata must be valid JSON."})

    chunks = _chunk(text)

    try:
        n = _index_chunks(
            collection_name=collection_name,
            chunks=chunks,
            source=source,
            metadata=metadata,
            embed_model=embed_model,
            ollama_url=ollama_url,
        )
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except httpx.ConnectError:
        return json.dumps({"error": f"Could not connect to Ollama at {ollama_url}. Is it running?"})

    return json.dumps({
        "status": "indexed",
        "collection": collection_name,
        "source": source,
        "chunks_indexed": n,
        "embed_model": embed_model,
    }, indent=2)


@mcp.tool()
def search(
    collection_name: str,
    query: str,
    n_results: int = 5,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> str:
    """
    Semantic search over a collection. Returns the most relevant chunks
    with source metadata. Claude should use these chunks to answer questions,
    synthesize information, or verify claims.

    Args:
        collection_name: Name of the collection to search.
        query: Natural language query (e.g. "volatility clustering in crypto markets").
        n_results: Number of chunks to return (default 5).
        embed_model: Must match the model used when indexing (default: nomic-embed-text).
        ollama_url: Ollama API base URL (default: http://localhost:11434).
    """
    try:
        col = _chroma_collection(collection_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        query_embedding = _embed([query], model=embed_model, ollama_url=ollama_url)[0]
    except httpx.ConnectError:
        return json.dumps({"error": f"Could not connect to Ollama at {ollama_url}. Is it running?"})

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, col.count() or 1),
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for i, (doc, meta, dist) in enumerate(zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )):
        chunks.append({
            "rank": i + 1,
            "score": round(1 - dist, 4),  # cosine similarity
            "source": meta.get("source", "unknown"),
            "chunk_index": meta.get("chunk_index"),
            "text": doc,
            "metadata": {k: v for k, v in meta.items() if k not in ("source", "chunk_index")},
        })

    return json.dumps({
        "collection": collection_name,
        "query": query,
        "n_results": len(chunks),
        "results": chunks,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def collection_status(collection_name: str) -> str:
    """
    Return the document count, chunk count, and metadata for a collection.
    Use this to check what's been indexed before searching.

    Args:
        collection_name: Name of the collection to inspect.
    """
    path = _get_collection_path(collection_name)
    if not path:
        return json.dumps({"error": f"Collection '{collection_name}' not found."})

    try:
        col = _chroma_collection(collection_name)
    except Exception as e:
        return json.dumps({"error": str(e)})

    total_chunks = col.count()

    # Count unique sources
    sources: set[str] = set()
    if total_chunks > 0:
        all_metas = col.get(include=["metadatas"])["metadatas"]
        sources = {m.get("source", "unknown") for m in all_metas}

    return json.dumps({
        "collection": collection_name,
        "path": path,
        "total_chunks": total_chunks,
        "unique_sources": len(sources),
        "sources": sorted(sources),
    }, indent=2)


@mcp.tool()
def list_collections() -> str:
    """
    List all known collections with their paths and descriptions.
    Use this to discover what RAG collections are available before searching.
    """
    cols = _all_collections()
    if not cols:
        return json.dumps({"message": "No collections found. Create one with create_collection.", "collections": []})

    return json.dumps({
        "count": len(cols),
        "collections": cols,
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
