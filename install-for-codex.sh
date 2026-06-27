#!/bin/bash
# Registers all MCP servers in this repo into Codex user config.
# Run once after cloning: ./install-codex.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"

echo "Installing MCP servers from $REPO into Codex"
echo ""

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: '$1' is not installed or not on PATH."
    exit 1
  fi
}

require_file() {
  if [ ! -f "$1" ]; then
    echo "Error: expected file not found: $1"
    exit 1
  fi
}

register_stdio_server() {
  local name="$1"
  local project_dir="$2"
  local server_file="$3"

  echo "── $name ─────────────────────────────────────────────────────────"

  require_file "$server_file"

  if codex mcp remove "$name" >/dev/null 2>&1; then
    echo "  removed existing $name"
  fi

  codex mcp add "$name" -- uv run \
    --project "$project_dir" \
    fastmcp run "$server_file"

  echo "  $name registered"
  echo ""
}

need_cmd codex
need_cmd uv

# ── doc-extractor ──────────────────────────────────────────────────────────
register_stdio_server \
  "doc-extractor" \
  "$REPO/doc-extractor" \
  "$REPO/doc-extractor/extract_from_docs.py"

# ── arxiv-explorer ─────────────────────────────────────────────────────────
register_stdio_server \
  "arxiv-explorer" \
  "$REPO/arxiv-explorer" \
  "$REPO/arxiv-explorer/arxiv_finder.py"

# ── rag-management ─────────────────────────────────────────────────────────
register_stdio_server \
  "rag-management" \
  "$REPO/rag-management" \
  "$REPO/rag-management/rag_manager.py"

echo "Done."
echo ""
echo "Registered Codex MCP servers:"
codex mcp list
echo ""
echo "Open Codex and run /mcp to verify active servers."
