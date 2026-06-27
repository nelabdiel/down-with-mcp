# arxiv MCP

Search and fetch arXiv papers from Claude Code. Claude handles summarization and synthesis — this server handles retrieval.

## Tools

| Tool | Description |
|------|-------------|
| `recent_papers` | Papers matching a topic submitted within a time window, sorted by date |
| `search_papers` | Papers sorted by relevance for a query — use for finding related work |
| `fetch_paper` | Full metadata and abstract for a single paper by ID or URL |

## Resources

| URI | Description |
|-----|-------------|
| `arxiv://recent/{topic}/{hours_back}` | Recent papers as an attachable resource |
| `arxiv://paper/{arxiv_id}` | Single paper as an attachable resource |

## Prompts

| Prompt | Description |
|--------|-------------|
| `lit_review` | Structured literature review for a topic over a time window |
| `related_work` | Find and synthesize related work for a specific claim |

## Usage

Once registered, use naturally in Claude Code:

```
What are the most relevant papers on Quantum Computing from the last 48 hours?
Find related work for: "WRT invariants of torus bundles"
Fetch the abstract for arxiv.org/abs/2512.19028
```

### Search query syntax

`search_papers` supports arXiv field prefixes:

```
ti:quantum algorithms                # title search
au:Nelson                            # author search
abs:invariants of torus bundles      # abstract search
cat:math.GT                          # category search
ti:ortiz AND cat:math.QA             # combined
```

## Install

From the repo root:

```bash
./install.sh
```

Or manually:

```bash
claude mcp add arxiv --scope user -- uv run \
  --project /path/to/mcps/arxiv \
  fastmcp run /path/to/mcps/arxiv/arxiv_mcp.py
```
