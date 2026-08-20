# tests/test_storage.py
"""wipe_processed() property tests (post-Zone-4 verdict ledger redesign).

wipe_processed() deletes all processed/* objects and leaves the verdicts/
prefix untouched.  No snapshot step is involved.
"""

from unittest.mock import MagicMock, call, patch

from pageindex_mcp.storage import wipe_processed


def _obj(name: str) -> MagicMock:
    obj = MagicMock()
    obj.object_name = name
    return obj


@patch("pageindex_mcp.storage.get_minio")
def test_wipe_processed_deletes_all_processed_objects(mock_get):
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = [
        _obj("processed/doc1.json"),
        _obj("processed/doc1.meta.json"),
        _obj("processed/doc2.json"),
    ]

    wipe_processed()

    remove_calls = [c for c in mc.mock_calls if c[0] == "remove_object"]
    removed = {c.args[1] for c in remove_calls}
    assert removed == {
        "processed/doc1.json",
        "processed/doc1.meta.json",
        "processed/doc2.json",
    }


@patch("pageindex_mcp.storage.get_minio")
def test_wipe_processed_only_lists_processed_prefix(mock_get):
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = []

    wipe_processed()

    mc.list_objects.assert_called_once()
    args, kwargs = mc.list_objects.call_args
    prefix = kwargs.get("prefix") or (args[1] if len(args) > 1 else None)
    assert prefix == "processed/"


@patch("pageindex_mcp.storage.get_minio")
def test_wipe_processed_empty_listing_is_noop(mock_get):
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = []

    wipe_processed()

    mc.remove_object.assert_not_called()


@patch("pageindex_mcp.storage.get_minio")
def test_wipe_processed_no_snapshot_step(mock_get):
    """Zone-4 removed snapshot_prior_verdicts — wipe_processed must not
    call put_object (no snapshot write) or stat_object (no snapshot check)."""
    mc = MagicMock()
    mock_get.return_value = mc
    mc.list_objects.return_value = [_obj("processed/d.json")]

    wipe_processed()

    put_calls = [c for c in mc.mock_calls if c[0] == "put_object"]
    assert put_calls == [], "wipe_processed should not write any snapshot"
    stat_calls = [c for c in mc.mock_calls if c[0] == "stat_object"]
    assert stat_calls == [], "wipe_processed should not check for snapshot"
