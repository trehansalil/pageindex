#!/usr/bin/env python3
"""g1_stage_timings -- RFC-050 gate G1 stage-timing report from worker logs.

Read-only, stdlib only. RFC-050 decision: stage timings are read from the
logs for now; the Prometheus scrape of ``pageindex_stage_duration_seconds``
is deferred to Phase 3.

Source records (RFC-050 Task 1.5): ``CustomPageIndexClient._emit_stage_timings``
(client/indexer.py) emits one ``decision()`` record per stage reached, per
non-deduped document, rendered by ``obs.formatter.JsonFormatter`` as one JSON
line on stderr (the converter child's stderr is forwarded verbatim to the
worker's stderr by worker/subprocess_mgr.py)::

    {"v": 1, ..., "kind": "decision", ..., "event": "stage_duration",
     "choice": "extraction", ..., "attrs": {"duration_ms": 41230}, ...}

Stages are DISJOINT (extraction / tree_build / recovery): tree_build is every
md_to_tree / page_index LLM call, subtracted from the wall time of the stage
it is nested in. The per-document ``doc_total`` row is the sum of the stages
one document reached.

LLM pressure signals are counted per LINE (one 429 typically produces
several lines -- the vendored pageindex prints a retry banner AND logs the
error), so compare them between arms; do not read them as request counts:

  rate_limit     429 / "rate limit" / RateLimitError in the message
  retry_client   client/llm.py  "LLM transient error (attempt n/N ..."
  retry_vendored pageindex/utils.py llm_(a)completion "***** Retrying *****"
  fallback       client/llm.py  "Primary LLM exhausted ... trying fallback"
  exhausted      pageindex/utils.py "Max retries reached"

Usage::

    scripts/g1_stage_timings.py worker.log                 # one group
    scripts/g1_stage_timings.py --label baseline a.log b.log
    scripts/g1_stage_timings.py baseline=a.log baseline=b.log post=c.log post=d.log
    kubectl logs deploy/pageindex-worker | scripts/g1_stage_timings.py -

With two or more labels the report ends with a G1 line: the change in median
per-document total from the first label to the last (G1 passes at >= 30%
reduction, 2 runs per arm; an arm with fewer runs is INVALID, never PASS).
Exit 1 when no complete stage_duration record was found; exit 2 when the G1
line is INVALID or FAIL; 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator

STAGES = ("extraction", "tree_build", "recovery")
DOC_TOTAL = "doc_total"
G1_THRESHOLD = 0.30
G1_MIN_RUNS = 2

_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rate_limit",
        re.compile(
            r"rate[ _-]?limit|RateLimitError|status=429|Error code: 429|\b429 Too Many",
            re.IGNORECASE,
        ),
    ),
    ("retry_client", re.compile(r"LLM transient error \(attempt")),
    ("retry_vendored", re.compile(r"\*+ Retrying \*+")),
    ("fallback", re.compile(r"Primary LLM exhausted")),
    ("exhausted", re.compile(r"Max retries reached")),
)
SIGNAL_NAMES = tuple(name for name, _ in _SIGNALS)


def _json_record(line: str) -> dict | None:
    """The JSON envelope in *line*, tolerating a prefix such as
    ``kubectl logs --prefix`` or a tee tag before the ``{``."""
    start = line.find("{")
    if start < 0:
        return None
    try:
        rec = json.loads(line[start:])
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _signal_text(line: str, rec: dict | None) -> str:
    """Text scanned for LLM signals. For a JSON record only msg + exc.stack,
    so a ``"proc": 429`` or a ``.429Z`` timestamp never counts as a 429."""
    if rec is None:
        return line
    exc = rec.get("exc")
    stack = exc.get("stack", "") if isinstance(exc, dict) else ""
    return f"{rec.get('msg', '')}\n{stack}"


def _doc_key(rec: dict, run_idx: int) -> tuple:
    """One document within one run file. doc_sha8 is bound before any stage
    completes; job_id / proc are fallbacks for records that lack it."""
    sha8 = rec.get("doc_sha8")
    return (
        run_idx,
        rec.get("run_id"),
        rec.get("job_id"),
        sha8,
        None if sha8 else rec.get("doc_id"),
        None if (sha8 or rec.get("job_id")) else rec.get("proc"),
    )


class Group:
    """All runs sharing one label (one arm of the comparison).

    A document counts toward ``doc_total`` (and so G1) only when it has an
    ``extraction`` record: ``_emit_stage_timings`` runs in a ``finally`` and
    always emits ``tree_build`` (0 when never reached), so a document that
    failed before extraction would otherwise enter the median as a
    near-zero runtime and fake a speed-up. A source file is a run only when
    it contributed at least one complete document -- an empty or failed log
    must not satisfy ``G1_MIN_RUNS``.
    """

    def __init__(self) -> None:
        self.sources = 0
        self.samples: dict[str, list[float]] = defaultdict(list)
        self._doc_s: dict[tuple, float] = defaultdict(float)
        self._doc_stages: dict[tuple, set[str]] = defaultdict(set)
        self.signals: dict[str, int] = dict.fromkeys(SIGNAL_NAMES, 0)

    def feed(self, lines: Iterable[str], run_idx: int) -> None:
        self.sources += 1
        for line in lines:
            rec = _json_record(line)
            if rec is not None and rec.get("event") == "stage_duration":
                self._add_stage(rec, run_idx)
                continue
            text = _signal_text(line, rec)
            for name, pattern in _SIGNALS:
                if pattern.search(text):
                    self.signals[name] += 1

    def _add_stage(self, rec: dict, run_idx: int) -> None:
        stage = rec.get("choice")
        attrs = rec.get("attrs")
        if stage not in STAGES or not isinstance(attrs, dict):
            return
        try:
            seconds = float(attrs.get("duration_ms")) / 1000.0
        except (TypeError, ValueError):
            return
        self.samples[stage].append(seconds)
        key = _doc_key(rec, run_idx)
        self._doc_s[key] += seconds
        self._doc_stages[key].add(stage)

    @property
    def docs(self) -> dict[tuple, float]:
        """Complete documents only (see the class docstring)."""
        return {k: v for k, v in self._doc_s.items() if "extraction" in self._doc_stages[k]}

    @property
    def incomplete(self) -> int:
        return len(self._doc_s) - len(self.docs)

    @property
    def runs(self) -> int:
        return len({key[0] for key in self.docs})

    def rows(self) -> Iterator[tuple[str, list[float]]]:
        for stage in STAGES:
            yield stage, self.samples.get(stage, [])
        yield DOC_TOTAL, list(self.docs.values())

    def doc_median(self) -> float | None:
        values = list(self.docs.values())
        return statistics.median(values) if values else None


def _p90(values: list[float]) -> float:
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def _fmt(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds:.1f}"


def _parse_sources(logs: list[str], default_label: str) -> list[tuple[str, str]]:
    sources = []
    for item in logs or ["-"]:
        label, sep, path = item.partition("=")
        if not sep or not label or "/" in label:
            label, path = default_label, item
        sources.append((label, path))
    return sources


def build(sources: list[tuple[str, str]]) -> dict[str, Group]:
    groups: dict[str, Group] = {}
    for run_idx, (label, path) in enumerate(sources):
        group = groups.setdefault(label, Group())
        if path == "-":
            group.feed(sys.stdin, run_idx)
        else:
            with open(path, encoding="utf-8", errors="replace") as fh:
                group.feed(fh, run_idx)
    return groups


def _g1_verdict(groups: dict[str, Group]) -> tuple[str, str]:
    """(verdict, G1 line). verdict is PASS, FAIL or INVALID; only PASS/FAIL
    are G1 readings -- an arm with < G1_MIN_RUNS runs is INVALID, never PASS."""
    labels = list(groups)
    first, last = labels[0], labels[-1]
    base, post = groups[first].doc_median(), groups[last].doc_median()
    if not base or post is None:
        return "INVALID", "G1: INVALID -- an arm has no complete stage_duration records"
    change = (base - post) / base
    runs = min(groups[first].runs, groups[last].runs)
    if runs < G1_MIN_RUNS:
        verdict = "INVALID"
        detail = f"INVALID (an arm has {runs} < {G1_MIN_RUNS} runs -- not a G1 reading)"
    else:
        verdict = detail = "PASS" if change >= G1_THRESHOLD else "FAIL"
    return verdict, (
        f"G1: median {DOC_TOTAL} {first} {_fmt(base)}s -> {last} {_fmt(post)}s "
        f"= {change:.1%} reduction; threshold {G1_THRESHOLD:.0%} -> {detail}"
    )


def render(groups: dict[str, Group]) -> tuple[str, str | None]:
    """(report, G1 verdict or None when fewer than two labels)."""
    out = [
        "| label | runs | stage | n | median s | p90 s | total s |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, group in groups.items():
        for stage, values in group.rows():
            if values:
                med, p90, tot = statistics.median(values), _p90(values), sum(values)
                out.append(
                    f"| {label} | {group.runs} | {stage} | {len(values)} "
                    f"| {_fmt(med)} | {_fmt(p90)} | {_fmt(tot)} |"
                )
            else:
                out.append(f"| {label} | {group.runs} | {stage} | 0 | - | - | - |")
    out += [
        "",
        "| label | " + " | ".join(SIGNAL_NAMES) + " |",
        "|---" * (len(SIGNAL_NAMES) + 1) + "|",
    ]
    for label, group in groups.items():
        out.append(f"| {label} | " + " | ".join(str(group.signals[n]) for n in SIGNAL_NAMES) + " |")
    for label, group in groups.items():
        if group.incomplete or group.sources != group.runs:
            out.append(
                f"\n{label}: excluded {group.incomplete} doc(s) with no extraction record; "
                f"{group.sources - group.runs} of {group.sources} source(s) had no complete doc"
            )
    verdict = None
    if len(groups) >= 2:
        verdict, line = _g1_verdict(groups)
        out += ["", line]
    return "\n".join(out), verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RFC-050 G1: per-stage timings + LLM rate-limit signals from worker JSON logs."
    )
    parser.add_argument(
        "logs",
        nargs="*",
        help="log files, '-' for stdin, or LABEL=path to group runs (default: stdin)",
    )
    parser.add_argument(
        "--label", default="all", help="label for files given without LABEL= (default: all)"
    )
    args = parser.parse_args(argv)
    groups = build(_parse_sources(args.logs, args.label))
    report, verdict = render(groups)
    print(report)
    if not any(group.docs for group in groups.values()):
        return 1
    return 2 if verdict in ("INVALID", "FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
