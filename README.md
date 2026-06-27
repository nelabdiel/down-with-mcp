# mcps

A personal collection of MCP servers for [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview). Each server lives in its own folder with its own dependencies and README.

## Servers

| Server | Description | Sources |
|--------|-------------|---------|
| [doc-extractor](./doc-extractor/) | Extract text, tables, OCR, and images | PDF, DOCX |
| [arxiv-explorer](./arxiv-explorer/) | Search and fetch arXiv papers | arXiv API |
| [rag-management](./rag-management/) | Local semantic search over documents and text | ChromaDB + Ollama |
## Setup

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) installed
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed
- Any system dependencies listed in individual server READMEs (e.g. Tesseract for doc-extractor)

### Install all servers

```bash
git clone https://github.com/nelabdiel/down-with-mcp.git
cd down-with-mcp
chmod +x install.sh
./install.sh
```

That's it. `install.sh` resolves absolute paths automatically so it works wherever you clone the repo. All servers are registered at user scope, meaning they're available in every Claude Code session on your machine — not just this project.

### Verify

```bash
claude mcp list
```

Or open Claude Code and run `/mcp` to see connected servers and their tools.

---

## How to add a new MCP server

### 1. Create a folder

```
down-with-mcp/
└── your-server/
    ├── server.py          ← your FastMCP server
    ├── pyproject.toml     ← uv dependencies
    └── README.md          ← usage and tool descriptions
```

### 2. Write the server

Use [FastMCP](https://gofastmcp.com) — it's the cleanest way to expose Python functions as MCP tools:

```python
from fastmcp import FastMCP

mcp = FastMCP(name="your-server")

@mcp.tool()
def your_tool(input: str) -> str:
    """
    Describe what this tool does and when Claude should use it.
    Good docstrings = Claude picks the right tool automatically.

    Args:
        input: Describe the parameter.
    """
    return do_something(input)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 3. Add a pyproject.toml

```toml
[project]
name = "your-server"
version = "0.1.0"
description = "What it does"
requires-python = ">=3.10"
dependencies = [
    "fastmcp>=2.3.3",
    # your deps here
]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = ["server"]   # filename without .py

[dependency-groups]
dev = ["ruff>=0.4.0"]
```

> **Note:** use `setuptools` with `py-modules` rather than `hatchling` — hatchling expects a package directory structure and will fail on single-file servers. Also make sure `name` in `[project]` doesn't match any of your dependencies or uv will error with a self-dependency conflict.

Run `uv sync` inside the folder to generate a lockfile. This makes Claude Code startup fast — uv reads the lockfile instead of resolving deps fresh every time.

### 4. Register it in install.sh

Add a block to `install.sh` following the existing pattern:

```bash
# ── your-server ────────────────────────────────────────────────────────────
claude mcp remove your-server 2>/dev/null && echo "  removed existing your-server"

claude mcp add your-server --scope user -- uv run \
  --project "$REPO/your-server" \
  fastmcp run "$REPO/your-server/server.py"

echo "your-server registered"
```

### 5. Update this README

Add a row to the servers table at the top.

### 6. Update CLAUDE.md

Add a section describing the new server's tools so Claude Code knows what's available when it reads the project context.

---

## Project structure

```
down-with-mcp/
├── README.md              ← you are here
├── CLAUDE.md              ← auto-read by Claude Code; describes all available tools
├── install.sh             ← registers all servers at user scope
│
├── doc-extractor/
│   ├── extract_from_docs.py
│   ├── pyproject.toml
│   └── README.md
│
└── arxiv-explorer/
|   ├── arxiv_finder.py
|   ├── pyproject.toml
|   └── README.md
|
└── -managementrag/
    ├── raanagermcp.py
    ├── pyproject.toml
    └── README.md
```


---

Contributor(s): @nelabdiel
