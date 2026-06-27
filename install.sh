#!/bin/bash
# Registers all MCP servers in this repo into Claude Code (user scope).
# Run once after cloning: ./install.sh

set -e

REPO="$(cd "$(dirname "$0")" && pwd)"

echo "Installing MCP servers from $REPO"

# ── doc-extractor ──────────────────────────────────────────────────────────
claude mcp remove doc-extractor 2>/dev/null && echo "  removed existing doc-extractor"

claude mcp add doc-extractor --scope user -- uv run \
  --project "$REPO/doc-extractor" \
  fastmcp run "$REPO/doc-extractor/extract_from_docs.py"

echo "doc-extractor registered"

# ── add future servers here ────────────────────────────────────────────────
# claude mcp remove another-mcp 2>/dev/null
# claude mcp add another-mcp --scope user -- uv run \
#   --project "$REPO/another-mcp" \
#   fastmcp run "$REPO/another-mcp/server.py"
# echo "another-mcp registered"

echo ""
echo "Done. Run 'claude mcp list' to verify, or open Claude Code and run /mcp."
