# doc-extractor MCP

Extracts text, tables, images, and OCR content from PDF and DOCX files. Built for Claude Code using FastMCP over stdio transport.

## Tools

| Tool | Description |
|------|-------------|
| `extract_from_file` | Full extraction — text, OCR, tables, image count |
| `extract_text_only` | Fast path, selectable text only, no OCR or tables |
| `extract_tables_only` | Tables only, returned as structured JSON |

## Requirements

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Tesseract binary (for OCR)

### Install Tesseract

```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt install tesseract-ocr
```

## Install into Claude Code

From the repo root, run:

```bash
./install.sh
```

Or manually:

```bash
claude mcp add doc-extractor --scope user -- uv run \
  --project /path/to/mcps/doc-extractor \
  fastmcp run /path/to/mcps/doc-extractor/extract_from_docs.py
```

Verify it's connected:

```bash
claude mcp list
# then inside a Claude Code session:
# /mcp
```

## Usage

Once registered, just ask Claude Code naturally:

```
Extract the text from ~/Documents/report.pdf
Pull all tables from /path/to/contract.docx
What does the OCR text say in ~/scans/invoice.pdf?
```

Claude will automatically call the right tool.

## Dependencies

Managed via `pyproject.toml` with uv. To install locally for development:

```bash
cd doc-extractor
uv sync
```
