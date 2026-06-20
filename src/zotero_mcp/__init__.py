import os
import re
from math import ceil
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from zotero_mcp.client import get_attachment_details, get_zotero_client

# Create an MCP server
mcp = FastMCP("Zotero")

DEFAULT_CHUNK_CHARS = 8000
DEFAULT_CHUNK_OVERLAP = 500
MAX_CHUNK_CHARS = 25000
MAX_CONTEXT_CHARS = 2500


def strip_note_html(note: str) -> str:
    """Remove the small subset of Zotero note HTML that is useful in previews."""
    note = note.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n")
    note = note.replace("<strong>", "**").replace("</strong>", "**")
    return note.replace("<em>", "*").replace("</em>", "*")


def normalize_doi(doi: str) -> str:
    """Normalize DOI strings for comparison."""
    normalized = doi.strip()
    normalized = re.sub(
        r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.IGNORECASE
    )
    normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.IGNORECASE)
    return normalized.strip().rstrip(".").lower()


def clamp_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    """Clamp optional integer MCP inputs to predictable bounds."""
    if value is None:
        return default
    return max(minimum, min(value, maximum))


def normalize_whitespace(text: str) -> str:
    """Collapse indexed full-text whitespace for compact snippets."""
    return re.sub(r"\s+", " ", text).strip()


def chunk_count(text_length: int, chunk_chars: int, overlap_chars: int) -> int:
    """Return the number of overlapping chunks for text of a given length."""
    if text_length <= 0:
        return 0
    step = max(1, chunk_chars - overlap_chars)
    return max(1, ceil(max(0, text_length - chunk_chars) / step) + 1)


def chunk_bounds(
    text_length: int, chunk_index: int, chunk_chars: int, overlap_chars: int
) -> tuple[int, int]:
    """Return 0-based character bounds for a 1-based chunk index."""
    step = max(1, chunk_chars - overlap_chars)
    start = max(0, (chunk_index - 1) * step)
    end = min(text_length, start + chunk_chars)
    return start, end


def chunk_index_for_offset(
    offset: int,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """Return the 1-based overlapping chunk containing an offset."""
    step = max(1, chunk_chars - overlap_chars)
    return max(1, offset // step + 1)


def make_snippet(content: str, center: int, context_chars: int) -> str:
    """Create a compact text snippet around a character offset."""
    start = max(0, center - context_chars)
    end = min(len(content), center + context_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{normalize_whitespace(content[start:end])}{suffix}"


def tokenize_query(query: str) -> list[str]:
    """Tokenize a topic query for fallback full-text matching."""
    return [token.lower() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", query)]


def get_indexed_attachment_text(
    zot: Any, item_key: str
) -> tuple[dict[str, Any] | None, Any | None, str | None, str | None]:
    """Fetch an item, its best attachment, and Zotero's indexed full text."""
    item: Any = zot.item(item_key)
    if not item:
        return None, None, None, f"No item found with key: {item_key}"

    attachment = get_attachment_details(zot, item)
    if attachment is None:
        return (
            item,
            None,
            None,
            "No suitable attachment found for full text extraction. This item may not have any attached files or they may not be in a supported format.",
        )

    full_text_data: Any = zot.fulltext_item(attachment.key)
    if not full_text_data or "content" not in full_text_data:
        return (
            item,
            attachment,
            None,
            "Attachment is available but indexed text is not available. The document may be scanned as images, not indexed by Zotero yet, or restricted.",
        )

    return item, attachment, full_text_data["content"], None


def iter_lines_with_offsets(content: str) -> list[tuple[int, str]]:
    """Return stripped non-empty lines with their 0-based character offsets."""
    lines = []
    offset = 0
    for raw_line in content.splitlines(keepends=True):
        stripped = raw_line.strip()
        if stripped:
            line_start = offset + raw_line.find(stripped)
            lines.append((line_start, normalize_whitespace(stripped)))
        offset += len(raw_line)
    return lines


def heading_score(line: str) -> int:
    """Score whether an indexed-text line looks like a structural heading."""
    if len(line) < 3 or len(line) > 140:
        return 0
    if re.fullmatch(r"\d+", line):
        return 0

    score = 0
    if re.match(r"^(chapter|section|appendix|part)\b", line, flags=re.IGNORECASE):
        score += 5
    if re.match(r"^\d+(?:\.\d+){0,4}\s+\S", line):
        score += 4
    if re.search(r"\.{3,}\s*\d+$", line):
        score += 3
    alpha_chars = [char for char in line if char.isalpha()]
    if (
        alpha_chars
        and sum(char.isupper() for char in alpha_chars) / len(alpha_chars) > 0.75
    ):
        score += 2
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", line)
    if 1 <= len(words) <= 12 and line[:1].isupper():
        score += 1

    return score


def extract_headings(
    content: str, limit: int = 80, dedupe: bool = True
) -> list[dict[str, Any]]:
    """Extract likely headings or table-of-contents entries from indexed text."""
    headings = []
    seen = set()
    for offset, line in iter_lines_with_offsets(content):
        score = heading_score(line)
        if score < 3:
            continue
        normalized = line.lower()
        if dedupe and normalized in seen:
            continue
        seen.add(normalized)
        headings.append(
            {
                "title": line,
                "offset": offset,
                "score": score,
                "chunk": chunk_index_for_offset(offset),
            }
        )
        if len(headings) >= limit:
            break
    return headings


def section_end_offset(
    headings: list[dict[str, Any]], start: int, content_length: int
) -> int:
    """Find the next heading offset after a section start."""
    for heading in headings:
        if heading["offset"] > start:
            return heading["offset"]
    return content_length


def find_heading_matches(
    headings: list[dict[str, Any]], section_title: str
) -> list[dict[str, Any]]:
    """Find likely heading matches for a requested section title."""
    needle = normalize_whitespace(section_title).lower()
    if not needle:
        return []
    needle_tokens = set(tokenize_query(needle))
    matches = []
    for heading in headings:
        title = heading["title"]
        haystack = title.lower()
        haystack_tokens = set(tokenize_query(haystack))
        if needle in haystack or haystack in needle:
            matches.append(heading)
        elif needle_tokens and needle_tokens.issubset(haystack_tokens):
            matches.append(heading)
    return matches


def creator_summary(data: dict[str, Any], limit: int = 3) -> str:
    """Format a compact creator list."""
    creators = []
    for creator in data.get("creators", [])[:limit]:
        if "firstName" in creator and "lastName" in creator:
            creators.append(f"{creator['lastName']}, {creator['firstName']}")
        elif "name" in creator:
            creators.append(creator["name"])

    if len(data.get("creators", [])) > limit:
        creators.append("et al.")

    return "; ".join(creators) if creators else "No authors"


def format_item_summary(item: dict[str, Any], index: int | None = None) -> str:
    """Format a compact Zotero item summary for result lists."""
    data = item.get("data", {})
    item_key = item.get("key") or data.get("key", "")
    item_type = data.get("itemType", "unknown")
    title = data.get("title", "Untitled")

    if item_type == "note":
        note_content = strip_note_html(data.get("note", ""))
        first_line = note_content.strip().split("\n", maxsplit=1)[0]
        title = first_line[:80] if first_line else "Note"

    prefix = f"{index}. " if index is not None else ""
    lines = [
        f"## {prefix}{title}",
        f"**Type**: {item_type} | **Key**: `{item_key}`",
    ]

    if date := data.get("date"):
        lines.append(f"**Date**: {date}")

    if item_type != "note":
        lines.append(f"**Authors**: {creator_summary(data)}")

    if doi := data.get("DOI"):
        lines.append(f"**DOI**: {doi}")

    if url := data.get("url"):
        lines.append(f"**URL**: {url}")

    if parent := data.get("parentItem"):
        lines.append(f"**Parent Item**: `{parent}`")

    if content_type := data.get("contentType"):
        lines.append(f"**Content Type**: {content_type}")

    return "\n".join(lines)


def format_item(item: dict[str, Any]) -> str:
    """Format a Zotero item's metadata as a readable string optimized for LLM consumption"""
    data = item["data"]
    item_key = item["key"]
    item_type = data.get("itemType", "unknown")

    # Special handling for notes
    if item_type == "note":
        # Get note content
        note_content = strip_note_html(data.get("note", ""))

        # Format note with clear sections
        formatted = [
            "## 📝 Note",
            f"Item Key: `{item_key}`",
        ]

        # Add parent item reference if available
        if parent_item := data.get("parentItem"):
            formatted.append(f"Parent Item: `{parent_item}`")

        # Add date if available
        if date := data.get("dateModified"):
            formatted.append(f"Last Modified: {date}")

        # Add tags with formatting for better visibility
        if tags := data.get("tags"):
            tag_list = [f"`{tag['tag']}`" for tag in tags]
            formatted.append(f"\n### Tags\n{', '.join(tag_list)}")

        # Add note content
        formatted.append(f"\n### Note Content\n{note_content}")

        return "\n".join(formatted)

    # Regular item handling (non-notes)

    # Basic metadata with key for easy reference
    formatted = [
        f"## {data.get('title', 'Untitled')}",
        f"Item Key: `{item_key}`",
        f"Type: {item_type}",
        f"Date: {data.get('date', 'No date')}",
    ]

    # Creators with role differentiation
    creators_by_role = {}
    for creator in data.get("creators", []):
        role = creator.get("creatorType", "contributor")
        name = ""
        if "firstName" in creator and "lastName" in creator:
            name = f"{creator['lastName']}, {creator['firstName']}"
        elif "name" in creator:
            name = creator["name"]

        if name:
            if role not in creators_by_role:
                creators_by_role[role] = []
            creators_by_role[role].append(name)

    for role, names in creators_by_role.items():
        role_display = role.capitalize() + ("s" if len(names) > 1 else "")
        formatted.append(f"{role_display}: {'; '.join(names)}")

    # Publication details
    if publication := data.get("publicationTitle"):
        formatted.append(f"Publication: {publication}")
    if volume := data.get("volume"):
        volume_info = f"Volume: {volume}"
        if issue := data.get("issue"):
            volume_info += f", Issue: {issue}"
        if pages := data.get("pages"):
            volume_info += f", Pages: {pages}"
        formatted.append(volume_info)

    # Abstract with clear section header
    if abstract := data.get("abstractNote"):
        formatted.append(f"\n### Abstract\n{abstract}")

    # Tags with formatting for better visibility
    if tags := data.get("tags"):
        tag_list = [f"`{tag['tag']}`" for tag in tags]
        formatted.append(f"\n### Tags\n{', '.join(tag_list)}")

    # URLs, DOIs, and identifiers grouped together
    identifiers = []
    if url := data.get("url"):
        identifiers.append(f"URL: {url}")
    if doi := data.get("DOI"):
        identifiers.append(f"DOI: {doi}")
    if isbn := data.get("ISBN"):
        identifiers.append(f"ISBN: {isbn}")
    if issn := data.get("ISSN"):
        identifiers.append(f"ISSN: {issn}")

    if identifiers:
        formatted.append("\n### Identifiers\n" + "\n".join(identifiers))

    # Notes and attachments
    if notes := item.get("meta", {}).get("numChildren", 0):
        formatted.append(
            f"\n### Additional Information\nNumber of notes/attachments: {notes}"
        )

    return "\n".join(formatted)


@mcp.tool(
    name="zotero_healthcheck",
    description="Check Zotero MCP configuration and whether the Zotero API is reachable.",
)
def healthcheck() -> str:
    """Check configuration and basic Zotero connectivity."""
    local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]
    library_id = os.getenv("ZOTERO_LIBRARY_ID") or ("0" if local else "(unset)")
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user")

    lines = [
        "## Zotero MCP Health",
        f"Mode: {'local Zotero API' if local else 'Zotero Web API'}",
        f"Library: {library_type}/{library_id}",
    ]

    try:
        zot = get_zotero_client()
        sample_items: Any = zot.items(limit=1)
    except Exception as e:
        lines.extend(
            [
                "Status: ERROR",
                f"Error: {str(e)}",
            ]
        )
        if local:
            lines.append(
                "Hint: Open Zotero and enable Settings > Advanced > Allow other applications on this computer to communicate with Zotero."
            )
        else:
            lines.append(
                "Hint: Set ZOTERO_LIBRARY_ID and ZOTERO_API_KEY, or use ZOTERO_LOCAL=true with the Zotero desktop app."
            )
        return "\n".join(lines)

    lines.extend(
        [
            "Status: OK",
            f"Endpoint: {getattr(zot, 'endpoint', 'unknown')}",
        ]
    )
    if sample_items:
        sample = sample_items[0]
        data = sample.get("data", {})
        lines.append(
            f"Sample Item: {data.get('title', 'Untitled')} (`{sample['key']}`)"
        )
    return "\n".join(lines)


@mcp.tool(
    name="zotero_item_metadata",
    description="Get metadata information about a specific Zotero item, given the item key.",
)
def get_item_metadata(item_key: str) -> str:
    """Get metadata information about a specific Zotero item"""
    zot = get_zotero_client()

    try:
        item: Any = zot.item(item_key)
        if not item:
            return f"No item found with key: {item_key}"
        return format_item(item)
    except Exception as e:
        return f"Error retrieving item metadata: {str(e)}"


@mcp.tool(
    name="zotero_item_fulltext",
    description="Get the full text content of a Zotero item, given the item key of a parent item or specific attachment.",
)
def get_item_fulltext(item_key: str) -> str:
    """Get the full text content of a specific Zotero item"""
    zot = get_zotero_client()

    try:
        item: Any = zot.item(item_key)
        if not item:
            return f"No item found with key: {item_key}"

        # Fetch full-text content
        attachment = get_attachment_details(zot, item)

        # Prepare header with metadata
        header = format_item(item)

        # Add attachment information
        if attachment is not None:
            attachment_info = f"\n## Attachment Information\n- **Key**: `{attachment.key}`\n- **Type**: {attachment.content_type}"

            # Get the full text
            full_text_data: Any = zot.fulltext_item(attachment.key)
            if full_text_data and "content" in full_text_data:
                item_text = full_text_data["content"]
                # Calculate approximate word count
                word_count = len(item_text.split())
                attachment_info += f"\n- **Word Count**: ~{word_count}"

                # Format the content with markdown for structure
                full_text = f"\n\n## Document Content\n\n{item_text}"
            else:
                # Clear error message when text extraction isn't possible
                full_text = "\n\n## Document Content\n\n[⚠️ Attachment is available but text extraction is not possible. The document may be scanned as images or have other restrictions that prevent text extraction.]"
        else:
            attachment_info = "\n\n## Attachment Information\n[❌ No suitable attachment found for full text extraction. This item may not have any attached files or they may not be in a supported format.]"
            full_text = ""

        # Combine all sections
        return f"{header}{attachment_info}{full_text}"

    except Exception as e:
        return f"Error retrieving item full text: {str(e)}"


@mcp.tool(
    name="zotero_item_fulltext_info",
    description="Get attachment and indexed full-text size information for a Zotero item without returning the full document.",
)
def get_item_fulltext_info(
    item_key: str,
    chunk_chars: int | None = DEFAULT_CHUNK_CHARS,
    overlap_chars: int | None = DEFAULT_CHUNK_OVERLAP,
) -> str:
    """Get size and chunking information for a Zotero item's indexed text."""
    chunk_chars = clamp_int(chunk_chars, DEFAULT_CHUNK_CHARS, 1000, MAX_CHUNK_CHARS)
    overlap_chars = clamp_int(overlap_chars, DEFAULT_CHUNK_OVERLAP, 0, chunk_chars - 1)

    try:
        zot = get_zotero_client()
        item, attachment, content, error = get_indexed_attachment_text(zot, item_key)
    except Exception as e:
        return f"Error retrieving item full text information: {str(e)}"

    if error:
        return f"Error: {error}"

    assert item is not None
    assert attachment is not None
    assert content is not None

    data = item.get("data", {})
    text_length = len(content)
    word_count = len(content.split())
    total_chunks = chunk_count(text_length, chunk_chars, overlap_chars)
    first_preview = make_snippet(content, 0, 500)
    last_preview = make_snippet(content, max(0, text_length - 1), 500)

    return "\n".join(
        [
            f"# Full-Text Info: {data.get('title', 'Untitled')}",
            f"Item Key: `{item_key}`",
            f"Attachment Key: `{attachment.key}`",
            f"Attachment Type: {attachment.content_type}",
            f"Characters: {text_length}",
            f"Words: ~{word_count}",
            f"Chunk Size: {chunk_chars} characters",
            f"Chunk Overlap: {overlap_chars} characters",
            f"Estimated Chunks: {total_chunks}",
            "",
            "## Start Preview",
            first_preview,
            "",
            "## End Preview",
            last_preview,
        ]
    )


@mcp.tool(
    name="zotero_item_text_chunk",
    description="Read one bounded overlapping chunk of a Zotero item's indexed attachment text. Chunk indexes are 1-based.",
)
def get_item_text_chunk(
    item_key: str,
    chunk_index: int = 1,
    chunk_chars: int | None = DEFAULT_CHUNK_CHARS,
    overlap_chars: int | None = DEFAULT_CHUNK_OVERLAP,
) -> str:
    """Read one chunk of indexed full text for long documents."""
    chunk_chars = clamp_int(chunk_chars, DEFAULT_CHUNK_CHARS, 1000, MAX_CHUNK_CHARS)
    overlap_chars = clamp_int(overlap_chars, DEFAULT_CHUNK_OVERLAP, 0, chunk_chars - 1)
    chunk_index = max(1, chunk_index)

    try:
        zot = get_zotero_client()
        item, attachment, content, error = get_indexed_attachment_text(zot, item_key)
    except Exception as e:
        return f"Error retrieving item text chunk: {str(e)}"

    if error:
        return f"Error: {error}"

    assert item is not None
    assert attachment is not None
    assert content is not None

    total_chunks = chunk_count(len(content), chunk_chars, overlap_chars)
    if chunk_index > total_chunks:
        return (
            f"Requested chunk {chunk_index}, but item `{item_key}` only has "
            f"{total_chunks} chunk(s) at {chunk_chars} characters with "
            f"{overlap_chars} overlap."
        )

    start, end = chunk_bounds(len(content), chunk_index, chunk_chars, overlap_chars)
    data = item.get("data", {})
    lines = [
        f"# Text Chunk {chunk_index}/{total_chunks}: {data.get('title', 'Untitled')}",
        f"Item Key: `{item_key}`",
        f"Attachment Key: `{attachment.key}`",
        f"Character Range: {start}-{end}",
    ]
    if chunk_index > 1:
        lines.append(f"Previous Chunk: {chunk_index - 1}")
    if chunk_index < total_chunks:
        lines.append(f"Next Chunk: {chunk_index + 1}")
    lines.extend(["", "## Content", content[start:end]])
    return "\n".join(lines)


@mcp.tool(
    name="zotero_item_search_text",
    description="Search within one Zotero item's indexed attachment text and return snippets, chunk indexes, and character offsets.",
)
def search_item_text(
    item_key: str,
    query: str,
    match_mode: Literal["auto", "phrase", "all_terms", "any_term"] | None = "auto",
    max_results: int | None = 10,
    context_chars: int | None = 500,
) -> str:
    """Search within a single Zotero item's indexed full text."""
    max_results = clamp_int(max_results, 10, 1, 25)
    context_chars = clamp_int(context_chars, 500, 80, MAX_CONTEXT_CHARS)
    match_mode = match_mode or "auto"

    if not query.strip():
        return "Please provide a query to search for."

    try:
        zot = get_zotero_client()
        item, attachment, content, error = get_indexed_attachment_text(zot, item_key)
    except Exception as e:
        return f"Error searching item text: {str(e)}"

    if error:
        return f"Error: {error}"

    assert item is not None
    assert attachment is not None
    assert content is not None

    results = []
    phrase_matches = list(
        re.finditer(re.escape(query.strip()), content, flags=re.IGNORECASE)
    )
    effective_mode = match_mode
    if match_mode == "auto":
        effective_mode = "phrase" if phrase_matches else "all_terms"

    if effective_mode == "phrase":
        for match in phrase_matches[:max_results]:
            results.append(
                {
                    "offset": match.start(),
                    "score": 1,
                    "snippet": make_snippet(content, match.start(), context_chars),
                }
            )
    else:
        terms = tokenize_query(query)
        if not terms:
            return "Please provide at least one searchable word."
        total_chunks = chunk_count(
            len(content), DEFAULT_CHUNK_CHARS, DEFAULT_CHUNK_OVERLAP
        )
        for chunk_index in range(1, total_chunks + 1):
            start, end = chunk_bounds(
                len(content),
                chunk_index,
                DEFAULT_CHUNK_CHARS,
                DEFAULT_CHUNK_OVERLAP,
            )
            chunk = content[start:end]
            chunk_lower = chunk.lower()
            term_counts = {term: chunk_lower.count(term.lower()) for term in terms}
            present_terms = [term for term, count in term_counts.items() if count > 0]
            if effective_mode == "all_terms" and len(present_terms) != len(terms):
                continue
            if effective_mode == "any_term" and not present_terms:
                continue
            score = sum(term_counts.values())
            first_match = min(
                (
                    chunk_lower.find(term.lower())
                    for term in present_terms
                    if chunk_lower.find(term.lower()) >= 0
                ),
                default=0,
            )
            results.append(
                {
                    "offset": start + first_match,
                    "score": score,
                    "snippet": make_snippet(
                        content, start + first_match, context_chars
                    ),
                }
            )
        results.sort(key=lambda result: (-result["score"], result["offset"]))
        results = results[:max_results]

    if not results:
        return f"No indexed-text matches found for `{query}` in item `{item_key}`."

    data = item.get("data", {})
    header = [
        f"# Text Search: {query}",
        f"Item: {data.get('title', 'Untitled')} (`{item_key}`)",
        f"Attachment: `{attachment.key}`",
        f"Mode: {effective_mode}",
        f"Results: {len(results)}",
    ]
    formatted_results = []
    for index, result in enumerate(results, start=1):
        offset = result["offset"]
        formatted_results.append(
            "\n".join(
                [
                    f"## {index}. Match",
                    f"Character Offset: {offset}",
                    f"Approx. Chunk: {chunk_index_for_offset(offset)}",
                    f"Score: {result['score']}",
                    "",
                    result["snippet"],
                ]
            )
        )

    return "\n\n".join(header + formatted_results)


@mcp.tool(
    name="zotero_item_outline",
    description="Extract likely headings or table-of-contents entries from a Zotero item's indexed attachment text.",
)
def get_item_outline(item_key: str, limit: int | None = 80) -> str:
    """Extract likely structural headings from indexed text."""
    limit = clamp_int(limit, 80, 5, 200)

    try:
        zot = get_zotero_client()
        item, attachment, content, error = get_indexed_attachment_text(zot, item_key)
    except Exception as e:
        return f"Error extracting item outline: {str(e)}"

    if error:
        return f"Error: {error}"

    assert item is not None
    assert attachment is not None
    assert content is not None

    headings = extract_headings(content, limit=limit)
    if not headings:
        return (
            f"No likely headings found in indexed text for item `{item_key}`. "
            "Try zotero_item_search_text for topic lookup."
        )

    data = item.get("data", {})
    lines = [
        f"# Indexed Text Outline: {data.get('title', 'Untitled')}",
        f"Item Key: `{item_key}`",
        f"Attachment Key: `{attachment.key}`",
        f"Headings Returned: {len(headings)}",
    ]
    for index, heading in enumerate(headings, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {heading['title']}",
                f"Character Offset: {heading['offset']}",
                f"Approx. Chunk: {heading['chunk']}",
            ]
        )

    return "\n".join(lines)


@mcp.tool(
    name="zotero_item_read_section",
    description="Read text under a likely heading or section title from a Zotero item's indexed attachment text.",
)
def read_item_section(
    item_key: str,
    section_title: str,
    occurrence: int | None = 1,
    max_chars: int | None = DEFAULT_CHUNK_CHARS,
    prefer_longest_match: bool | None = True,
) -> str:
    """Read a likely section from a Zotero item's indexed full text."""
    occurrence = clamp_int(occurrence, 1, 1, 50)
    max_chars = clamp_int(max_chars, DEFAULT_CHUNK_CHARS, 1000, MAX_CHUNK_CHARS)

    if not section_title.strip():
        return "Please provide a section title or heading to read."

    try:
        zot = get_zotero_client()
        item, attachment, content, error = get_indexed_attachment_text(zot, item_key)
    except Exception as e:
        return f"Error reading item section: {str(e)}"

    if error:
        return f"Error: {error}"

    assert item is not None
    assert attachment is not None
    assert content is not None

    headings = extract_headings(content, limit=1000, dedupe=False)
    matches = find_heading_matches(headings, section_title)
    if len(matches) < occurrence:
        return (
            f"No heading match #{occurrence} found for `{section_title}` in item "
            f"`{item_key}`. Try zotero_item_outline or zotero_item_search_text."
        )

    if prefer_longest_match and occurrence == 1:
        selected = max(
            matches,
            key=lambda match: (
                section_end_offset(headings, match["offset"], len(content))
                - match["offset"]
            ),
        )
    else:
        selected = matches[occurrence - 1]
    start = selected["offset"]
    next_heading_offset = section_end_offset(headings, start, len(content))
    if next_heading_offset == len(content):
        next_heading_offset = None

    end = min(len(content), start + max_chars)
    if next_heading_offset is not None:
        end = min(end, next_heading_offset)

    data = item.get("data", {})
    truncated = end < len(content) and (
        next_heading_offset is None or end < next_heading_offset
    )
    lines = [
        f"# Section Read: {selected['title']}",
        f"Item: {data.get('title', 'Untitled')} (`{item_key}`)",
        f"Attachment: `{attachment.key}`",
        f"Character Range: {start}-{end}",
        f"Approx. Chunk: {chunk_index_for_offset(start)}",
    ]
    if next_heading_offset is not None:
        lines.append(f"Next Heading Offset: {next_heading_offset}")
    if truncated:
        lines.append("Truncated: true; increase max_chars to read more.")
    lines.extend(["", "## Content", content[start:end]])
    return "\n".join(lines)


@mcp.tool(
    name="zotero_find_item_by_doi",
    description="Find Zotero items by DOI. Accepts raw DOI, doi: DOI, or https://doi.org/DOI.",
)
def find_item_by_doi(doi: str, limit: int | None = 25) -> str:
    """Find Zotero items by DOI."""
    normalized = normalize_doi(doi)
    if not normalized:
        return "Please provide a DOI to search for."

    try:
        zot = get_zotero_client()
        results: Any = zot.items(q=normalized, qmode="everything", limit=limit)
    except Exception as e:
        return f"Error searching Zotero items by DOI: {str(e)}"

    exact_matches = []
    seen_keys = set()
    parent_keys = []
    for item in results:
        data = item.get("data", {})
        item_key = item.get("key") or data.get("key")
        if normalize_doi(data.get("DOI", "")) == normalized:
            exact_matches.append(item)
            seen_keys.add(item_key)
        elif parent := data.get("parentItem"):
            parent_keys.append(parent)

    for parent_key in dict.fromkeys(parent_keys):
        try:
            parent_item: Any = zot.item(parent_key)
        except Exception:
            continue

        data = parent_item.get("data", {})
        item_key = parent_item.get("key") or data.get("key")
        if (
            item_key not in seen_keys
            and normalize_doi(data.get("DOI", "")) == normalized
        ):
            exact_matches.append(parent_item)
            seen_keys.add(item_key)

    if exact_matches:
        header = [
            f"# DOI Match: {normalized}",
            f"Found {len(exact_matches)} exact match(es).",
        ]
        return "\n\n".join(header + [format_item(item) for item in exact_matches])

    if results:
        header = [
            f"# DOI Search: {normalized}",
            "No exact DOI match found, but Zotero returned possible matches.",
        ]
        return "\n\n".join(
            header
            + [format_item_summary(item, i + 1) for i, item in enumerate(results)]
        )

    return f"No Zotero item found with DOI: {normalized}"


@mcp.tool(
    name="zotero_item_children",
    description="List child notes and attachments for a Zotero item key.",
)
def get_item_children(item_key: str, limit: int | None = 50) -> str:
    """List child notes and attachments for a Zotero item."""
    try:
        zot = get_zotero_client()
        children: Any = zot.children(item_key, limit=limit)
    except Exception as e:
        return f"Error retrieving item children: {str(e)}"

    if not children:
        return f"No child notes or attachments found for item key: {item_key}"

    header = [
        f"# Child Items for `{item_key}`",
        f"Found {len(children)} child item(s).",
    ]
    return "\n\n".join(
        header + [format_item_summary(item, i + 1) for i, item in enumerate(children)]
    )


@mcp.tool(
    name="zotero_list_collections",
    description="List Zotero collections with collection keys, parent collection keys, and item counts.",
)
def list_collections(limit: int | None = 100) -> str:
    """List Zotero collections."""
    try:
        zot = get_zotero_client()
        collections: Any = zot.collections(limit=limit)
    except Exception as e:
        return f"Error retrieving Zotero collections: {str(e)}"

    if not collections:
        return "No Zotero collections found."

    lines = [
        "# Zotero Collections",
        f"Found {len(collections)} collection(s).",
    ]
    for i, collection in enumerate(collections, start=1):
        data = collection.get("data", {})
        meta = collection.get("meta", {})
        parent = data.get("parentCollection") or "top-level"
        lines.extend(
            [
                f"\n## {i}. {data.get('name', 'Untitled Collection')}",
                f"Key: `{collection.get('key') or data.get('key', '')}`",
                f"Parent: `{parent}`" if parent != "top-level" else "Parent: top-level",
                f"Items: {meta.get('numItems', 'unknown')}",
                f"Subcollections: {meta.get('numCollections', 'unknown')}",
            ]
        )

    return "\n".join(lines)


@mcp.tool(
    name="zotero_collection_items",
    description="List items in a Zotero collection, given the collection key.",
)
def get_collection_items(collection_key: str, limit: int | None = 25) -> str:
    """List items in a Zotero collection."""
    try:
        zot = get_zotero_client()
        items: Any = zot.collection_items(collection_key, limit=limit)
    except Exception as e:
        return f"Error retrieving Zotero collection items: {str(e)}"

    if not items:
        return f"No Zotero items found in collection key: {collection_key}"

    header = [
        f"# Collection Items for `{collection_key}`",
        f"Found {len(items)} item(s).",
    ]
    return "\n\n".join(
        header + [format_item_summary(item, i + 1) for i, item in enumerate(items)]
    )


@mcp.tool(
    name="zotero_list_tags",
    description="List tags in the Zotero library.",
)
def list_tags(limit: int | None = 100) -> str:
    """List Zotero tags."""
    try:
        zot = get_zotero_client()
        tags: Any = zot.tags(limit=limit)
    except Exception as e:
        return f"Error retrieving Zotero tags: {str(e)}"

    if not tags:
        return "No Zotero tags found."

    lines = [
        "# Zotero Tags",
        f"Found {len(tags)} tag(s).",
    ]
    for tag in tags:
        tag_name = tag.get("tag") if isinstance(tag, dict) else str(tag)
        lines.append(f"- `{tag_name}`")

    return "\n".join(lines)


@mcp.tool(
    name="zotero_search_items",
    # More detail can be added if useful: https://www.zotero.org/support/dev/web_api/v3/basics#searching
    description="Search for items in your Zotero library, given a query string, query mode (titleCreatorYear or everything), and optional tag search (supports boolean searches). Returned results can be looked up with zotero_item_fulltext or zotero_item_metadata.",
)
def search_items(
    query: str,
    qmode: Literal["titleCreatorYear", "everything"] | None = "titleCreatorYear",
    tag: str | None = None,
    limit: int | None = 10,
) -> str:
    """Search for items in your Zotero library"""
    try:
        zot = get_zotero_client()

        # Search using the q parameter
        params = {"q": query, "qmode": qmode, "limit": limit}
        if tag:
            params["tag"] = tag

        zot.add_parameters(**params)
        # n.b. types for this return do not work, it's a parsed JSON object
        results: Any = zot.items()
    except Exception as e:
        return f"Error searching Zotero items: {str(e)}"

    if not results:
        return "No items found matching your query."

    # Header with search info
    header = [
        f"# Search Results for: '{query}'",
        f"Found {len(results)} items." + (f" Using tag filter: {tag}" if tag else ""),
        "Use item keys with zotero_item_metadata or zotero_item_fulltext for more details.\n",
    ]

    # Format results
    formatted_results = []
    for i, item in enumerate(results):
        data = item["data"]
        item_key = item.get("key", "")
        item_type = data.get("itemType", "unknown")

        # Special handling for notes
        if item_type == "note":
            # Get note content
            note_content = strip_note_html(data.get("note", ""))

            # Extract a title from the first line if possible, otherwise use first few words
            title_preview = ""
            if note_content:
                lines = note_content.strip().split("\n")
                first_line = lines[0].strip()
                if first_line:
                    # Use first line if it's reasonably short, otherwise use first few words
                    if len(first_line) <= 50:
                        title_preview = first_line
                    else:
                        words = first_line.split()
                        title_preview = " ".join(words[:5]) + "..."

            # Create a good title for the note
            note_title = title_preview if title_preview else "Note"

            # Get a preview of the note content (truncated)
            preview = note_content.strip()
            if len(preview) > 150:
                preview = preview[:147] + "..."

            # Format the note entry
            entry = [
                f"## {i + 1}. 📝 {note_title}",
                f"**Type**: Note | **Key**: `{item_key}`",
                f"\n{preview}",
            ]

            # Add parent item reference if available
            if parent_item := data.get("parentItem"):
                entry.insert(2, f"**Parent Item**: `{parent_item}`")

            # Add tags if present (limited to first 5)
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags[:5]]
                if len(tags) > 5:
                    tag_list.append("...")
                entry.append(f"\n**Tags**: {' '.join(tag_list)}")

            formatted_results.append("\n".join(entry))
            continue

        # Regular item processing (non-notes)
        title = data.get("title", "Untitled")
        date = data.get("date", "")

        # Get publication or source info
        source = ""
        if pub := data.get("publicationTitle"):
            source = pub
        elif book := data.get("bookTitle"):
            source = f"In: {book}"
        elif publisher := data.get("publisher"):
            source = f"{publisher}"

        # Get a brief abstract (truncated if too long)
        abstract = data.get("abstractNote", "")
        if len(abstract) > 150:
            abstract = abstract[:147] + "..."

        # Build formatted entry with markdown for better structure
        entry = [
            f"## {i + 1}. {title}",
            f"**Type**: {item_type} | **Date**: {date} | **Key**: `{item_key}`",
            f"**Authors**: {creator_summary(data)}",
        ]

        if source:
            entry.append(f"**Source**: {source}")

        if abstract:
            entry.append(f"\n{abstract}")

        # Add tags if present (limited to first 5)
        if tags := data.get("tags"):
            tag_list = [f"`{tag['tag']}`" for tag in tags[:5]]
            if len(tags) > 5:
                tag_list.append("...")
            entry.append(f"\n**Tags**: {' '.join(tag_list)}")

        formatted_results.append("\n".join(entry))

    return "\n\n".join(header + formatted_results)
