"""Tests for item metadata and fulltext operations"""

from typing import Any

from zotero_mcp import (
    get_item_fulltext,
    get_item_fulltext_info,
    get_item_metadata,
    get_item_outline,
    get_item_text_chunk,
    read_item_section,
    search_item_text,
)


def test_get_item_metadata(mock_zotero: Any, sample_item: dict[str, Any]) -> None:
    """Test retrieving item metadata"""
    mock_zotero.item.return_value = sample_item

    result = get_item_metadata("ABCD1234")

    assert "## Test Article" in result
    assert "Item Key: `ABCD1234`" in result
    assert "Type: journalArticle" in result
    assert "Date: 2024" in result
    assert "Doe, John; Smith, Jane" in result
    assert "### Abstract" in result
    assert "This is a test abstract" in result
    assert "### Tags" in result
    assert "`test`" in result and "`article`" in result
    assert "URL: https://example.com" in result
    assert "DOI: 10.1234/test" in result
    assert "Number of notes/attachments: 2" in result


def test_get_item_metadata_not_found(mock_zotero: Any) -> None:
    """Test retrieving metadata for nonexistent item"""
    mock_zotero.item.return_value = None

    result = get_item_metadata("NONEXISTENT")

    assert "No item found" in result


def test_get_item_fulltext(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test retrieving item fulltext"""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {"content": "Sample full text content"}

    result = get_item_fulltext("ABCD1234")

    assert "Test Article" in result
    assert "Sample full text content" in result
    assert "XYZ789" in result  # Attachment key


def test_get_item_fulltext_no_attachment(
    mock_zotero: Any, sample_item: dict[str, Any]
) -> None:
    """Test retrieving fulltext when no attachment is available"""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = []

    result = get_item_fulltext("ABCD1234")

    assert "No suitable attachment found" in result


def test_get_item_fulltext_info(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test retrieving size and chunking information without full document output."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "CMOS VLSI design introduction " * 120
    }

    result = get_item_fulltext_info("ABCD1234", chunk_chars=1000, overlap_chars=100)

    assert "Full-Text Info" in result
    assert "Attachment Key: `XYZ789`" in result
    assert "Estimated Chunks:" in result
    assert "Start Preview" in result


def test_get_item_text_chunk(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test reading one bounded chunk from a long item."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "A" * 1000 + "Chapter 2 Circuit Characterization" + "B" * 1200
    }

    result = get_item_text_chunk(
        "ABCD1234", chunk_index=2, chunk_chars=1000, overlap_chars=0
    )

    assert "Text Chunk 2/3" in result
    assert "Character Range: 1000-2000" in result
    assert "Chapter 2 Circuit Characterization" in result


def test_search_item_text_phrase(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test exact phrase search within a book-like full-text attachment."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "Chapter 5 CMOS inverter. The body effect changes threshold voltage."
    }

    result = search_item_text("ABCD1234", "body effect")

    assert "Text Search: body effect" in result
    assert "Mode: phrase" in result
    assert "Approx. Chunk:" in result
    assert "body effect changes threshold voltage" in result


def test_search_item_text_topic_fallback(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test topic search falls back to term matching when a phrase is absent."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": (
            "Chapter 6 Designing Combinational Logic. "
            "Dynamic nodes require careful power and noise analysis."
        )
    }

    result = search_item_text("ABCD1234", "dynamic power")

    assert "Mode: all_terms" in result
    assert "Dynamic nodes require careful power" in result


def test_get_item_outline(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test heuristic heading extraction from indexed book text."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "\n".join(
            [
                "CMOS VLSI Design: A Circuits and Systems Perspective",
                "Chapter 1 Introduction",
                "1.1 Historical Perspective",
                "1.2 CMOS Inverter",
                "This section discusses inverter transfer characteristics.",
            ]
        )
    }

    result = get_item_outline("ABCD1234")

    assert "Indexed Text Outline" in result
    assert "Chapter 1 Introduction" in result
    assert "1.2 CMOS Inverter" in result


def test_read_item_section(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test reading content under a requested section heading."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "\n".join(
            [
                "Chapter 1 Introduction",
                "Intro text.",
                "1.2 CMOS Inverter",
                "The CMOS inverter is the fundamental logic gate.",
                "Its switching threshold depends on device sizing.",
                "1.3 Power Dissipation",
                "Power has dynamic and static components.",
            ]
        )
    }

    result = read_item_section("ABCD1234", "CMOS Inverter")

    assert "Section Read: 1.2 CMOS Inverter" in result
    assert "The CMOS inverter is the fundamental logic gate." in result
    assert "Power has dynamic and static components." not in result


def test_read_item_section_prefers_body_over_toc(
    mock_zotero: Any, sample_item: dict[str, Any], sample_attachment: dict[str, Any]
) -> None:
    """Test duplicate TOC/body headings prefer the longer body section by default."""
    mock_zotero.item.return_value = sample_item
    mock_zotero.children.return_value = [sample_attachment]
    mock_zotero.fulltext_item.return_value = {
        "content": "\n".join(
            [
                "Contents",
                "1.2 CMOS Inverter ........ 14",
                "1.3 Power Dissipation ........ 18",
                "Chapter 1 Introduction",
                "1.2 CMOS Inverter",
                "The body section explains switching threshold and noise margins.",
                "It includes the material a user would want quoted.",
                "1.3 Power Dissipation",
                "Power content follows.",
            ]
        )
    }

    result = read_item_section("ABCD1234", "CMOS Inverter")

    assert "The body section explains switching threshold" in result
    assert "It includes the material a user would want quoted." in result
    assert "1.3 Power Dissipation ........ 18" not in result
