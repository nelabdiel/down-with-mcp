# rag MCP

Local semantic search over your documents. Index PDFs, DOCX files, or any raw text into a ChromaDB vector database and query it from Claude Code. Embeddings are generated locally via Ollama — no data leaves your machine.

## Tools

| Tool | Description |
|------|-------------|
| `create_collection` | Initialize a new vector DB at a given path |
| `add_document` | Extract, chunk, embed, and index a PDF, DOCX, TXT, MD, TEX, or CSV file |
| `add_text` | Chunk, embed, and index raw text — web pages, API output, notes, Mistral OCR results |
| `search` | Semantic search returning ranked chunks with source metadata |
| `collection_status` | Document and chunk counts for a collection |
| `list_collections` | Show all known collections |

## Prerequisites

Ollama must be running with an embedding model pulled:

```bash
ollama pull nomic-embed-text
```

`nomic-embed-text` is the default. Any Ollama embedding model works — pass `embed_model` to override.

## Usage

```
# Set up a collection for a project
create_collection("quantum-algorithms", "./qa/rag_db")

# Index documents
add_document("quantum-risk-paper", "./ref1.pdf")
add_document("quantum-risk-paper", "./draft.md")

# Index raw text (e.g. Mistral OCR output, web pages)
add_text("quantum-algorithm", "<text content>", source="mistral-ocr:scan.pdf")

# Search
search("quantum-algorithm", "coefficient counting complexity for non-commutative torus multiplication")

# Check what's indexed
collection_status("quantum-algorithm")
list_collections()
```

Then just ask Claude naturally:

```
What do my reference papers say about quantum risk in crypto?
Summarize what I've indexed about WRT invariants.
Find anything in my notes about torus bundles.
```

## Collections

Each collection is an independent ChromaDB database stored at the path you specify. You can have one per project, per paper, or per topic — whatever makes sense for your workflow.

A lightweight registry (`collections_registry.db`, lives next to `rag_manager.py`) tracks all collections by name so you never have to remember paths.

## Supported file types

| Extension | Extraction method |
|-----------|------------------|
| `.pdf` | PyMuPDF (selectable text) |
| `.docx` | python-docx |
| `.txt`, `.md`, `.tex`, `.csv` | UTF-8 read |

For scanned PDFs with no selectable text, run OCR first (e.g. via doc-extractor or Mistral OCR) and use `add_text` with the output.

## Install

From the repo root:

```bash
./install.sh
```

Or manually:

```bash
claude mcp add rag --scope user -- uv run \
  --project /path/to/mcps/rag-management \
  fastmcp run /path/to/mcps/rag-management/rag_manager.py
```
