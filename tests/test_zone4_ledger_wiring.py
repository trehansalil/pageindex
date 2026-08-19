"""Zone 4 contract tests: verdict ledger wiring into production paths.

Validates:
  - save_doc_meta calls persist_verdict_ledger conditionally on CAS guard
  - _persist_flat_result AND _persist_tree_result both independently call
    read_verdict_ledger (two distinct wiring assertions)
  - read_verdict_ledger is imported in client.py
  - persist_verdict_ledger is called inside save_doc_meta
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# 1. save_doc_meta wires persist_verdict_ledger
# ---------------------------------------------------------------------------


class TestSaveDocMetaLedgerWiring:
    def test_save_doc_meta_source_calls_persist_verdict_ledger(self):
        """save_doc_meta must contain a call to persist_verdict_ledger."""
        from pageindex_mcp.storage import save_doc_meta
        src = inspect.getsource(save_doc_meta)
        assert "persist_verdict_ledger(" in src, (
            "save_doc_meta must call persist_verdict_ledger for Zone-4 verdict ledger wiring"
        )

    def test_save_doc_meta_ledger_gated_by_skip_verdict(self):
        """persist_verdict_ledger call must be gated by the CAS guard (_skip_verdict)."""
        from pageindex_mcp.storage import save_doc_meta
        src = inspect.getsource(save_doc_meta)
        # The ledger call is inside `if not _skip_verdict and ...`
        assert "_skip_verdict" in src, (
            "save_doc_meta must gate ledger write behind _skip_verdict CAS guard"
        )
        # Verify the gating pattern: `not _skip_verdict` appears before persist_verdict_ledger
        skip_pos = src.find("not _skip_verdict")
        persist_pos = src.find("persist_verdict_ledger(")
        assert skip_pos < persist_pos, (
            "CAS guard check must precede persist_verdict_ledger call in save_doc_meta"
        )

    def test_save_doc_meta_ledger_requires_verdict_and_sha256(self):
        """persist_verdict_ledger call must check for verdict and sha256 presence."""
        from pageindex_mcp.storage import save_doc_meta
        src = inspect.getsource(save_doc_meta)
        # The guard checks sidecar.get("verdict") and sidecar.get("sha256")
        assert 'sidecar.get("verdict")' in src or "sidecar.get('verdict')" in src, (
            "save_doc_meta must check verdict presence before ledger write"
        )
        assert 'sidecar.get("sha256")' in src or "sidecar.get('sha256')" in src, (
            "save_doc_meta must check sha256 presence before ledger write"
        )

    def test_save_doc_meta_ledger_is_fire_and_forget(self):
        """persist_verdict_ledger call must be wrapped in try/except (fire-and-forget)."""
        from pageindex_mcp.storage import save_doc_meta
        src = inspect.getsource(save_doc_meta)
        # Find the persist_verdict_ledger call and verify it's inside a try block
        # by checking the surrounding lines contain try/except
        persist_idx = src.find("persist_verdict_ledger(")
        assert persist_idx > 0
        # Look backwards from persist call for a try: statement
        before = src[:persist_idx]
        # The try should be close (within ~200 chars before)
        try_pos = before.rfind("try:")
        assert try_pos > 0 and (persist_idx - try_pos) < 300, (
            "persist_verdict_ledger must be inside a try block (fire-and-forget)"
        )


# ---------------------------------------------------------------------------
# 2. _persist_flat_result wires read_verdict_ledger
# ---------------------------------------------------------------------------


class TestPersistFlatResultLedgerWiring:
    def test_persist_flat_result_calls_read_verdict_ledger(self):
        """_persist_flat_result must call read_verdict_ledger for hysteresis anchoring."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_flat_result)
        assert "read_verdict_ledger" in src, (
            "_persist_flat_result must call read_verdict_ledger for Zone-4 "
            "hysteresis anchoring in the flat path"
        )

    def test_persist_flat_result_has_ledger_priority(self):
        """_persist_flat_result must define _LEDGER_PRIORITY for verdict comparison."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_flat_result)
        assert "_LEDGER_PRIORITY" in src, (
            "_persist_flat_result must use _LEDGER_PRIORITY for hysteresis comparison"
        )


# ---------------------------------------------------------------------------
# 3. _persist_tree_result wires read_verdict_ledger
# ---------------------------------------------------------------------------


class TestPersistTreeResultLedgerWiring:
    def test_persist_tree_result_calls_read_verdict_ledger(self):
        """_persist_tree_result must call read_verdict_ledger for hysteresis anchoring."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "read_verdict_ledger" in src, (
            "_persist_tree_result must call read_verdict_ledger for Zone-4 "
            "hysteresis anchoring in the tree path"
        )

    def test_persist_tree_result_has_ledger_priority(self):
        """_persist_tree_result must define _LEDGER_PRIORITY for verdict comparison."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "_LEDGER_PRIORITY" in src, (
            "_persist_tree_result must use _LEDGER_PRIORITY for hysteresis comparison"
        )


# ---------------------------------------------------------------------------
# 4. Symmetric wiring: both paths read the ledger
# ---------------------------------------------------------------------------


class TestSymmetricWiring:
    def test_both_paths_wire_ledger_independently(self):
        """Both _persist_flat_result and _persist_tree_result must independently
        call read_verdict_ledger -- not share a single call site."""
        from pageindex_mcp.client import CustomPageIndexClient
        flat_src = inspect.getsource(CustomPageIndexClient._persist_flat_result)
        tree_src = inspect.getsource(CustomPageIndexClient._persist_tree_result)

        flat_count = flat_src.count("read_verdict_ledger")
        tree_count = tree_src.count("read_verdict_ledger")

        assert flat_count >= 1, "Flat path missing read_verdict_ledger call"
        assert tree_count >= 1, "Tree path missing read_verdict_ledger call"

    def test_read_verdict_ledger_imported_in_client(self):
        """read_verdict_ledger must be imported in client.py."""
        import pageindex_mcp.client as client_mod
        src = inspect.getsource(client_mod)
        assert "read_verdict_ledger" in src, (
            "client.py must import read_verdict_ledger from storage"
        )
        # Verify it's in an import statement, not just a comment
        tree = ast.parse(src)
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "read_verdict_ledger":
                        imported = True
                        break
        assert imported, (
            "read_verdict_ledger must be imported via `from .storage import read_verdict_ledger`"
        )

    def test_persist_verdict_ledger_imported_in_storage(self):
        """persist_verdict_ledger must be defined in storage.py."""
        from pageindex_mcp.storage import persist_verdict_ledger
        assert callable(persist_verdict_ledger)


# ---------------------------------------------------------------------------
# 5. Hysteresis anchoring pattern: ledger overrides lower-priority verdict
# ---------------------------------------------------------------------------


class TestHysteresisAnchoringPattern:
    def test_flat_path_anchoring_logs_override(self):
        """Flat path must log when overriding verdict via ledger anchoring."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_flat_result)
        assert "anchored_by_ledger" in src, (
            "Flat path must produce anchored_by_ledger reason when overriding"
        )

    def test_tree_path_anchoring_logs_override(self):
        """Tree path must log when overriding verdict via ledger anchoring."""
        from pageindex_mcp.client import CustomPageIndexClient
        src = inspect.getsource(CustomPageIndexClient._persist_tree_result)
        assert "anchored_by_ledger" in src, (
            "Tree path must produce anchored_by_ledger reason when overriding"
        )
