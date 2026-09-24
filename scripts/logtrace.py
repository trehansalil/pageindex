#!/usr/bin/env python3
"""logtrace -- reconstruct one document's ordered record sequence from a
captured JSON-lines log file (RFC-046 D12, task 12.10, R12.13).

Read-only. No Loki, no Grafana, no network, no third-party dependency --
stdlib only, by design (this is the acceptance instrument proving "recon-
structable from logs alone").

--- The correlation model (do not re-derive; read R12.3's 2026-09-18
correction in agents/rfcs/046-ocr-attribution-failure-cluster-remediation.md
and src/pageindex_mcp/obs/ before touching this file) ---

There is no single universal correlation key. Two routes bind differently:

  arq worker route:   parent binds run_id + job_id + doc_name
  batch/corpus route: parent binds run_id + doc_name    (never job_id)

(The worker route gained doc_name on 2026-09-18; logs written before that
carry job_id only, which is why doc_name is still treated as one of two
possible secondary key-fields rather than the universal one.)
  child (both routes): inherits whatever the parent bound, via
                        PAGEINDEX_LOG_CONTEXT (see obs/constants.py)
  child, later:        binds doc_sha8 (after sha256 is computed), then
                        doc_id (at persist) -- both LATE-ARRIVING
                        enrichments carried only by a document's later
                        records.

So resolution is two-pass:

  Pass 1: find any record carrying the identifier the caller supplied
          (doc_id, doc_sha8, or doc_name). Read that record's run_id and
          whichever of job_id/doc_name it carries -- that pair is the
          document's real spanning key.
  Pass 2: return every record in the file that shares that (run_id,
          job_id) or (run_id, doc_name) key -- including records that
          predate the identifier entirely (the parent-side records, which
          carry neither doc_sha8 nor doc_id).

A naive "start from the first record carrying doc_id" implementation
silently drops the document's whole first half. That is the specific
failure this module exists to avoid.

Ambiguity: if the supplied identifier's key resolves to more than one
distinct (run_id, job_id-or-doc_name) pair -- e.g. the same doc_name
reused across two different runs -- that is reported as an error listing
every candidate, never silently merged or silently picked.

Malformed lines (a library writing straight to stderr, mid-file) are
normal, not fatal: skipped and counted.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Envelope fields that can identify a document's spanning key (R12.3).
_KEY_FIELDS_BY_ROUTE: dict[str, tuple[str, str]] = {
    "worker": ("run_id", "job_id"),
    "batch": ("run_id", "doc_name_sha8"),
}

#: Identifiers that resolve to a spanning key via a two-pass lookup, rather
#: than being part of the key themselves (R12.3: late-arriving enrichments).
_RESOLVABLE_IDENTIFIER_FIELDS: tuple[str, ...] = ("doc_id", "doc_sha8", "doc_name_sha8")


def _hash_doc_name(name: str | None) -> str | None:
    """Digest a --doc-name argument the same way the emitter did.

    Imported from the production helper rather than reimplemented: if the two
    ever disagreed, every --doc-name query would silently match nothing.
    """
    if name is None:
        return None
    try:
        from pageindex_mcp.obs.redact import hash_doc_name
    except ImportError:  # pragma: no cover - logtrace must run without the package
        import hashlib

        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return hash_doc_name(name)


_DECISION_KIND = "decision"
_PHASE_ENTRY_KIND = "phase_entry"
_PHASE_EXIT_KIND = "phase_exit"


class LogTraceError(Exception):
    """Base class for user-facing logtrace failures."""


class IdentifierNotFoundError(LogTraceError):
    """Raised when the supplied identifier matches no record in the file,
    including --doc-name against a pre-2026-09-18 worker-route log (where
    job_id, and not doc_name, was bound) -- that must fail clearly rather
    than silently returning an empty trace."""


class AmbiguousIdentifierError(LogTraceError):
    """Raised when the supplied identifier resolves to more than one
    distinct (run_id, job_id-or-doc_name) key -- e.g. the same doc_name
    reused across two separate runs. Lists every candidate key."""


@dataclass(frozen=True)
class TraceResult:
    """One document's resolved, ordered record sequence."""

    records: list[dict[str, Any]] = field(default_factory=list)
    malformed_line_count: int = 0


def _iter_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse every JSON-line record in ``path``, in file order.

    A non-JSON line, or a JSON line that is not an object, is skipped and
    counted rather than raising -- a library writing straight to stderr
    mid-file is the normal case, not a fatal one.
    """
    records: list[dict[str, Any]] = []
    malformed = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(parsed, dict):
                malformed += 1
                continue
            parsed["_line_number"] = line_number
            records.append(parsed)
    return records, malformed


def _matching_key_field(record: dict[str, Any]) -> str | None:
    """Which of the two route key-fields (job_id / doc_name) this record
    carries, or None if it carries neither (a record with only run_id
    bound, e.g. before route selection)."""
    for _route, (_run_field, secondary_field) in _KEY_FIELDS_BY_ROUTE.items():
        if record.get(secondary_field) is not None:
            return secondary_field
    return None


def _candidate_keys(
    records: list[dict[str, Any]], identifier_field: str, identifier_value: str
) -> set[tuple[str | None, str, str | None]]:
    """Pass 1: every distinct (run_id, secondary_field_name, secondary_value)
    key carried by a record that matches the supplied identifier."""
    keys: set[tuple[str | None, str, str | None]] = set()
    for record in records:
        if record.get(identifier_field) != identifier_value:
            continue
        secondary_field = _matching_key_field(record)
        if secondary_field is None:
            # A record carrying the identifier but neither job_id nor
            # doc_name is not itself enough to name a spanning key; skip
            # it for key discovery (it will still be included in pass 2
            # once the key is known, since run_id alone still filters).
            continue
        keys.add((record.get("run_id"), secondary_field, record.get(secondary_field)))
    return keys


def _record_matches_key(
    record: dict[str, Any], run_id: str | None, secondary_field: str, secondary_value: str | None
) -> bool:
    return record.get("run_id") == run_id and record.get(secondary_field) == secondary_value


def _resolve_by_identifier(
    records: list[dict[str, Any]],
    identifier_field: str,
    identifier_value: str,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Two-pass resolution for doc_id / doc_sha8 / doc_name (R12.3).

    When ``run_id`` is supplied it narrows pass 1's candidate keys before
    the ambiguity check, so an explicit --run-id disambiguates rather than
    always erroring out on a repeated doc_name across runs.
    """
    candidates = _candidate_keys(records, identifier_field, identifier_value)
    if run_id is not None:
        candidates = {c for c in candidates if c[0] == run_id}

    if not candidates:
        if identifier_field == "doc_name_sha8":
            raise IdentifierNotFoundError(
                f"no record carries doc_name_sha8={identifier_value!r} "
                "(the digest of the --doc-name you gave; filenames are hashed "
                "before they are logged, see obs/redact.py:hash_doc_name). "
                "Logs written before 2026-09-18 by the arq worker route carry "
                "no doc_name at all (the key was run_id + job_id) -- for those, "
                "query by --job-id, --doc-id or --doc-sha8 instead."
            )
        raise IdentifierNotFoundError(f"no record carries {identifier_field}={identifier_value!r}")

    if len(candidates) > 1:
        described = ", ".join(
            f"(run_id={run_id!r}, {field_name}={value!r})"
            for run_id, field_name, value in sorted(
                candidates, key=lambda item: (str(item[0]), item[1], str(item[2]))
            )
        )
        raise AmbiguousIdentifierError(
            f"{identifier_field}={identifier_value!r} matches more than one "
            f"run; candidates: {described}. Disambiguate with --run-id."
        )

    (run_id, secondary_field, secondary_value) = next(iter(candidates))
    return [
        record
        for record in records
        if _record_matches_key(record, run_id, secondary_field, secondary_value)
    ]


def _resolve_by_explicit_key(
    records: list[dict[str, Any]],
    run_id: str | None,
    job_id: str | None,
) -> list[dict[str, Any]]:
    """Direct (run_id, job_id) lookup -- used when the caller already knows
    the worker-route key rather than an identifier that needs resolving."""
    # run_id narrows only when supplied. Requiring equality unconditionally
    # made `--job-id` alone match nothing (every record has a real run_id,
    # the filter compared it against None), contradicting this tool's own
    # --help, which offers --run-id as a disambiguator rather than a
    # requirement.
    matched = [
        record
        for record in records
        if (run_id is None or record.get("run_id") == run_id) and record.get("job_id") == job_id
    ]
    if not matched:
        described = f"job_id={job_id!r}"
        if run_id is not None:
            described = f"run_id={run_id!r}, {described}"
        raise IdentifierNotFoundError(f"no record carries {described}")
    return matched


def resolve_trace(
    path: Path,
    *,
    doc_id: str | None = None,
    doc_sha8: str | None = None,
    doc_name: str | None = None,
    job_id: str | None = None,
    run_id: str | None = None,
) -> TraceResult:
    """Resolve one document's ordered record sequence from the log at
    ``path``.

    Exactly one of ``doc_id``, ``doc_sha8``, ``doc_name`` or ``job_id`` must
    identify the document; ``run_id`` is an optional disambiguator (required
    when the bare identifier is ambiguous across runs).
    """
    identifier_fields = [
        (name, value)
        for name, value in (
            ("doc_id", doc_id),
            ("doc_sha8", doc_sha8),
            ("doc_name_sha8", _hash_doc_name(doc_name)),
            ("job_id", job_id),
        )
        if value is not None
    ]
    if not identifier_fields:
        raise ValueError(
            "resolve_trace() requires one of doc_id, doc_sha8, doc_name or "
            "job_id to identify the document"
        )

    records, malformed = _iter_records(path)

    identifier_field, identifier_value = identifier_fields[0]

    if identifier_field == "job_id":
        matched = _resolve_by_explicit_key(records, run_id=run_id, job_id=job_id)
    else:
        matched = _resolve_by_identifier(records, identifier_field, identifier_value, run_id=run_id)

    ordered = sorted(matched, key=lambda record: record["_line_number"])
    cleaned = [
        {key: value for key, value in record.items() if key != "_line_number"} for record in ordered
    ]
    return TraceResult(records=cleaned, malformed_line_count=malformed)


def render_human(result: TraceResult) -> str:
    """A readable, one-line-per-record rendering: phase entry/exit with
    durations, decision records, and plain log lines in between."""
    lines: list[str] = []
    for record in result.records:
        kind = record.get("kind")
        ts = record.get("ts", "?")
        if kind == _PHASE_ENTRY_KIND:
            lines.append(f"{ts}  -> phase {record.get('phase')} (seq={record.get('phase_seq')})")
        elif kind == _PHASE_EXIT_KIND:
            lines.append(
                f"{ts}  <- phase {record.get('phase')} "
                f"(seq={record.get('phase_seq')}, dur_ms={record.get('dur_ms')})"
            )
        elif kind == _DECISION_KIND:
            lines.append(
                f"{ts}  decision {record.get('event')}: choice={record.get('choice')!r} "
                f"reason={record.get('reason')!r}"
            )
        else:
            lines.append(f"{ts}  [{record.get('level', '?')}] {record.get('msg', '')}")
    if result.malformed_line_count:
        lines.append(f"({result.malformed_line_count} malformed line(s) skipped)")
    return "\n".join(lines)


def render_json(result: TraceResult) -> str:
    """The resolved records as a JSON array, in order -- machine-consumable."""
    return json.dumps(result.records, ensure_ascii=False, indent=2)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruct one document's ordered record sequence from a "
            "captured JSON-lines log file (RFC-046 D12)."
        )
    )
    parser.add_argument("logfile", type=Path, help="path to the captured log file")
    identifier_group = parser.add_mutually_exclusive_group(required=True)
    identifier_group.add_argument("--doc-id", help="resolve by doc_id")
    identifier_group.add_argument("--doc-sha8", help="resolve by doc_sha8")
    identifier_group.add_argument("--doc-name", help="resolve by doc_name (batch route only)")
    identifier_group.add_argument("--job-id", help="resolve by job_id (worker route)")
    parser.add_argument(
        "--run-id", default=None, help="disambiguate when the identifier matches multiple runs"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON instead of text"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        result = resolve_trace(
            args.logfile,
            doc_id=args.doc_id,
            doc_sha8=args.doc_sha8,
            doc_name=args.doc_name,
            job_id=args.job_id,
            run_id=args.run_id,
        )
    except LogTraceError as exc:
        print(f"logtrace: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"logtrace: no such file: {args.logfile}", file=sys.stderr)
        return 1

    print(render_json(result) if args.json else render_human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
