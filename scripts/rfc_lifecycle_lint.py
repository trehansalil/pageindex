#!/usr/bin/env python3
"""RFC lifecycle CI gate — RFC-041 D8 / Property 8.

Parses agents/rfcs/*.md frontmatter, agents/tasks/*.md checkboxes, and
audit/zones/ZONE_OWNERSHIP.yaml to catch two recurring lifecycle failures:

  1. Checkbox-order skip (RFC-037 Release B pattern): a later-phase task is
     checked while an earlier task marked [GATE] is still unchecked.
  2. Scope-narrowing closure (RFC-040 Zone 2 pattern): a zone's owning RFC is
     closed, the zone still has unresolved bugs, and no successor RFC owns it.

Both are merge-blocking. All-tasks-done drafts and unresolved Open Questions
are advisory only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

CLOSED_STATUSES = {"implemented", "landed", "done", "closed"}

RFC_ID_RE = re.compile(r"^(\d{3})-")
TASKS_ID_RE = re.compile(r"tasks-rfc(\d{3})-")
CHECKBOX_RE = re.compile(
    r"^\s*-\s\[( |x|X)\]\s*(?:<a id=\"[^\"]*\"></a>)?\s*(\d+(?:\.\d+)*[a-z]?)\.?\s+(.*)$"
)
GATE_RE = re.compile(r"\[gate\]", re.IGNORECASE)
OPEN_QUESTIONS_ITEM_RE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*.*$", re.MULTILINE)


@dataclass
class RfcMeta:
    rfc_id: str
    status: str
    path: Path
    body: str


@dataclass
class TaskItem:
    line_no: int
    checked: bool
    task_id: str
    text: str
    is_gate: bool


@dataclass
class Violation:
    severity: str  # "blocking" | "advisory"
    rule: str
    message: str
    path: Path
    line_no: int | None = None


def _task_id_key(task_id: str) -> tuple[int, ...]:
    stripped = re.sub(r"[a-z]+$", "", task_id)
    return tuple(int(p) for p in stripped.split(".") if p)


def _extract_frontmatter(text: str) -> str | None:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].strip() == "" or lines[i].strip().startswith("<!--")):
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return None
    end = None
    for j in range(i + 1, len(lines)):
        if lines[j].strip() == "---":
            end = j
            break
    if end is None:
        return None
    return "\n".join(lines[i + 1 : end])


def parse_rfc_frontmatter(path: Path) -> RfcMeta | None:
    text = path.read_text(encoding="utf-8")
    raw_front = _extract_frontmatter(text)
    if raw_front is None:
        return None
    try:
        front = yaml.safe_load(raw_front) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(front, dict):
        return None
    rfc_id = str(front.get("id", "")).replace("RFC-", "").replace("RFC", "").strip()
    if not rfc_id:
        id_match = RFC_ID_RE.match(path.name)
        rfc_id = id_match.group(1) if id_match else path.stem
    rfc_id = rfc_id.zfill(3) if rfc_id.isdigit() else rfc_id
    status = str(front.get("status", "")).strip().lower()
    return RfcMeta(rfc_id=rfc_id, status=status, path=path, body=text)


def parse_tasks_file(path: Path) -> list[TaskItem]:
    items: list[TaskItem] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = CHECKBOX_RE.match(line)
        if not match:
            continue
        checked = match.group(1).lower() == "x"
        task_id = match.group(2)
        text = match.group(3)
        items.append(
            TaskItem(
                line_no=line_no,
                checked=checked,
                task_id=task_id,
                text=text,
                is_gate=bool(GATE_RE.search(text)),
            )
        )
    return items


def detect_skipped_gates(items: list[TaskItem], path: Path) -> list[Violation]:
    violations: list[Violation] = []
    open_gates: list[TaskItem] = []
    for item in items:
        if item.is_gate and not item.checked:
            open_gates.append(item)
            continue
        if item.checked and open_gates:
            for gate in open_gates:
                if _task_id_key(item.task_id) > _task_id_key(gate.task_id):
                    violations.append(
                        Violation(
                            severity="blocking",
                            rule="skipped-gate",
                            message=(
                                f"task {item.task_id} is checked but earlier GATE "
                                f"task {gate.task_id} ('{gate.text.strip()}') is unchecked"
                            ),
                            path=path,
                            line_no=item.line_no,
                        )
                    )
    return violations


def detect_all_done_draft(rfc: RfcMeta, items: list[TaskItem], path: Path) -> list[Violation]:
    if rfc.status != "draft" or not items:
        return []
    if all(item.checked for item in items):
        return [
            Violation(
                severity="advisory",
                rule="all-tasks-done-draft",
                message=(
                    f"RFC-{rfc.rfc_id} status is 'draft' but every task in "
                    f"{path.name} is checked"
                ),
                path=rfc.path,
            )
        ]
    return []


def detect_unresolved_open_questions(rfc: RfcMeta) -> list[Violation]:
    section = re.search(r"## Open Questions\n(.*?)(\n## |\Z)", rfc.body, re.DOTALL)
    if not section:
        return []
    body = section.group(1)
    violations = []
    for match in OPEN_QUESTIONS_ITEM_RE.finditer(body):
        item_text = match.group(0)
        if re.search(r"\bresolved\b", item_text, re.IGNORECASE):
            continue
        violations.append(
            Violation(
                severity="advisory",
                rule="unresolved-open-question",
                message=f"RFC-{rfc.rfc_id} has an unresolved Open Question: {match.group(1)}",
                path=rfc.path,
            )
        )
    return violations


def load_zone_ownership(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def detect_orphaned_zones(
    zone_manifest: dict, rfc_statuses: dict[str, str], zone_path: Path
) -> list[Violation]:
    violations: list[Violation] = []
    for zone_id, zone in (zone_manifest.get("zones") or {}).items():
        if zone.get("resolved", False):
            continue
        owning_rfc = str(zone.get("owning_rfc") or "").replace("RFC-", "").strip()
        if not owning_rfc:
            continue
        owning_status = rfc_statuses.get(owning_rfc.zfill(3), rfc_statuses.get(owning_rfc))
        if owning_status not in CLOSED_STATUSES:
            continue
        if zone.get("successor_rfc"):
            continue
        violations.append(
            Violation(
                severity="blocking",
                rule="orphaned-zone",
                message=(
                    f"{zone_id} ('{zone.get('name', '')}') has unresolved bugs, "
                    f"owning RFC-{owning_rfc} is '{owning_status}', and no "
                    "successor_rfc is recorded"
                ),
                path=zone_path,
            )
        )
    return violations


def lint(rfcs_dir: Path, tasks_dir: Path, zone_file: Path) -> list[Violation]:
    violations: list[Violation] = []
    rfcs: dict[str, RfcMeta] = {}
    for rfc_path in sorted(rfcs_dir.glob("*.md")):
        meta = parse_rfc_frontmatter(rfc_path)
        if meta is None:
            continue
        rfcs[meta.rfc_id] = meta
        violations.extend(detect_unresolved_open_questions(meta))

    for tasks_path in sorted(tasks_dir.glob("*.md")):
        id_match = TASKS_ID_RE.search(tasks_path.name)
        rfc_id = id_match.group(1) if id_match else None
        items = parse_tasks_file(tasks_path)
        violations.extend(detect_skipped_gates(items, tasks_path))
        if rfc_id and rfc_id in rfcs:
            violations.extend(detect_all_done_draft(rfcs[rfc_id], items, tasks_path))

    rfc_statuses = {rfc_id: meta.status for rfc_id, meta in rfcs.items()}
    zone_manifest = load_zone_ownership(zone_file)
    violations.extend(detect_orphaned_zones(zone_manifest, rfc_statuses, zone_file))

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RFC lifecycle CI gate")
    parser.add_argument("--rfcs-dir", type=Path, default=Path("agents/rfcs"))
    parser.add_argument("--tasks-dir", type=Path, default=Path("agents/tasks"))
    parser.add_argument(
        "--zone-file", type=Path, default=Path("audit/zones/ZONE_OWNERSHIP.yaml")
    )
    args = parser.parse_args(argv)

    violations = lint(args.rfcs_dir, args.tasks_dir, args.zone_file)
    blocking = [v for v in violations if v.severity == "blocking"]
    advisory = [v for v in violations if v.severity == "advisory"]

    for v in blocking:
        loc = f"{v.path}:{v.line_no}" if v.line_no else str(v.path)
        print(f"[FAIL] {v.rule} — {loc} — {v.message}")
    for v in advisory:
        loc = f"{v.path}:{v.line_no}" if v.line_no else str(v.path)
        print(f"[WARN] {v.rule} — {loc} — {v.message}")

    print(f"\n{len(blocking)} blocking, {len(advisory)} advisory")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
