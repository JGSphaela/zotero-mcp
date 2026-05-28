# Zotero MCP Research Fork

This repository is a research-workflow fork of
[kujenga/zotero-mcp](https://github.com/kujenga/zotero-mcp). The original
project is a compact Model Context Protocol (MCP) server for Zotero with three
core read-only tools: search, metadata lookup, and full-text lookup.

This fork keeps that small, local-first design, but adds tools that make Zotero
more useful as an academic paper knowledge base for LLMs:

- API health checks and setup diagnostics
- DOI-first lookup for citation grounding
- Collection, tag, child-note, and attachment navigation
- Friendlier error handling when Zotero is closed or the local API is disabled
- Tests against current `mcp`, `pyzotero`, and Python 3.13

The current fork is still intentionally read-only. Importing PDFs, editing
metadata, creating collections, and bulk library cleanup should be added behind
explicit opt-in write controls.

## Differences from upstream

| Area | Upstream `kujenga/zotero-mcp` | This fork |
| --- | --- | --- |
| Scope | Minimal Zotero read access | Research knowledge-management workflow |
| Tools | 3 tools | 9 tools |
| Diagnostics | Basic exceptions | `zotero_healthcheck` and readable API errors |
| Citation grounding | Search only | DOI-normalized lookup |
| Library navigation | Items only | Collections, collection items, tags, child notes, attachments |
| Metadata | Upstream package metadata | Fork repository and issue links |

## Tools

- `zotero_healthcheck`: Check Zotero API configuration and reachability.
- `zotero_search_items`: Search items by title/creator/year or full text.
- `zotero_find_item_by_doi`: Find exact DOI matches, accepting raw DOI,
  `doi:...`, or `https://doi.org/...`.
- `zotero_item_metadata`: Get detailed metadata for a Zotero item key.
- `zotero_item_fulltext`: Get indexed full text from the best available child
  attachment.
- `zotero_item_children`: List child notes and attachments for an item.
- `zotero_list_collections`: List collections with keys and item counts.
- `zotero_collection_items`: List items in a collection.
- `zotero_list_tags`: List library tags.

## Recommended setup: local Zotero API

This mode keeps your Zotero API traffic on your Mac and does not require a
Zotero web API key.

1. Open Zotero.
2. Open Zotero Settings.
3. Go to Advanced.
4. Enable "Allow other applications on this computer to communicate with Zotero".
5. Keep Zotero running while using the MCP server.

The local API is available at:

```text
http://localhost:23119/api/
```

For remote/cloud use, the Zotero Web API is still supported through
`ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`, and `ZOTERO_LIBRARY_TYPE`.

## Configuration

Environment variables:

- `ZOTERO_LOCAL=true`: Use the local Zotero desktop API.
- `ZOTERO_API_KEY`: Zotero Web API key. Not required for local mode.
- `ZOTERO_LIBRARY_ID`: Zotero user or group library ID. In local mode this can
  be blank; the server uses `0` for the current local user.
- `ZOTERO_LIBRARY_TYPE`: `user` or `group`. Defaults to `user`.

Example MCP configuration for a local clone:

```json
{
  "mcpServers": {
    "zotero": {
      "command": "/path/to/zotero-mcp/.venv/bin/zotero-mcp",
      "env": {
        "ZOTERO_LOCAL": "true",
        "ZOTERO_API_KEY": "",
        "ZOTERO_LIBRARY_ID": ""
      }
    }
  }
}
```

## Development

Clone the fork:

```bash
git clone https://github.com/JGSphaela/zotero-mcp.git
cd zotero-mcp
```

Install dependencies with `uv`:

```bash
uv sync
```

Or use a standard virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest ruff
```

Run tests:

```bash
uv run pytest
uv run ruff check .
```

With a virtual environment:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Start the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run zotero-mcp
```

## Roadmap

Near-term:

- Structured JSON outputs in addition to human-readable Markdown.
- Citation export tools for BibTeX and CSL JSON.
- Better note and annotation extraction.
- Safer full-text limits and chunking for long PDFs.

Later, behind explicit write opt-in:

- Dry-run PDF import planning.
- DOI/title metadata repair.
- Duplicate detection.
- Collection assignment and tag cleanup.
- Batch import from messy local PDF folders.

## Upstream credit

This fork builds on [kujenga/zotero-mcp](https://github.com/kujenga/zotero-mcp)
by Aaron Taylor. The original project established the clean FastMCP/Pyzotero
foundation used here.

## Relevant documentation

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Pyzotero](https://pyzotero.readthedocs.io/en/latest/)
- [Zotero Web API](https://www.zotero.org/support/dev/web_api/v3/start)
