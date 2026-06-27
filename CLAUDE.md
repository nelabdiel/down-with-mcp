# MCP Servers

This repo contains MCP servers for Claude Code. When you open this project,
check if the servers below are registered. If any are missing from `claude mcp list`,
run `./install.sh` to register them all, then verify with `/mcp`.

## Registered servers

### doc-extractor

Extracts text, OCR, tables, and image counts from PDF and DOCX files.

Tools available:
- `extract_from_file` — full extraction
- `extract_text_only` — fast text-only path
- `extract_tables_only` — structured table data

System requirement: Tesseract must be installed (`brew install tesseract` on macOS).

### arxiv-explorer

Search and fetch arXiv papers. Claude handles summarization and synthesis.

Tools available:
- `recent_papers` — papers by topic within a time window
- `search_papers` — relevance-sorted search, supports field prefixes (ti:, au:, abs:, cat:)
- `fetch_paper` — full metadata and abstract by arXiv ID or URL

Prompts available:
- `lit_review` — structured literature review for a topic
- `related_work` — find and synthesize related work for a specific claim
