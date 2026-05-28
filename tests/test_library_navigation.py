"""Tests for fork-specific library navigation tools"""

from typing import Any

from zotero_mcp import (
    find_item_by_doi,
    get_collection_items,
    get_item_children,
    healthcheck,
    list_collections,
    list_tags,
)


def test_healthcheck_ok(mock_zotero: Any, sample_item: dict[str, Any]) -> None:
    """Test healthcheck reports an OK connection"""
    mock_zotero.items.return_value = [sample_item]
    mock_zotero.endpoint = "http://localhost:23119/api"

    result = healthcheck()

    assert "Status: OK" in result
    assert "http://localhost:23119/api" in result
    assert "Test Article" in result
    mock_zotero.items.assert_called_once_with(limit=1)


def test_find_item_by_doi_exact_match(
    mock_zotero: Any, sample_item: dict[str, Any]
) -> None:
    """Test DOI lookup normalizes DOI URLs and returns exact matches"""
    mock_zotero.items.return_value = [sample_item]

    result = find_item_by_doi("https://doi.org/10.1234/test")

    assert "DOI Match: 10.1234/test" in result
    assert "Test Article" in result
    mock_zotero.items.assert_called_once_with(
        q="10.1234/test", qmode="everything", limit=25
    )


def test_find_item_by_doi_checks_attachment_parents(
    mock_zotero: Any,
    sample_item: dict[str, Any],
    sample_attachment: dict[str, Any],
) -> None:
    """Test DOI lookup follows matching attachment results to parent items"""
    sample_attachment["data"]["parentItem"] = "ABCD1234"
    mock_zotero.items.return_value = [sample_attachment]
    mock_zotero.item.return_value = sample_item

    result = find_item_by_doi("10.1234/test", limit=5)

    assert "DOI Match: 10.1234/test" in result
    assert "Test Article" in result
    mock_zotero.item.assert_called_once_with("ABCD1234")


def test_get_item_children(mock_zotero: Any, sample_attachment: dict[str, Any]) -> None:
    """Test child item listing includes attachment keys and content types"""
    mock_zotero.children.return_value = [sample_attachment]

    result = get_item_children("ABCD1234")

    assert "Child Items for `ABCD1234`" in result
    assert "`XYZ789`" in result
    assert "application/pdf" in result
    mock_zotero.children.assert_called_once_with("ABCD1234", limit=50)


def test_list_collections(mock_zotero: Any, sample_collection: dict[str, Any]) -> None:
    """Test collection listing includes collection keys and counts"""
    mock_zotero.collections.return_value = [sample_collection]

    result = list_collections()

    assert "Research Papers" in result
    assert "`COLL1234`" in result
    assert "Items: 12" in result
    mock_zotero.collections.assert_called_once_with(limit=100)


def test_get_collection_items(mock_zotero: Any, sample_item: dict[str, Any]) -> None:
    """Test collection item listing includes item summaries"""
    mock_zotero.collection_items.return_value = [sample_item]

    result = get_collection_items("COLL1234")

    assert "Collection Items for `COLL1234`" in result
    assert "Test Article" in result
    assert "`ABCD1234`" in result
    mock_zotero.collection_items.assert_called_once_with("COLL1234", limit=25)


def test_list_tags(mock_zotero: Any) -> None:
    """Test tag listing supports string and dict tag responses"""
    mock_zotero.tags.return_value = ["hardware", {"tag": "quantum"}]

    result = list_tags()

    assert "`hardware`" in result
    assert "`quantum`" in result
    mock_zotero.tags.assert_called_once_with(limit=100)
