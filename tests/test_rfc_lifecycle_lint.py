# ALLOW-NEW-TEST-FILE: scripts/rfc_lifecycle_lint.py is not under src/pageindex_mcp/
"""RFC-041 D8 / Property 8 — RFC lifecycle CI gate tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rfc_lifecycle_lint.py"
_spec = importlib.util.spec_from_file_location("rfc_lifecycle_lint", _SCRIPT_PATH)
rfc_lifecycle_lint = importlib.util.module_from_spec(_spec)
sys.modules["rfc_lifecycle_lint"] = rfc_lifecycle_lint
_spec.loader.exec_module(rfc_lifecycle_lint)


def _write_rfc(
    dir_path: Path, filename: str, rfc_id: str, status: str, extra_body: str = ""
) -> Path:
    path = dir_path / filename
    path.write_text(
        f"""---
id: "{rfc_id}"
title: "Test RFC"
status: {status}
---

## Overview

Test body.
{extra_body}
""",
        encoding="utf-8",
    )
    return path


def _write_tasks(dir_path: Path, filename: str, lines: list[str]) -> Path:
    path = dir_path / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def rfc_env(tmp_path):
    rfcs_dir = tmp_path / "agents" / "rfcs"
    tasks_dir = tmp_path / "agents" / "tasks"
    zones_dir = tmp_path / "audit" / "zones"
    rfcs_dir.mkdir(parents=True)
    tasks_dir.mkdir(parents=True)
    zones_dir.mkdir(parents=True)
    return rfcs_dir, tasks_dir, zones_dir / "ZONE_OWNERSHIP.yaml"


def test_skipped_gate_detected(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "999-test.md", "RFC-999", "draft")
    _write_tasks(
        tasks_dir,
        "tasks-rfc999-test.md",
        [
            '- [ ] <a id="91"></a>9.1 **[GATE]** Scoped re-ingest and re-measurement',
            '- [x] <a id="92"></a>9.2 Promote bidi_coherence_enforce to blocking',
        ],
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    blocking = [v for v in violations if v.severity == "blocking"]
    assert any(v.rule == "skipped-gate" for v in blocking)


def test_gate_checked_before_later_task_is_clean(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "999-test.md", "RFC-999", "draft")
    _write_tasks(
        tasks_dir,
        "tasks-rfc999-test.md",
        [
            '- [x] <a id="91"></a>9.1 **[GATE]** Scoped re-ingest and re-measurement',
            '- [x] <a id="92"></a>9.2 Promote bidi_coherence_enforce to blocking',
        ],
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.severity == "blocking"]


def test_draft_with_all_tasks_checked_is_advisory(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "999-test.md", "RFC-999", "draft")
    _write_tasks(
        tasks_dir,
        "tasks-rfc999-test.md",
        [
            '- [x] <a id="11"></a>1.1 First task',
            '- [x] <a id="12"></a>1.2 Second task',
        ],
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.severity == "blocking"]
    assert any(v.rule == "all-tasks-done-draft" for v in violations)


def test_unresolved_open_questions_is_advisory(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(
        rfcs_dir,
        "999-test.md",
        "RFC-999",
        "draft",
        extra_body="\n## Open Questions\n\n1. **Unresolved thing:** still undecided.\n",
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.severity == "blocking"]
    assert any(v.rule == "unresolved-open-question" for v in violations)


def test_resolved_open_question_is_not_flagged(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(
        rfcs_dir,
        "999-test.md",
        "RFC-999",
        "draft",
        extra_body="\n## Open Questions\n\n1. **Settled thing:** RESOLVED, see RFC-998.\n",
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.rule == "unresolved-open-question"]


def test_closed_rfc_with_orphaned_zone_bugs_is_blocking(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "998-owner.md", "RFC-998", "implemented")
    zone_file.write_text(
        """
zones:
  zone_x:
    name: "Orphaned zone"
    owning_rfc: RFC-998
    resolved: false
    successor_rfc: null
""",
        encoding="utf-8",
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    blocking = [v for v in violations if v.severity == "blocking"]
    assert any(v.rule == "orphaned-zone" for v in blocking)


def test_zone_transferred_to_successor_is_clean(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "998-owner.md", "RFC-998", "implemented")
    _write_rfc(rfcs_dir, "999-successor.md", "RFC-999", "draft")
    zone_file.write_text(
        """
zones:
  zone_x:
    name: "Transferred zone"
    owning_rfc: RFC-998
    resolved: false
    successor_rfc: RFC-999
""",
        encoding="utf-8",
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.rule == "orphaned-zone"]


def test_zone_with_open_owning_rfc_is_not_flagged(rfc_env):
    rfcs_dir, tasks_dir, zone_file = rfc_env
    _write_rfc(rfcs_dir, "998-owner.md", "RFC-998", "draft")
    zone_file.write_text(
        """
zones:
  zone_x:
    name: "Still-open zone"
    owning_rfc: RFC-998
    resolved: false
    successor_rfc: null
""",
        encoding="utf-8",
    )
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    assert not [v for v in violations if v.rule == "orphaned-zone"]


def test_real_zone_ownership_manifest_loads():
    real_zone_file = Path(__file__).resolve().parents[1] / "audit" / "zones" / "ZONE_OWNERSHIP.yaml"
    manifest = rfc_lifecycle_lint.load_zone_ownership(real_zone_file)
    assert manifest["zones"]["zone_2"]["successor_rfc"] == "RFC-041"


def test_main_exits_nonzero_on_repo_state():
    rfcs_dir = Path(__file__).resolve().parents[1] / "agents" / "rfcs"
    tasks_dir = Path(__file__).resolve().parents[1] / "agents" / "tasks"
    zone_file = Path(__file__).resolve().parents[1] / "audit" / "zones" / "ZONE_OWNERSHIP.yaml"
    violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
    blocking = [v for v in violations if v.severity == "blocking"]
    assert any(v.rule == "skipped-gate" for v in blocking)
