"""RFC-052 R8 AC5 table-search evaluation (task 9.5): pocketbook table Q&A
with vs without table nodes in the search view.

Runs ``pageindex_mcp.helpers.rag._search_one_doc`` per the design's "runs
`_search_one_doc` twice per arm": each arm (``TABLES_IN_SEARCH`` 0/1,
temperature 0) is run TWICE end to end over the whole question set, so every
question gets two independent search calls per arm (13 questions x 2 arms x
2 runs = 52 calls for the shipped question set). The search LLM call
(``_llm`` in ``helpers/rag.py``) already pins ``temperature=0``
unconditionally, so there is nothing for this script to override there.

Scoring (design "Evaluation (9.5, R8 AC5)"): a hit is the normalized expected
answer contained in the returned context text (the concatenated matched
node(s) ``text``). Normalizing casefolds, strips diacritics, collapses
whitespace and removes thousands-separator spaces between digits, so
"96 698 005" and "96698005" count as the same hit. A question's SCORED hit
for an arm is conservative: both of that arm's two runs must hit, so a
one-off flaky search does not count as a pass. Per-run hits are still kept
and reported separately, along with a flakiness column (the two runs
disagreed) so a reviewer can tell "never found it" apart from "found it
once."

Pass rule: hits(on) >= hits(off), with at most one per-question regression
(a question the "off" arm hit -- both runs -- and the "on" arm missed).

Output: ``audit/RFC052_TABLE_SEARCH_EVAL_<date>.md`` (``--out`` overrides),
including estimated prompt tokens per arm -- the ``_search_view`` tree size
in tokens (tiktoken ``o200k_base`` if installed, else chars/4: the same
counting rule and fallback R8 AC3 uses for the token budget itself). This is
the added search-view size, not a full accounting of the LLM prompt (system
message, question text, etc.), which is fixed across arms and cancels out in
the on-vs-off token delta the design cares about.

Needs a document already ingested through the normal pipeline, so its tree
has table nodes to compare against. Two ways to point at one:

  --doc-id <doc_id>     fetched via ``storage.documents.load_doc`` (MinIO)
  --doc-json <path>     a local ``processed/<doc_id>.json`` copy, for an
                         offline compare with no MinIO/Postgres reachable

``--dry-run`` only parses and validates the question set and prints the plan
(arms, question count, per-question preview) -- no document load, no LLM
call, no network of any kind.

Usage::

    # Validate the question set only (safe anywhere, including this host):
    uv run python scripts/table_search_eval.py --dry-run

    # Live run (RFC-052 orchestrator, on a host with LLM + storage access):
    uv run python scripts/table_search_eval.py \\
        --doc-id <pocketbook doc_id> \\
        --questions evals/table_questions/pocketbook.yaml

    # Live run against a local processed JSON copy instead of MinIO:
    uv run python scripts/table_search_eval.py \\
        --doc-json /path/to/<doc_id>.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_QUESTIONS = Path("evals/table_questions/pocketbook.yaml")
REQUIRED_FIELDS = ("id", "question", "answer", "answer_page", "table_hint")
#: R8 AC5: the question set must have at least this many entries.
MIN_QUESTIONS = 10
#: R8 AC5: on-vs-off may regress at most this many previously-hit questions.
MAX_REGRESSIONS = 1


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answer: str
    answer_page: int
    table_hint: str


@dataclass
class QuestionResult:
    question_id: str
    hit: bool  # conservative: True only if BOTH runs hit
    hit_run1: bool
    hit_run2: bool
    text_chars_run1: int
    text_chars_run2: int
    doc_name: str | None

    @property
    def flaky(self) -> bool:
        """The two runs of this arm disagreed on this question."""
        return self.hit_run1 != self.hit_run2


@dataclass
class ArmResult:
    arm: str  # "off" or "on"
    tables_in_search: bool
    results: list[QuestionResult] = field(default_factory=list)
    search_view_tokens: int = 0

    @property
    def hits(self) -> int:
        return sum(1 for r in self.results if r.hit)


def load_questions(path: Path) -> list[Question]:
    """Parse and validate the YAML question set. Raises ValueError on any schema defect."""
    if not path.exists():
        raise ValueError(f"{path}: not found")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        raise ValueError(f"{path}: expected a top-level 'questions' list")
    entries = raw["questions"]
    if len(entries) < MIN_QUESTIONS:
        raise ValueError(f"{path}: {len(entries)} question(s), need >= {MIN_QUESTIONS} (R8 AC5)")
    seen_ids: set[str] = set()
    questions: list[Question] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry {i} is not a mapping: {entry!r}")
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"{path}: entry {i} missing field(s) {missing}: {entry}")
        qid = str(entry["id"])
        if qid in seen_ids:
            raise ValueError(f"{path}: duplicate id {qid!r}")
        seen_ids.add(qid)
        if not str(entry["question"]).strip():
            raise ValueError(f"{path}: entry {qid!r} has an empty question")
        if not str(entry["answer"]).strip():
            raise ValueError(f"{path}: entry {qid!r} has an empty answer")
        try:
            page = int(entry["answer_page"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: entry {qid!r} answer_page must be an int") from exc
        if page < 0:
            raise ValueError(f"{path}: entry {qid!r} answer_page must be >= 0")
        if not str(entry["table_hint"]).strip():
            raise ValueError(f"{path}: entry {qid!r} has an empty table_hint")
        questions.append(
            Question(
                id=qid,
                question=str(entry["question"]),
                answer=str(entry["answer"]),
                answer_page=page,
                table_hint=str(entry["table_hint"]),
            )
        )
    return questions


_WS = re.compile(r"\s+")
_DIGIT_GROUP_SPACE = re.compile(r"(?<=\d)[  ](?=\d)")


def normalize(text: str) -> str:
    """Casefold, strip diacritics and thousands-separator spaces, collapse whitespace.

    Matches the design's scoring rule ("normalized answer contained in
    returned content") without being so loose it can't tell "218" apart from
    unrelated digits -- callers still do plain substring containment on top
    of this, so it only removes formatting noise, never numeric structure.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = _DIGIT_GROUP_SPACE.sub("", text)
    return _WS.sub(" ", text).strip()


def count_tokens(text: str) -> int:
    """tiktoken ``o200k_base`` if importable, else chars/4 (R8 AC3's own fallback rule)."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


async def run_arm(
    arm: str,
    tables_in_search: bool,
    doc_id: str,
    data: dict,
    questions: list[Question],
) -> ArmResult:
    """Run every question TWICE against ``data``, with ``TABLES_IN_SEARCH`` pinned for this arm.

    Design: "runs `_search_one_doc` twice per arm." A question's scored hit
    for this arm is conservative -- both runs must hit -- to avoid crediting
    a one-off flaky search; per-run hits and any run-to-run disagreement are
    kept in the ``QuestionResult`` for the report.

    One call at a time (semaphore of 1): the eval cares about per-question
    correctness, not search throughput, and a shared semaphore of 1 keeps
    the two arms' LLM call patterns identical for a fair token/latency read.
    """
    from pageindex_mcp.helpers.rag import _search_one_doc, _search_view

    os.environ["TABLES_IN_SEARCH"] = "1" if tables_in_search else "0"
    tree = data.get("structure", [])
    tree_slim, _view_stats = _search_view(tree, doc_id)
    result = ArmResult(
        arm=arm,
        tables_in_search=tables_in_search,
        search_view_tokens=count_tokens(json.dumps(tree_slim, indent=2)),
    )
    sem = asyncio.Semaphore(1)
    for q in questions:
        run_hits: list[bool] = []
        run_chars: list[int] = []
        name: str | None = None
        for _run_i in range(2):
            found = await _search_one_doc(q.question, doc_id, data, sem)
            text = found[2] if found else ""
            name = found[1] if found else name
            run_hits.append(normalize(q.answer) in normalize(text))
            run_chars.append(len(text))
        result.results.append(
            QuestionResult(
                question_id=q.id,
                hit=run_hits[0] and run_hits[1],
                hit_run1=run_hits[0],
                hit_run2=run_hits[1],
                text_chars_run1=run_chars[0],
                text_chars_run2=run_chars[1],
                doc_name=name,
            )
        )
    return result


def evaluate(off: ArmResult, on: ArmResult) -> tuple[str, list[str]]:
    """R8 AC5 pass rule: hits(on) >= hits(off), at most one per-question regression."""
    regressions = [
        r_off.question_id
        for r_off, r_on in zip(off.results, on.results, strict=True)
        if r_off.hit and not r_on.hit
    ]
    verdict = "PASS" if on.hits >= off.hits and len(regressions) <= MAX_REGRESSIONS else "FAIL"
    return verdict, regressions


def write_report(  # noqa: PLR0913
    out: Path,
    *,
    questions: list[Question],
    doc_id: str,
    off: ArmResult,
    on: ArmResult,
    verdict: str,
    regressions: list[str],
) -> None:
    lines = [
        f"# RFC-052 table search eval ({_dt.date.today().isoformat()})",
        "",
        f"Document: `{doc_id}` · questions: {len(questions)} · "
        "script: `scripts/table_search_eval.py`",
        "",
        f"**Result: {verdict}** -- hits off={off.hits}/{len(questions)}, "
        f"on={on.hits}/{len(questions)}, regressions={len(regressions)} "
        f"(gate: on >= off, <= {MAX_REGRESSIONS} regression)",
        "",
        f"Prompt tokens (search-view size only, added over `_strip_text`): "
        f"off={off.search_view_tokens}, on={on.search_view_tokens}, "
        f"delta={on.search_view_tokens - off.search_view_tokens}",
        "",
        "Hit = both of an arm's two runs hit (conservative). `flaky` marks a "
        "question where an arm's two runs disagreed.",
        "",
        "| id | question | answer | page | off hit | off runs | off flaky "
        "| on hit | on runs | on flaky | table_hint |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    by_id_off = {r.question_id: r for r in off.results}
    by_id_on = {r.question_id: r for r in on.results}
    for q in questions:
        r_off = by_id_off.get(q.id)
        r_on = by_id_on.get(q.id)
        off_runs = f"{r_off.hit_run1}/{r_off.hit_run2}" if r_off else "--"
        on_runs = f"{r_on.hit_run1}/{r_on.hit_run2}" if r_on else "--"
        lines.append(
            f"| {q.id} | {q.question} | {q.answer} | {q.answer_page} "
            f"| {bool(r_off and r_off.hit)} | {off_runs} | {bool(r_off and r_off.flaky)} "
            f"| {bool(r_on and r_on.hit)} | {on_runs} | {bool(r_on and r_on.flaky)} "
            f"| {q.table_hint} |"
        )
    lines += [
        "",
        "## Regressions (off hit, on missed)",
        "",
    ]
    lines += [f"- {r}" for r in regressions] or ["- none"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


def _print_plan(questions: list[Question]) -> None:
    print(f"{len(questions)} question(s), OK")
    print("plan:")
    print("  arm off: TABLES_IN_SEARCH=0 (today's _strip_text view), run twice")
    print("  arm on:  TABLES_IN_SEARCH=1 (table entries added), run twice")
    print(
        f"  {len(questions)} question(s) x 2 arms x 2 runs = "
        f"{len(questions) * 4} _search_one_doc calls"
    )
    print("  a question's scored hit per arm requires both of that arm's runs to hit")
    for q in questions:
        print(f"  - [{q.id}] p{q.answer_page}: {q.question!r} -> {q.answer!r} ({q.table_hint})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument(
        "--doc-id",
        help="doc_id of an already-ingested document (fetched via storage.documents.load_doc "
        "unless --doc-json is also given)",
    )
    ap.add_argument(
        "--doc-json",
        type=Path,
        help="local processed/<doc_id>.json for an offline compare (no MinIO/Postgres needed)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(f"audit/RFC052_TABLE_SEARCH_EVAL_{_dt.date.today().isoformat()}.md"),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the question set and print the plan; no document load, no LLM call",
    )
    args = ap.parse_args(argv)

    try:
        questions = load_questions(args.questions)
    except ValueError as exc:
        print(f"invalid question set: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        _print_plan(questions)
        if not (args.doc_id or args.doc_json):
            print(
                "note: no --doc-id/--doc-json given -- a live run additionally needs one",
                file=sys.stderr,
            )
        return 0

    if not args.doc_id and not args.doc_json:
        ap.error("a live run needs --doc-id and/or --doc-json")

    if args.doc_json:
        data = json.loads(args.doc_json.read_text())
        doc_id = args.doc_id or data.get("doc_id") or args.doc_json.stem
    else:
        from pageindex_mcp.storage.documents import load_doc

        doc_id = args.doc_id
        data = load_doc(doc_id)

    off = asyncio.run(run_arm("off", False, doc_id, data, questions))
    on = asyncio.run(run_arm("on", True, doc_id, data, questions))
    verdict, regressions = evaluate(off, on)
    write_report(
        args.out,
        questions=questions,
        doc_id=doc_id,
        off=off,
        on=on,
        verdict=verdict,
        regressions=regressions,
    )
    print(f"{verdict}: hits off={off.hits} on={on.hits} regressions={regressions}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
