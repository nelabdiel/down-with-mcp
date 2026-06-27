"""
arXiv MCP server for Claude Code.

Tools:
  - recent_papers   : papers by topic within a time window
  - search_papers   : papers by relevance for a query
  - fetch_paper     : single paper by arXiv ID or URL

Resources:
  - arxiv://recent/{topic}/{hours_back}
  - arxiv://paper/{arxiv_id}

Install into Claude Code (from repo root):
  ./install.sh

Or manually:
  claude mcp add arxiv --scope user -- uv run \
    --project /path/to/mcps/arxiv \
    fastmcp run /path/to/mcps/arxiv/arxiv_mcp.py
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import arxiv
from fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

mcp = FastMCP("arxiv")


# ── helpers ──────────────────────────────────────────────────────────────────

def _utc_since(hours_back: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_back)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _serialize(r: arxiv.Result, include_abstract: bool = True) -> dict:
    item = {
        "id": r.entry_id.rsplit("/", 1)[-1],       # e.g. 2501.01234v2
        "arxiv_url": r.entry_id,
        "pdf_url": r.pdf_url,
        "title": _clean(r.title),
        "authors": [a.name for a in r.authors],
        "primary_category": r.primary_category,
        "categories": r.categories,
        "published_utc": r.published.isoformat() if r.published else None,
        "updated_utc": r.updated.isoformat() if r.updated else None,
        "resource_uri": f"arxiv://paper/{r.entry_id.rsplit('/', 1)[-1]}",
    }
    if include_abstract:
        item["abstract"] = _clean(r.summary or "")
    return item


def _client() -> arxiv.Client:
    return arxiv.Client(delay_seconds=3.0, num_retries=2)


# ── tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def recent_papers(
    topic: str = "quantum",
    max_results: int = 10,
    hours_back: int = 24,
    include_abstracts: bool = True,
    abstract_max_chars: int = 1200,
) -> str:
    """
    Return arXiv papers matching `topic` submitted within the last `hours_back` hours,
    sorted by submission date descending.

    Args:
        topic: Search term or phrase (searched across all fields).
        max_results: Maximum number of papers to return.
        hours_back: How far back to look (default 24 hours).
        include_abstracts: Whether to include abstracts in results.
        abstract_max_chars: Truncate abstracts to this length (0 = no limit).
    """
    since = _utc_since(hours_back)
    search = arxiv.Search(
        query=f'all:"{topic}"',
        max_results=200,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    items = []
    for r in _client().results(search):
        if r.published and r.published >= since:
            item = _serialize(r, include_abstract=include_abstracts)
            if include_abstracts and abstract_max_chars:
                abstract = item.get("abstract", "")
                if len(abstract) > abstract_max_chars:
                    item["abstract"] = abstract[:abstract_max_chars] + "…"
            items.append(item)
            if len(items) >= max_results:
                break
        else:
            break

    return json.dumps({
        "topic": topic,
        "since_utc": since.isoformat(),
        "hours_back": hours_back,
        "count": len(items),
        "results": items,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def search_papers(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
    include_abstracts: bool = True,
) -> str:
    """
    Search arXiv by relevance for a query. Use this for finding related work
    on a specific topic, claim, or paper you're writing about.

    Args:
        query: Free-text search query. Supports arXiv field prefixes:
               ti: (title), au: (author), abs: (abstract), cat: (category).
               Examples: "ti:diffusion models", "au:Hinton", "abs:transformer pruning".
        max_results: Maximum number of papers to return (max 50).
        sort_by: One of "relevance" (default), "lastUpdatedDate", "submittedDate".
        include_abstracts: Whether to include abstracts in results.
    """
    sort_map = {
        "relevance": arxiv.SortCriterion.Relevance,
        "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
        "submittedDate": arxiv.SortCriterion.SubmittedDate,
    }
    criterion = sort_map.get(sort_by, arxiv.SortCriterion.Relevance)

    search = arxiv.Search(
        query=query,
        max_results=min(max_results, 50),
        sort_by=criterion,
        sort_order=arxiv.SortOrder.Descending,
    )

    items = [_serialize(r, include_abstract=include_abstracts)
             for r in _client().results(search)]

    return json.dumps({
        "query": query,
        "sort_by": sort_by,
        "count": len(items),
        "results": items,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def fetch_paper(arxiv_id: str) -> str:
    """
    Fetch full metadata and abstract for a single arXiv paper by ID or URL.
    Use this to verify a citation, get a full abstract, or retrieve metadata
    for a paper you already know about.

    Args:
        arxiv_id: arXiv ID (e.g. "2501.01234", "2501.01234v2") or full
                  arXiv URL (e.g. "https://arxiv.org/abs/2501.01234").
    """
    # Normalize: strip URL to bare ID
    arxiv_id = arxiv_id.strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                   "https://arxiv.org/pdf/", "http://arxiv.org/pdf/"):
        if arxiv_id.startswith(prefix):
            arxiv_id = arxiv_id[len(prefix):].rstrip("/").replace(".pdf", "")
            break

    search = arxiv.Search(id_list=[arxiv_id])
    for r in _client().results(search):
        return json.dumps(_serialize(r, include_abstract=True),
                          ensure_ascii=False, indent=2)

    return json.dumps({"error": f"Paper not found: {arxiv_id}"})


# ── resources ────────────────────────────────────────────────────────────────

@mcp.resource("arxiv://recent/{topic}/{hours_back}", mime_type="application/json")
def recent_resource(topic: str = "quantum", hours_back: int = 24) -> str:
    """Recent papers as a resource URI — clients can attach this directly."""
    return recent_papers(topic=topic, hours_back=int(hours_back))


@mcp.resource("arxiv://paper/{arxiv_id}", mime_type="application/json")
def paper_resource(arxiv_id: str) -> str:
    """Single paper as a resource URI."""
    return fetch_paper(arxiv_id)


# ── prompts ──────────────────────────────────────────────────────────────────

@mcp.prompt()
def lit_review(
    topic: str = "quantum",
    hours_back: int = 24,
    audience: str = "grad students",
    bullets: int = 7,
) -> str:
    """
    Reusable literature review prompt. Points the host at the recent papers
    resource for the given topic and asks for a structured summary.
    """
    return (
        f"You are preparing a brief literature review for {audience}.\n"
        f"Fetch and read: arxiv://recent/{topic}/{hours_back}\n\n"
        f"Write {bullets} concise bullets covering themes, methods, and key results.\n"
        "Cite each paper inline as [First Author et al., YYYY] and end with a "
        "1–2 sentence outlook on open problems or directions."
    )


@mcp.prompt()
def related_work(
    claim: str,
    max_results: int = 10,
) -> str:
    """
    Given a specific claim or contribution, find and synthesize related work.
    """
    return (
        f"I am writing a paper and need to situate the following claim in the literature:\n\n"
        f'"{claim}"\n\n'
        f"Use the search_papers tool to find the {max_results} most relevant arXiv papers. "
        "Then write a 2–3 paragraph related work synthesis that:\n"
        "1) Groups papers by theme or method\n"
        "2) Highlights how each group relates to the claim\n"
        "3) Identifies the gap this claim fills\n"
        "Cite inline as [First Author et al., YYYY]."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
