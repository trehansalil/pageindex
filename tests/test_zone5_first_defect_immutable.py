"""Zone 5: first_defect write-once regression guard.

Static analysis of client.py to ensure ``first_defect =`` (assignment)
appears at most once per validate_tree call site and is NOT inside
the ExtractionSnapshot revert block.
"""
from __future__ import annotations

import re
from pathlib import Path

CLIENT_PATH = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client.py"


def _read_client() -> str:
    return CLIENT_PATH.read_text(encoding="utf-8")


class TestFirstDefectWriteOnce:
    """first_defect must be assigned exactly once (write-once semantics)."""

    _ASSIGN_RE = re.compile(
        r"^\s*(?:state\.)?first_defect\s*(?::.*)?=\s", re.MULTILINE
    )

    def test_single_assignment(self):
        source = _read_client()
        matches = self._ASSIGN_RE.findall(source)
        assert len(matches) == 1, (
            f"Expected exactly 1 `first_defect =` assignment, found {len(matches)}: "
            f"{matches}"
        )


class TestFirstDefectNotInRevertBlock:
    """first_defect must NOT be reassigned inside the ExtractionSnapshot revert block."""

    def test_no_assignment_after_restore(self):
        source = _read_client()
        lines = source.splitlines()
        in_revert = False
        revert_indent = 0
        violations = []

        for i, line in enumerate(lines, 1):
            # Detect the restore() call that begins the revert block
            if ".restore()" in line:
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
