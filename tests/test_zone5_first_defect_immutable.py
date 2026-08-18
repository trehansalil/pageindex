"""Zone 5: first_defect assignment regression guard.

Static analysis of client.py to ensure ``first_defect =`` (assignment)
appears only at the expected sites and is NOT inside the RecoveryOutcome
revert block.

Zone-3 deleted ``_finalize_routing`` and inlined its recomputation
directly into the gate-driven recovery loop in ``index()`` (once per
loop iteration) plus once more after the post-loop image-dominant-OCR
recovery. Expected sites are therefore:
  1. ``_convert_to_tree`` initial assignment (1 site)
  2. in-loop re-derivation, if/else branch on ``state.gate_result`` (2 sites)
  3. post-loop re-derivation, same if/else branch (2 sites)
Total: 5.
"""
from __future__ import annotations

import re
from pathlib import Path

CLIENT_PATH = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client.py"


def _read_client() -> str:
    return CLIENT_PATH.read_text(encoding="utf-8")


class TestFirstDefectAssignmentSites:
    """first_defect is assigned in _convert_to_tree (1) plus the inlined
    in-loop and post-loop re-derivation blocks (2 + 2) that replaced
    _finalize_routing under Zone-3."""

    _ASSIGN_RE = re.compile(
        r"^\s*(?:state\.)?first_defect\s*(?::.*)?=\s", re.MULTILINE
    )

    def test_known_assignment_count(self):
        source = _read_client()
        matches = self._ASSIGN_RE.findall(source)
        assert len(matches) == 5, (
            f"Expected 5 `first_defect =` assignments "
            f"(1 in _convert_to_tree + 2 in-loop + 2 post-loop "
            f"re-derivation sites inlined from deleted _finalize_routing), "
            f"found {len(matches)}: {matches}"
        )


class TestFirstDefectNotInRevertBlock:
    """first_defect must NOT be reassigned inside a RecoveryOutcome revert block.

    Zone-3 replaced ExtractionSnapshot.restore() with RecoveryOutcome.apply();
    client.py no longer calls .restore() at all, so this guard is
    vacuously satisfied post-refactor -- kept as a regression guard in
    case a future revert-style call is reintroduced under either name.
    """

    def test_no_assignment_after_restore(self):
        source = _read_client()
        lines = source.splitlines()
        in_revert = False
        revert_indent = 0
        violations = []

        for i, line in enumerate(lines, 1):
            # Detect a restore()/apply() call that begins a revert block
            if ".restore()" in line or ".apply(state)" in line:
                in_revert = True
                revert_indent = len(line) - len(line.lstrip())
                continue

            if in_revert:
                stripped = line.lstrip()
                current_indent = len(line) - len(line.lstrip())
                # Exit the revert block when dedented back to or past restore indent
                if stripped and current_indent <= revert_indent and not stripped.startswith("#"):
                    in_revert = False

                if re.match(r"\s*(?:state\.)?first_defect\s*=", line):
                    violations.append(f"line {i}: {line.strip()}")

        assert not violations, (
            "first_defect assigned inside ExtractionSnapshot revert block: "
            + "; ".join(violations)
        )
