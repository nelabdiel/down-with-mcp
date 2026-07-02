"""
RAG management MCP server for Claude Code.

Provides local vector search over documents and text using ChromaDB
and Ollama embeddings. Claude handles reasoning and synthesis —
this server handles chunking, embedding, retrieval, and citation formatting.

Tools:
  - create_collection   : initialize a new vector DB at a given path
  - add_document        : chunk, embed, and index a PDF or DOCX file
  - add_text            : chunk, embed, and index raw text (web pages, notes, API output)
  - search              : baseline semantic search (best for exact entities, dates, IDs)
  - search_multi_query  : multi-phrasing search for synonym/coverage problems
  - search_hyde         : hypothetical document search for abstract/conceptual queries
  - search_mmr          : diversity-aware search using Maximal Marginal Relevance
  - collection_status   : document and chunk counts for a collection
  - list_collections    : show all known collections in a registry

Citation support:
  Every search result includes a ready-to-use `citation` string (e.g. "[report.pdf, p. 4]")
  and a `text_preview` field. PDFs are extracted page-by-page so page numbers are always
  available in results. Use the citation strings inline when synthesizing answers.

Install into Claude Code (from repo root):
  ./install.sh

Or manually:
  claude mcp add rag-management --scope user -- uv run \\
    --project /path/to/mcps/rag-management \\
    fastmcp run /path/to/mcps/rag-management/rag_manager.py
"""

import io
import json
import logging
import re
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

mcp = FastMCP("rag-management")

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

def _extract_pdf(path: Path) -> tuple[str, dict[int, int]]:
    """
    Extract text from a PDF page-by-page.

    Returns:
        text: Full document text with page boundary markers in the form
              \x00PAGE:N\x00 (null-delimited so they survive chunking and
              can be parsed back out without polluting visible content).
        page_char_offsets: Mapping of {page_number (1-based): char offset where
              that page starts in the returned text}. Used during chunking to
              assign page numbers to chunks.
    """
    doc = fitz.open(str(path))
    parts: list[str] = []
    page_char_offsets: dict[int, int] = {}
    cursor = 0

    for page_num, page in enumerate(doc, start=1):
        marker = f"\x00PAGE:{page_num}\x00"
        page_char_offsets[page_num] = cursor + len(marker)
        page_text = page.get_text("text")
        segment = marker + page_text
        parts.append(segment)
        cursor += len(segment)

    return "".join(parts), page_char_offsets


def _extract_docx(path: Path) -> tuple[str, dict]:
    document = docx.Document(str(path))
    text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
    return text, {}


def _extract_text(path: Path) -> tuple[str, dict]:
    return path.read_text(encoding="utf-8", errors="replace"), {}


def _extract(path: Path) -> tuple[str, dict[int, int]]:
    """
    Extract text from a supported file.

    Returns:
        (text, page_char_offsets) where page_char_offsets maps
        page_number -> char offset in text (only populated for PDFs).
    """
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

# Matches the page markers inserted by _extract_pdf
_PAGE_MARKER_RE = re.compile(r"\x00PAGE:(\d+)\x00")


def _page_at_offset(offset: int, page_char_offsets: dict[int, int]) -> int | None:
    """
    Return the 1-based page number that contains `offset` in the full text.
    page_char_offsets maps page_number -> char offset where that page's *text*
    starts (i.e. after the marker). Returns None for non-PDF sources.
    """
    if not page_char_offsets:
        return None
    best_page = None
    for page_num, page_start in sorted(page_char_offsets.items()):
        if page_start <= offset:
            best_page = page_num
        else:
            break
    return best_page


def _strip_page_markers(text: str) -> str:
    """Remove the internal \x00PAGE:N\x00 markers from visible text."""
    return _PAGE_MARKER_RE.sub("", text)


def _chunk(text: str, page_char_offsets: dict[int, int] | None = None) -> list[dict]:
    """
    Split text into overlapping chunks.

    Returns a list of dicts:
        {
            "text": str,          # chunk text (page markers stripped)
            "page_number": int | None,   # 1-based page where chunk starts
            "char_offset": int,   # character offset of chunk start in original text
        }
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    raw_chunks = splitter.split_text(text)
    offsets = _compute_chunk_offsets(text, raw_chunks)
    po = page_char_offsets or {}

    result = []
    for raw, offset in zip(raw_chunks, offsets):
        result.append({
            "text": _strip_page_markers(raw),
            "page_number": _page_at_offset(offset, po),
            "char_offset": offset,
        })
    return result


def _compute_chunk_offsets(text: str, chunks: list[str]) -> list[int]:
    """
    Walk through the original text and find the start offset of each chunk.
    Handles overlapping chunks correctly by advancing a cursor.
    """
    offsets = []
    cursor = 0
    for chunk in chunks:
        idx = text.find(chunk, cursor)
        if idx == -1:
            # Fallback: use cursor if exact match not found (shouldn't happen)
            offsets.append(cursor)
        else:
            offsets.append(idx)
            cursor = idx  # allow overlap — next chunk may start before end of this one
    return offsets


# ── indexing ──────────────────────────────────────────────────────────────────

def _index_chunks(
    collection_name: str,
    chunks: list[dict],
    source: str,
    metadata: dict,
    embed_model: str,
    ollama_url: str,
) -> int:
    """
    Embed and store chunks in ChromaDB.

    Each chunk dict must have at minimum:
        "text"        : str   — the chunk text
        "page_number" : int | None
        "char_offset" : int

    Stored metadata per chunk includes source, chunk_index, page_number,
    char_offset, and everything from the caller-supplied metadata dict.
    """
    col = _chroma_collection(collection_name)
    texts = [c["text"] for c in chunks]
    embeddings = _embed(texts, model=embed_model, ollama_url=ollama_url)
    doc_id = str(uuid.uuid4())[:8]

    ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
    metas = []
    for i, chunk in enumerate(chunks):
        m = {
            "source": source,
            "chunk_index": i,
            "page_number": chunk["page_number"] if chunk["page_number"] is not None else -1,
            "char_offset": chunk["char_offset"],
            **metadata,
        }
        metas.append(m)

    col.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
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
        text, page_char_offsets = _extract(path)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not text.strip():
        return json.dumps({"error": f"No text extracted from {path.name}. File may be scanned — try OCR first."})

    # Determine total page count for PDFs (stored in metadata for citation use)
    total_pages = max(page_char_offsets.keys()) if page_char_offsets else None

    chunks = _chunk(text, page_char_offsets)
    metadata = {
        "filename": path.name,
        "filepath": str(path),
        "filetype": path.suffix.lower().lstrip("."),
        "indexed_utc": datetime.now(timezone.utc).isoformat(),
        "total_pages": total_pages if total_pages is not None else -1,
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
        "total_pages": total_pages,
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
        "total_pages": -1,
    }
    if extra_metadata:
        try:
            metadata.update(json.loads(extra_metadata))
        except json.JSONDecodeError:
            return json.dumps({"error": "extra_metadata must be valid JSON."})

    # Raw text has no page structure — chunk without page offsets
    chunks = _chunk(text, page_char_offsets=None)

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
    with source metadata, page numbers, and ready-to-use citation strings.

    Each result includes:
      - citation     : inline citation string, e.g. "[report.pdf, p. 4]" — use this
                       directly in your response immediately after any claim it supports.
      - page_number  : 1-based page number (populated for PDFs; null for other sources).
      - text_preview : first 200 characters of the chunk for quick relevance scanning.
      - text         : full chunk text.
      - score        : cosine similarity (0–1). Scores below 0.5 are lower-confidence.

    After synthesizing an answer, append a "Sources" section listing each unique
    source cited with its page number(s).

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
        source = meta.get("source", "unknown")
        chunk_index = meta.get("chunk_index", i)
        page_raw = meta.get("page_number", -1)
        page_number = page_raw if page_raw and page_raw != -1 else None
        citation = _build_citation(source, meta, chunk_index)

        chunks.append({
            "rank": i + 1,
            "score": round(1 - dist, 4),  # cosine similarity
            "source": source,
            "chunk_index": chunk_index,
            "page_number": page_number,
            "citation": f"[{citation}]",
            "text": doc,
            "text_preview": doc[:PREVIEW_LENGTH] + ("…" if len(doc) > PREVIEW_LENGTH else ""),
            "metadata": {k: v for k, v in meta.items() if k not in ("source", "chunk_index", "page_number")},
        })

    return json.dumps({
        "collection": collection_name,
        "query": query,
        "method": "semantic",
        "n_results": len(chunks),
        "citation_instructions": (
            "Cite inline using the `citation` field in square brackets immediately after "
            "each claim, e.g. '...shown in prior work [report.pdf, p. 3].' "
            "End your response with a 'Sources' section listing unique sources cited."
        ),
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


# ── retrieval helpers ─────────────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _query_collection(
    col,
    embedding: list[float],
    n_results: int,
) -> list[dict]:
    """Run a single embedding query and return normalized result dicts."""
    results = col.query(
        query_embeddings=[embedding],
        n_results=min(n_results, col.count() or 1),
        include=["documents", "metadatas", "distances", "embeddings"],
    )
    chunks = []
    for doc, meta, dist, emb in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
        results["embeddings"][0],
    ):
        page_raw = meta.get("page_number", -1)
        chunks.append({
            "score": round(1 - dist, 4),
            "source": meta.get("source", "unknown"),
            "chunk_index": meta.get("chunk_index"),
            "page_number": page_raw if page_raw and page_raw != -1 else None,
            "text": doc,
            "embedding": emb,
            "metadata": {k: v for k, v in meta.items() if k not in ("source", "chunk_index", "page_number")},
        })
    return chunks


def _deduplicate(all_chunks: list[dict]) -> list[dict]:
    """Keep best score per unique (source, chunk_index) pair."""
    seen: dict[tuple, dict] = {}
    for chunk in all_chunks:
        # Deduplicate by (source, chunk_index) — chunk_index is globally unique per
        # indexed document, so this correctly identifies identical passages even when
        # retrieved by different query variants.
        key = (chunk["source"], chunk["chunk_index"])
        if key not in seen or chunk["score"] > seen[key]["score"]:
            seen[key] = chunk
    return sorted(seen.values(), key=lambda c: c["score"], reverse=True)


PREVIEW_LENGTH = 200  # characters for text_preview field


def _build_citation(source: str, metadata: dict, chunk_index: int) -> str:
    """
    Build a compact, human-readable citation string for a chunk.

    Format examples:
        "report.pdf, p. 4"          — PDF with known page
        "report.pdf, chunk 7"       — PDF where page could not be determined
        "notes.md, chunk 3"         — non-PDF source
        "web-scrape, chunk 0"       — raw text indexed via add_text

    The citation is ready to drop inline, e.g.:
        "...as described in the methodology [report.pdf, p. 4]."
    """
    page = metadata.get("page_number", -1)
    if page and page != -1:
        location = f"p. {page}"
    else:
        location = f"chunk {chunk_index}"
    return f"{source}, {location}"


def _format_results(chunks: list[dict], query: str, collection_name: str, method: str) -> str:
    """
    Serialize ranked search results to JSON.

    Each result includes:
      - rank, score, source, chunk_index
      - page_number  : 1-based page (None if not a PDF or page unknown)
      - citation     : ready-to-use inline citation string, e.g. "[report.pdf, p. 4]"
      - text         : full chunk text
      - text_preview : first 200 characters of chunk text (for quick scanning)
      - metadata     : all other stored metadata fields

    CITATION INSTRUCTIONS FOR CLAUDE
    ─────────────────────────────────
    When synthesizing an answer from these results:
    1. Use the `citation` field inline, wrapped in square brackets, immediately
       after any claim drawn from that chunk. Example:
         "Volatility clustering was shown to persist across market regimes
          [crypto_study.pdf, p. 12], consistent with earlier equity findings
          [lit_review.pdf, p. 3]."
    2. At the end of your response, add a "Sources" section listing each unique
       source cited, with its score and page range if multiple pages were used.
    3. If score < 0.5, note that the match is lower-confidence.
    4. Never fabricate page numbers — only use the `page_number` from this payload.
    """
    ranked = []
    for i, c in enumerate(chunks):
        meta = c.get("metadata", {})
        page_raw = meta.get("page_number", -1)
        page_number = page_raw if page_raw and page_raw != -1 else None
        citation = _build_citation(c["source"], meta, c["chunk_index"])

        ranked.append({
            "rank": i + 1,
            "score": c["score"],
            "source": c["source"],
            "chunk_index": c["chunk_index"],
            "page_number": page_number,
            "citation": f"[{citation}]",
            "text": c["text"],
            "text_preview": c["text"][:PREVIEW_LENGTH] + ("…" if len(c["text"]) > PREVIEW_LENGTH else ""),
            "metadata": {k: v for k, v in meta.items() if k not in ("page_number",)},
        })

    return json.dumps({
        "collection": collection_name,
        "query": query,
        "method": method,
        "n_results": len(ranked),
        "citation_instructions": (
            "Cite inline using the `citation` field in square brackets immediately after "
            "each claim, e.g. '...shown in prior work [report.pdf, p. 3].' "
            "End your response with a 'Sources' section listing unique sources cited."
        ),
        "results": ranked,
    }, ensure_ascii=False, indent=2)


# ── advanced search tools ─────────────────────────────────────────────────────

@mcp.tool()
def search_multi_query(
    collection_name: str,
    query: str,
    query_variants: list[str],
    n_results: int = 5,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> str:
    """
    Multi-query search: retrieve for the original query plus phrasing variants,
    then deduplicate and return the union ranked by best score.

    Use this when the query might suffer from synonym or coverage problems —
    e.g. "ML model accuracy" might miss docs that say "neural network F1-score".
    Claude should generate the variants before calling this tool.

    Best for: synonym-heavy queries, terminology variations, broader coverage.
    Not ideal for: exact entity/ID lookups (use search instead).

    Args:
        collection_name: Name of the collection to search.
        query: The original natural language query.
        query_variants: 2-4 rephrased versions of the query. Preserve meaning,
                        vary phrasing. Do NOT change entities, dates, or numbers.
                        Example: ["machine learning classifier performance",
                                  "neural network prediction quality",
                                  "model evaluation metrics"]
        n_results: Number of results to return per variant before deduplication.
        embed_model: Must match the model used when indexing (default: nomic-embed-text).
        ollama_url: Ollama API base URL (default: http://localhost:11434).
    """
    try:
        col = _chroma_collection(collection_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    all_queries = [query] + query_variants[:4]  # cap at 4 variants per slide 22
    all_chunks: list[dict] = []

    try:
        for q in all_queries:
            emb = _embed([q], model=embed_model, ollama_url=ollama_url)[0]
            chunks = _query_collection(col, emb, n_results)
            all_chunks.extend(chunks)
    except httpx.ConnectError:
        return json.dumps({"error": f"Could not connect to Ollama at {ollama_url}. Is it running?"})

    unique = _deduplicate(all_chunks)[:15]  # hard cap per slide 22
    return _format_results(unique, query, collection_name, method="multi_query")


@mcp.tool()
def search_hyde(
    collection_name: str,
    query: str,
    hypothesis: str,
    n_results: int = 5,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> str:
    """
    HyDE (Hypothetical Document Embeddings) search: embed a hypothetical answer
    instead of the raw query, then search for real documents similar to it.

    Why this works: answers and documents use similar language/style, while
    questions use different language. Embedding a fake answer bridges that gap.

    Claude should write the hypothesis before calling this tool:
      "Write a concise factual paragraph (4-6 sentences) that would answer
       this question. Write in the style of academic or technical documentation.
       Do not acknowledge uncertainty. Question: {query}"

    Best for: abstract/conceptual queries, "What is X?" definitions,
              questions where the query phrasing differs from how docs are written.
    Not ideal for: exact entity lookups, specific dates/numbers (use search instead).

    Args:
        collection_name: Name of the collection to search.
        query: The original question (used for result labeling only).
        hypothesis: A hypothetical answer paragraph written by Claude.
                    This is what gets embedded and searched — NOT the query.
        n_results: Number of chunks to return.
        embed_model: Must match the model used when indexing (default: nomic-embed-text).
        ollama_url: Ollama API base URL (default: http://localhost:11434).
    """
    try:
        col = _chroma_collection(collection_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    try:
        # Embed the hypothesis, not the query
        hyp_embedding = _embed([hypothesis], model=embed_model, ollama_url=ollama_url)[0]
    except httpx.ConnectError:
        return json.dumps({"error": f"Could not connect to Ollama at {ollama_url}. Is it running?"})

    chunks = _query_collection(col, hyp_embedding, n_results)
    return _format_results(chunks, query, collection_name, method="hyde")


@mcp.tool()
def search_mmr(
    collection_name: str,
    query: str,
    n_results: int = 5,
    n_candidates: int = 20,
    lambda_mmr: float = 0.7,
    embed_model: str = DEFAULT_EMBED_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> str:
    """
    MMR (Maximal Marginal Relevance) search: retrieves a diverse set of chunks
    by balancing relevance to the query with dissimilarity to already-selected chunks.

    Avoids returning 5 nearly identical passages from the same section of a document.
    Formula: MMR = argmax [ λ·Sim(chunk, query) - (1-λ)·max Sim(chunk, selected) ]

    Best for: broad exploratory queries, getting coverage across a document,
              avoiding redundant passages when the corpus has repeated content.
    Not ideal for: when you specifically want the top-N most similar chunks.

    Args:
        collection_name: Name of the collection to search.
        query: Natural language query.
        n_results: Number of diverse results to return (default 5).
        n_candidates: Candidate pool size before MMR reranking (default 20).
                      Larger = better diversity but slower.
        lambda_mmr: Balance between relevance and diversity (default 0.7).
                    1.0 = pure relevance (same as baseline search).
                    0.0 = pure diversity.
                    0.7 = favor relevance slightly, per standard practice.
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

    # Fetch candidate pool
    candidates = _query_collection(col, query_embedding, n_candidates)
    if not candidates:
        return json.dumps({"collection": collection_name, "query": query,
                           "method": "mmr", "n_results": 0, "results": []})

    # MMR selection
    selected: list[dict] = []
    selected_embeddings: list[list[float]] = []
    remaining = list(candidates)

    while len(selected) < n_results and remaining:
        best_score = -float("inf")
        best_chunk = None

        for chunk in remaining:
            relevance = _cosine_similarity(query_embedding, chunk["embedding"])
            if selected_embeddings:
                max_sim = max(
                    _cosine_similarity(chunk["embedding"], sel_emb)
                    for sel_emb in selected_embeddings
                )
            else:
                max_sim = 0.0

            mmr_score = lambda_mmr * relevance - (1 - lambda_mmr) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_chunk = chunk

        if best_chunk:
            selected.append({**best_chunk, "score": round(best_score, 4)})
            selected_embeddings.append(best_chunk["embedding"])
            remaining.remove(best_chunk)

    return _format_results(selected, query, collection_name, method="mmr")


if __name__ == "__main__":
    mcp.run(transport="stdio")
