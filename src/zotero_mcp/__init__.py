import os
import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from zotero_mcp.client import get_attachment_details, get_zotero_client

# Create an MCP server
mcp = FastMCP("Zotero")


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
