"""Tests for opt-in Zotero write operations"""

from typing import Any
from unittest.mock import MagicMock

from zotero_mcp import (
    apply_organization_plan,
    create_child_note,
    create_collection,
    delete_collection,
    rename_collection,
    update_item_collections,
    update_item_metadata,
    update_item_tags,
    write_status,
)


def test_write_status_disabled(monkeypatch) -> None:
    """Test write status reports disabled mode by default."""
    monkeypatch.delenv("ZOTERO_WRITE_ENABLED", raising=False)

    result = write_status()

    assert "ZOTERO_WRITE_ENABLED: disabled" in result
    assert "Real writes are blocked" in result


def test_create_collection_dry_run(mock_zotero: Any) -> None:
    """Test collection creation dry-run does not call Zotero write API."""
    result = create_collection("CMOS VLSI", dry_run=True)

    assert "Dry Run: Create Collection" in result
    assert "No Zotero changes were made." in result
    mock_zotero.create_collections.assert_not_called()


def test_create_collection_requires_write_env(mock_zotero: Any, monkeypatch) -> None:
    """Test real collection creation is blocked unless writes are enabled."""
    monkeypatch.delenv("ZOTERO_WRITE_ENABLED", raising=False)

    result = create_collection("CMOS VLSI", dry_run=False)

    assert "Write blocked" in result
    mock_zotero.create_collections.assert_not_called()


def test_create_collection_apply(mock_zotero: Any, monkeypatch) -> None:
    """Test collection creation calls Pyzotero when write mode is enabled."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.create_collections.return_value = {"successful": {"0": "NEWCOLL"}}

    result = create_collection(
        "CMOS VLSI", parent_collection_key="PARENT1", dry_run=False
    )

    assert "Applied: Create Collection" in result
    mock_zotero.create_collections.assert_called_once_with(
        [{"name": "CMOS VLSI", "parentCollection": "PARENT1"}]
    )


def test_rename_collection_apply(
    mock_zotero: Any, sample_collection: dict[str, Any], monkeypatch
) -> None:
    """Test collection rename updates fetched collection data."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.collection.return_value = sample_collection
    mock_zotero.update_collection.return_value = MagicMock(status_code=204)

    result = rename_collection("COLL1234", "VLSI References", dry_run=False)

    assert "Applied: Rename Collection" in result
    updated = mock_zotero.update_collection.call_args.args[0]
    assert updated["data"]["name"] == "VLSI References"


def test_delete_collection_dry_run(
    mock_zotero: Any, sample_collection: dict[str, Any]
) -> None:
    """Test delete collection defaults to dry-run."""
    mock_zotero.collection.return_value = sample_collection

    result = delete_collection("COLL1234")

    assert "Dry Run: Delete Collection" in result
    mock_zotero.delete_collection.assert_not_called()


def test_update_item_tags_dry_run(
    mock_zotero: Any, sample_item: dict[str, Any]
) -> None:
    """Test tag dry-run computes the resulting tag set."""
    mock_zotero.item.return_value = sample_item

    result = update_item_tags(
        "ABCD1234", add_tags=["vlsi", "cmos"], remove_tags=["test"]
    )

    assert "Dry Run: Update Item Tags" in result
    assert "Before Tags: test, article" in result
    assert "After Tags: article, vlsi, cmos" in result
    mock_zotero.update_item.assert_not_called()


def test_update_item_tags_apply(
    mock_zotero: Any, sample_item: dict[str, Any], monkeypatch
) -> None:
    """Test tag writes update item payload with Zotero tag objects."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.item.return_value = sample_item
    mock_zotero.update_item.return_value = MagicMock(status_code=204)

    result = update_item_tags("ABCD1234", replace_tags=["vlsi", "cmos"], dry_run=False)

    assert "Applied: Update Item Tags" in result
    updated = mock_zotero.update_item.call_args.args[0]
    assert updated["data"]["tags"] == [{"tag": "vlsi"}, {"tag": "cmos"}]


def test_update_item_collections_apply(
    mock_zotero: Any, sample_item: dict[str, Any], monkeypatch
) -> None:
    """Test collection assignment writes update the item collection list."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.item.return_value = sample_item
    mock_zotero.update_item.return_value = MagicMock(status_code=204)

    result = update_item_collections(
        "ABCD1234",
        add_collection_keys=["NEW1234"],
        remove_collection_keys=["OLD1234"],
        dry_run=False,
    )

    assert "Applied: Update Item Collections" in result
    updated = mock_zotero.update_item.call_args.args[0]
    assert updated["data"]["collections"] == ["NEW1234"]


def test_update_item_metadata_rejects_protected_field(
    mock_zotero: Any, sample_item: dict[str, Any]
) -> None:
    """Test protected item fields cannot be patched through metadata updates."""
    mock_zotero.item.return_value = sample_item

    result = update_item_metadata("ABCD1234", {"itemType": "book"})

    assert "Refusing to update protected Zotero fields" in result
    mock_zotero.update_item.assert_not_called()


def test_update_item_metadata_apply(
    mock_zotero: Any, sample_item: dict[str, Any], monkeypatch
) -> None:
    """Test safe metadata fields are patched onto the item data payload."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.item.return_value = sample_item
    mock_zotero.update_item.return_value = MagicMock(status_code=204)

    result = update_item_metadata(
        "ABCD1234", {"title": "Updated Title", "abstractNote": "Updated"}, dry_run=False
    )

    assert "Applied: Update Item Metadata" in result
    updated = mock_zotero.update_item.call_args.args[0]
    assert updated["data"]["title"] == "Updated Title"
    assert updated["data"]["abstractNote"] == "Updated"


def test_create_child_note_apply(
    mock_zotero: Any, sample_item: dict[str, Any], monkeypatch
) -> None:
    """Test child-note creation posts a note payload under the parent item."""
    monkeypatch.setenv("ZOTERO_WRITE_ENABLED", "true")
    mock_zotero.item.return_value = sample_item
    mock_zotero.create_items.return_value = {"successful": {"0": "NOTE123"}}

    result = create_child_note(
        "ABCD1234",
        "Suggested organization note\nUse for CMOS references.",
        tags=["organization"],
        dry_run=False,
    )

    assert "Applied: Create Child Note" in result
    payload = mock_zotero.create_items.call_args.args[0]
    assert payload[0]["itemType"] == "note"
    assert "<p>Suggested organization note</p>" in payload[0]["note"]
    assert payload[0]["tags"] == [{"tag": "organization"}]
    assert mock_zotero.create_items.call_args.kwargs["parentid"] == "ABCD1234"


def test_apply_organization_plan_dry_run(mock_zotero: Any) -> None:
    """Test organization plans summarize all supported actions in dry-run mode."""
    plan = {
        "create_collections": [{"name": "CMOS VLSI"}],
        "rename_collections": [{"collection_key": "COLL1234", "name": "VLSI"}],
        "item_updates": [
            {
                "item_key": "ABCD1234",
                "add_tags": ["vlsi"],
                "add_collection_keys": ["COLL1234"],
                "metadata": {"title": "Updated"},
            }
        ],
        "create_child_notes": [
            {"parent_item_key": "ABCD1234", "note_text": "Organization rationale"}
        ],
    }

    result = apply_organization_plan(plan)

    assert "Dry Run: Apply Organization Plan" in result
    assert "Create collection `CMOS VLSI`" in result
    assert "Update tags for item `ABCD1234`" in result
    assert "Create child note under `ABCD1234`" in result
    mock_zotero.update_item.assert_not_called()
