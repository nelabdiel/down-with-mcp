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
| `search_multi_query` | Multi-query search: searches the original query plus 2–4 rephrasings, then deduplicates results |
| `search_hyde` | HyDE search: embeds a hypothetical answer/document instead of the raw query for abstract or conceptual questions |
| `search_mmr` | MMR (Maximal Marginal Retrieval) search: diversity-aware retrieval that avoids returning near-duplicate chunks |

## Retrieval modes

The default `search` tool performs baseline semantic search and is best for exact entities, dates, identifiers, or straightforward factual lookup.

For broader or more ambiguous questions, the server also provides advanced retrieval tools:

- `search_multi_query`: use when wording may vary across documents. Claude generates 2–4 alternate phrasings, searches all of them, deduplicates the results, and returns the best union of chunks.
- `search_hyde`: use for abstract or conceptual questions where the query language may differ from the document language. Claude writes a short hypothetical answer, embeds that text, and searches for real chunks similar to it.
- `search_mmr`: use when you want broader coverage across a corpus or document. It balances relevance with diversity so the results are not all from the same repeated passage or section.

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
add_document("quantum-algorithms", "./ref1.pdf")
add_document("quantum-algorithms", "./draft.md")

# Index raw text (e.g. Mistral OCR output, web pages)
add_text("quantum-algorithms", "<text content>", source="mistral-ocr:scan.pdf")

# Check what's indexed
collection_status("quantum-algorithms")
list_collections()

# Search
search("quantum-algorithms", "coefficient counting complexity for non-commutative torus multiplication")



# Multi-query search for terminology variation
search_multi_query(
  "quantum-algorithms",
  "query complexity of quantum walk algorithms",
  query_variants=[
    "quantum walk speedups and oracle query complexity",
    "algorithmic complexity bounds for discrete-time quantum walks",
    "search algorithms based on quantum walks"
  ]
)

# HyDE search for conceptual questions
search_hyde(
"quantum-algorithms",
  "How does amplitude amplification generalize Grover search?",
  hypothesis="Amplitude amplification generalizes Grover search by increasing the success probability of a quantum procedure whose good outcomes can be recognized. It alternates reflections around the initial state and the marked subspace, rotating amplitude toward desired outcomes. This gives a quadratic improvement in the number of calls to the underlying procedure compared with classical repetition."
)

# MMR (Maximal Marginal Relevance) search for diverse coverage
search_mmr(
  "quantum-algorithms",
  "quantum algorithms for hidden subgroup problems",
  n_results=8,
  n_candidates=30,
  lambda_mmr=0.7
)
```

Then just ask Claude naturally:

```
What do my notes say about amplitude amplification?
Summarize what I've indexed about WRT invariants.
Find anything in my notes about torus bundles.
Find the sections that discuss quantum walks and query complexity.
Compare the treatment of phase estimation across my references.
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
claude mcp add rag-management --scope user -- uv run \
  --project /path/to/mcps/rag-management \
  fastmcp run /path/to/mcps/rag-management/rag_manager.py
```
