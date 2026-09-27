"""RFC-052 R4 conversion benchmark (task 5.5): TableFormer and OCR levers.

Drives ``POST /convert/pdf`` on the ACTIVE REMOTE Docling backend directly,
with each arm chosen by the per-request overrides the service accepts
(``tableformer_mode``, ``pageclass_chunking``, ``do_ocr_policy``):

  =========  ==================  =============  ================
  arm        pageclass_chunking  do_ocr_policy  tableformer_mode
  =========  ==================  =============  ================
  baseline   off (uniform)       force_on       accurate
  r3         on                  page_class     accurate
  r3_fast    on                  page_class     fast
  =========  ==================  =============  ================

``baseline`` pins today's remote settings explicitly (``DOCLING_DO_OCR=1`` on
docling-1 and the Mac), so the arm means the same thing whatever the
backend's env says. Every arm sends the same page classes and table pages the
worker would send, computed once per document with the production classifier.

Fixed plan (R4 AC2): exactly three documents -- ``--pocketbook``, ``--scanned``
(a scanned or image-heavy PDF) and ``--arabic`` (an Arabic or garble-prone
PDF) -- x three arms x ``--runs`` (default 3) TIMED runs each, plus one
discarded warm-up run per arm per document so a cold model/converter cache
never pollutes run 1's timing; medians are reported over the timed runs.
Per arm (R4 AC3): extraction seconds and seconds per page, the
``validate_tree`` verdict on a heading tree built without an LLM
(``_md_to_structure``), the garble screen, and a table-cell diff against the
baseline arm (cell-count delta and changed-cell ratio over every markdown
table cell -- the markdown carries no page numbers, and the pocketbook's
tables all sit on its 262 table pages).

R4 AC4 gate (task 5.5 fixer round 1): the gate INPUT is r3_fast vs r3 -- the
FAST effect in isolation -- not vs baseline, since baseline also differs in
chunking/OCR policy and would conflate the TableFormer-mode effect with
those. Baseline comparisons stay in the report as context. A baseline-vs-
baseline diff between the first two timed runs is reported as the noise
floor: a same-settings diff of comparable size to the r3_fast-vs-r3 diff
means the "effect" may just be run-to-run noise. FAST becomes the default
only if, on every document, the changed-cell ratio (r3_fast vs r3) is <= 2%,
no verdict gets worse and garble does not increase (tolerant of float noise:
a garble-ratio increase must exceed ``GARBLE_NOISE_TOLERANCE`` to count). The
script only REPORTS the gate; changing ``DOCLING_TABLEFORMER_MODE``'s default
is a separate, reviewed change.

Before running any arm, the script GETs ``/version`` and refuses to run
unless the remote build advertises ``bench_overrides_supported`` -- an older
build silently ignores ``tableformer_mode``/``pageclass_chunking``/
``do_ocr_policy`` and would make every arm identical. After each call, the
response's ``applied`` dict (the RESOLVED settings the conversion actually
ran with) is checked against the arm's intended overrides; a mismatch aborts
that arm rather than silently reporting numbers for the wrong settings.

Never runs against portfolio (NG7): the backend must be named explicitly in
``DOCLING_BENCH_BASE_URL`` (or ``--base-url``), and loopback hosts or the
``docling-service-local`` Deployment are refused. PII corpora go only to a
ZDR-compliant backend (HR3), the same gate the worker applies. The staged
upload is deleted when the run ends (HR2). If one arm errors (build too old,
network failure, a bad conversion), the run continues with the remaining
arms/documents and still writes a PARTIAL report rather than aborting
everything already measured.

Usage::

    DOCLING_BENCH_BASE_URL=https://docling-1.example \\
    uv run python scripts/conversion_bench.py \\
        --pocketbook doc_store/world-stats-pocketbook-2023.pdf \\
        --scanned doc_store/<scanned>.pdf \\
        --arabic doc_store/<arabic>.pdf

The bearer token is ``DOCLING_BENCH_TOKEN``, else the worker's
``DOCLING_SERVICE_BEARER_TOKEN`` setting. The report lands in
``audit/RFC052_CONVERSION_BENCH_<date>.md`` (``--out`` overrides).

Needs MinIO (staging + presigned URL) and PyMuPDF (AGPL, HR4) for the page
classes; it never imports or runs Docling itself.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import ipaddress
import json
import os
import re
import socket
import statistics
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ARMS: dict[str, dict] = {
    "baseline": {
        "pageclass_chunking": False,
        "do_ocr_policy": "force_on",
        "tableformer_mode": "accurate",
    },
    "r3": {
        "pageclass_chunking": True,
        "do_ocr_policy": "page_class",
        "tableformer_mode": "accurate",
    },
    "r3_fast": {
        "pageclass_chunking": True,
        "do_ocr_policy": "page_class",
        "tableformer_mode": "fast",
    },
}
#: R4 AC4: the most table cells FAST may change against ACCURATE (r3_fast vs r3).
FAST_MAX_CHANGED_CELL_RATIO = 0.02
#: A garble-ratio increase must exceed this to count as a real regression,
#: not float noise from a non-deterministic conversion.
GARBLE_NOISE_TOLERANCE = 0.001
#: Host fragments that name portfolio's own Docling (NG7).
_PORTFOLIO_HOST_MARKERS = ("docling-service-local", "portfolio")
#: /version must report this truthy, or an older build silently ignores the
#: per-request overrides and every arm would run identically.
_OVERRIDES_VERSION_FLAG = "bench_overrides_supported"


@dataclass
class Doc:
    label: str
    path: Path
    expected_script: str | None = None
    page_count: int = 0
    page_classes: list | None = None  # run-length wire form
    pages_with_tables: list[int] | None = None


@dataclass
class RunResult:
    seconds: float
    markdown: str
    applied: dict | None = None


@dataclass
class ArmReport:
    doc: str
    arm: str
    runs: list[RunResult] = field(default_factory=list)
    verdict: str = ""
    verdicts: list[str] = field(default_factory=list)
    garbled: bool = False
    garble_ratio: float = 0.0
    cell_diff: dict | None = None  # vs baseline (context)
    cell_diff_vs_r3: dict | None = None  # r3_fast only: the gate input
    noise_floor: dict | None = None  # same-arm run-to-run diff (context)
    error: str | None = None

    @property
    def median_s(self) -> float:
        return statistics.median(r.seconds for r in self.runs)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def refuse_portfolio(base_url: str) -> None:
    """Raise unless ``base_url`` is a remote Docling backend (NG7).

    Mirrors ``services/docling-service/app.py:_refuse_private_url``, but only
    loopback / unspecified addresses and portfolio's own service names are
    refused: the active remote may sit on a tailnet or cluster address.
    """
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SystemExit(f"refusing {base_url!r}: not an http(s) URL")
    host = parts.hostname.lower()
    if host == "localhost" or any(m in host for m in _PORTFOLIO_HOST_MARKERS):
        raise SystemExit(f"refusing {base_url!r}: that is portfolio's Docling (RFC-052 NG7)")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SystemExit(f"refusing {base_url!r}: host does not resolve") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_loopback or addr.is_unspecified:
            raise SystemExit(f"refusing {base_url!r}: resolves to {addr} (portfolio, NG7)")


def require_hr3(base_url: str) -> None:
    """The worker's HR3 gate: a PII corpus goes only to a ZDR-compliant backend."""
    from pageindex_mcp.config import require_zdr_compliance, settings

    if settings.pii_corpus:
        require_zdr_compliance(base_url, "RFC-052 conversion benchmark")


def require_bench_overrides_supported(base_url: str, token: str, timeout_s: float = 30.0) -> None:
    """Refuse to run unless the remote build advertises the R4 overrides.

    An older build accepts and silently ignores ``tableformer_mode`` /
    ``pageclass_chunking`` / ``do_ocr_policy`` -- every arm would then hit
    identical settings and the whole bench would report a fake "no
    difference" result. Checked once, up front, via ``GET /version``.
    """
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.get(f"{base_url}/version", headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        info = resp.json()
    except Exception as exc:
        raise SystemExit(
            f"could not verify {base_url}/version supports bench overrides: {exc}"
        ) from exc
    if not info.get(_OVERRIDES_VERSION_FLAG):
        raise SystemExit(
            f"refusing to run: {base_url}/version does not advertise "
            f"{_OVERRIDES_VERSION_FLAG!r} (build={info}); this backend predates "
            "the R4 tableformer_mode/pageclass_chunking/do_ocr_policy overrides "
            "and every arm would silently run identically"
        )


class AppliedMismatchError(RuntimeError):
    """The service ran with different settings than the arm intended."""


def verify_applied(
    arm: str,
    doc_label: str,
    overrides: dict,
    applied: dict | None,
    expect_page_classes_active: bool = False,
) -> None:
    """Check the response's ``applied`` echo against the arm's intended overrides.

    ``None`` means an old build that does not echo ``applied`` at all --
    ``require_bench_overrides_supported`` should already have refused that
    backend, but a defensive check here still catches a build that
    advertises support without actually doing it.

    Finding 3 (RFC-052 P2): matching ``do_ocr_policy: "page_class"`` on its
    own is not proof anything ran page-class-driven -- with the R2 AC7 fix,
    inactive page classes ALSO resolve to a working (force-on) OCR policy, so
    the literal string would still match while the doc got every model on
    every page instead of the r3 arm's actual selective behaviour.
    ``expect_page_classes_active`` (true for r3/r3_fast when the document has
    real page classes to drive) closes that gap by checking the new
    ``applied["page_classes_active"]`` field directly.
    """
    if applied is None:
        raise AppliedMismatchError(
            f"[{doc_label}] {arm}: response carries no 'applied' echo -- cannot verify overrides"
        )
    mismatches = {k: (v, applied.get(k)) for k, v in overrides.items() if applied.get(k) != v}
    if mismatches:
        raise AppliedMismatchError(
            f"[{doc_label}] {arm}: applied settings do not match the requested arm: {mismatches}"
        )
    if expect_page_classes_active and not applied.get("page_classes_active"):
        raise AppliedMismatchError(
            f"[{doc_label}] {arm}: page classes were not actually active "
            f"(applied={applied}) -- this arm ran with every model on every "
            "page instead of page-class-driven selection"
        )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def classify(doc: Doc) -> None:
    """Page count, page classes and table pages, as the worker computes them."""
    import fitz  # PyMuPDF (AGPL, HR4) -- local, never served

    from pageindex_mcp.converters.preclassify import detect_page_classes, page_classes_to_ranges

    with fitz.open(doc.path) as pdf:
        doc.page_count = pdf.page_count
    classes, method = detect_page_classes(str(doc.path))
    if classes is None:
        print(f"[{doc.label}] page classification off or failed: every arm runs all models")
        return
    doc.page_classes = page_classes_to_ranges(classes)
    doc.pages_with_tables = [i for i, pc in enumerate(classes) if pc.has_tables]
    print(
        f"[{doc.label}] {doc.page_count} pages via {method}: "
        f"{len(doc.pages_with_tables)} need tables, {sum(pc.needs_ocr for pc in classes)} need OCR"
    )


# ---------------------------------------------------------------------------
# One arm
# ---------------------------------------------------------------------------


def run_arm(  # noqa: PLR0913
    base_url: str, token: str, doc: Doc, overrides: dict, staging_key: str, timeout_s: float
) -> RunResult:
    """One ``/convert/pdf`` call with the arm's overrides; wall-clock timed."""
    import httpx

    from pageindex_mcp.storage import presigned_get_url

    payload = {
        # Presigned per call: a URL expires long before a slow arm finishes.
        "presigned_url": presigned_get_url(staging_key),
        "pages_with_tables": doc.pages_with_tables,
        "page_classes": doc.page_classes,
        **overrides,
    }
    headers = {"X-Shard": f"1/1:0-{doc.page_count - 1}"} if doc.page_count else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.monotonic()
    resp = httpx.post(f"{base_url}/convert/pdf", json=payload, headers=headers, timeout=timeout_s)
    seconds = time.monotonic() - started
    resp.raise_for_status()
    body = resp.json()
    return RunResult(seconds=seconds, markdown=body["markdown"], applied=body.get("applied"))


def score(report: ArmReport, doc: Doc) -> None:
    """The ``validate_tree`` verdict and the garble screen, per run and overall.

    The tree is the heading tree ``_md_to_structure`` builds (no LLM, no
    summaries): the same structural gates the worker's HR5 check applies.
    """
    from pageindex_mcp.converters.headings import _md_to_structure
    from pageindex_mcp.helpers.tree_validation import validate_tree

    ratios: list[float] = []
    for run in report.runs:
        gate = validate_tree(
            _md_to_structure(run.markdown),
            expected_script=doc.expected_script,
            page_count=doc.page_count or None,
        )
        report.verdicts.append("PASS" if gate.ok else f"FAIL:{gate.defect}")
        sig = gate.signals
        report.garbled = report.garbled or bool(sig and sig.garbled)
        ratios.append(float(sig.garble_ratio) if sig else 0.0)
    # The worst run's verdict: a flaky FAIL must not hide behind a PASS.
    fails = [v for v in report.verdicts if v != "PASS"]
    report.verdict = fails[0] if fails else "PASS"
    report.garble_ratio = statistics.median(ratios) if ratios else 0.0


# ---------------------------------------------------------------------------
# Table-cell diff
# ---------------------------------------------------------------------------

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def table_cells(markdown: str) -> list[str]:
    """Every markdown table cell in document order (separator rows dropped)."""
    cells: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        row = [re.sub(r"\s+", " ", c.strip()) for c in line[1:-1].split("|")]
        if row and all(_SEPARATOR_CELL.match(c) for c in row if c):
            continue
        cells.extend(row)
    return cells


def table_cell_diff(baseline_md: str, arm_md: str) -> dict:
    """Cell-count delta and changed-cell ratio of ``arm_md`` against ``baseline_md``.

    Cells are aligned as sequences (``difflib``), so one inserted row does
    not count every later cell as changed. ``changed_cell_ratio`` is the
    share of the larger cell list not matched in order; 0.0 when neither
    side has a table. Despite the parameter names, this is a generic diff --
    used both against the baseline arm (context) and between two r3/r3_fast
    runs (the gate input, and the same-arm noise floor).
    """
    base, arm = table_cells(baseline_md), table_cells(arm_md)
    matched = sum(
        b.size
        for b in difflib.SequenceMatcher(None, base, arm, autojunk=False).get_matching_blocks()
    )
    total = max(len(base), len(arm))
    return {
        "baseline_cells": len(base),
        "arm_cells": len(arm),
        "cell_count_delta": len(arm) - len(base),
        "changed_cell_ratio": (1 - matched / total) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# R4 AC4 gate + report
# ---------------------------------------------------------------------------


def fast_gate(reports: dict[tuple[str, str], ArmReport], docs: list[Doc]) -> tuple[str, list[str]]:
    """R4 AC4: may FAST become the default?

    The gate input is r3_fast vs r3 (the FAST effect in isolation) -- NOT vs
    baseline, since baseline also differs in chunking/OCR policy and would
    conflate that effect with the TableFormer-mode change. Returns
    ``(verdict, notes)`` where ``verdict`` is ``"YES"``, ``"NO"`` or
    ``"INCONCLUSIVE"``; a missing r3/r3_fast report (that arm errored) is
    itself a reason the gate cannot pass (``"NO"``).

    Finding 4 (RFC-052 P2): the changed-cell-ratio check alone cannot tell a
    real TableFormer-mode effect from run-to-run non-determinism once the
    baseline-vs-baseline noise floor (same settings, two different runs, see
    ``noise_floor``) is already at or above ``FAST_MAX_CHANGED_CELL_RATIO`` --
    at that point the threshold itself cannot discriminate, so that document
    is ``INCONCLUSIVE`` rather than a hard ``NO``. The other two checks
    (verdict regression, garble increase) are unaffected: they are not
    ratio-threshold comparisons against a noisy baseline.
    """
    hard_reasons: list[str] = []
    inconclusive_notes: list[str] = []
    for doc in docs:
        r3 = reports.get((doc.label, "r3"))
        fast = reports.get((doc.label, "r3_fast"))
        if r3 is None or r3.error or fast is None or fast.error:
            hard_reasons.append(
                f"{doc.label}: r3 or r3_fast arm errored -- gate cannot be evaluated"
            )
            continue
        ratio = (fast.cell_diff_vs_r3 or {}).get("changed_cell_ratio", 0.0)
        if ratio > FAST_MAX_CHANGED_CELL_RATIO:
            baseline = reports.get((doc.label, "baseline"))
            noise = (baseline.noise_floor or {}).get("changed_cell_ratio") if baseline else None
            if noise is not None and noise >= FAST_MAX_CHANGED_CELL_RATIO:
                inconclusive_notes.append(
                    f"{doc.label}: changed-cell ratio (r3_fast vs r3) {ratio:.2%} "
                    f"> {FAST_MAX_CHANGED_CELL_RATIO:.0%}, but the baseline-vs-baseline "
                    f"noise floor is already {noise:.2%} -- INCONCLUSIVE, not a real "
                    "TableFormer-mode effect"
                )
            else:
                hard_reasons.append(
                    f"{doc.label}: changed-cell ratio (r3_fast vs r3) {ratio:.2%} "
                    f"> {FAST_MAX_CHANGED_CELL_RATIO:.0%}"
                )
        if r3.verdict == "PASS" and fast.verdict != "PASS":
            hard_reasons.append(f"{doc.label}: verdict worse ({r3.verdict} -> {fast.verdict})")
        noisy_ok = fast.garble_ratio <= r3.garble_ratio + GARBLE_NOISE_TOLERANCE
        if (fast.garbled and not r3.garbled) or not noisy_ok:
            hard_reasons.append(
                f"{doc.label}: garble increased "
                f"({r3.garble_ratio:.3f} -> {fast.garble_ratio:.3f}, garbled={fast.garbled})"
            )
    if hard_reasons:
        verdict = "NO"
    elif inconclusive_notes:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "YES"
    return verdict, hard_reasons + inconclusive_notes


def write_report(  # noqa: PLR0913
    out: Path,
    *,
    base_url: str,
    docs: list[Doc],
    reports: dict[tuple[str, str], ArmReport],
    runs: int,
    partial: bool,
) -> None:
    verdict, reasons = fast_gate(reports, docs)
    lines = [
        f"# RFC-052 conversion benchmark ({_dt.date.today().isoformat()})"
        + (" -- PARTIAL (one or more arms errored)" if partial else ""),
        "",
        f"Backend: `{base_url}` · runs per arm: {runs} (medians, plus 1 discarded "
        f"warm-up) · script: `scripts/conversion_bench.py`",
        "",
        "| doc | arm | pages | median s | s/page | verdict | garbled | garble ratio "
        "| cells | cell delta vs baseline | changed vs baseline | changed vs r3 (gate) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for doc in docs:
        for arm in ARMS:
            r = reports.get((doc.label, arm))
            if r is None or r.error:
                lines.append(
                    f"| {doc.label} | {arm} | -- | -- | -- | ERROR | -- | -- | -- | -- | -- |"
                )
                continue
            d = r.cell_diff or {}
            spp = r.median_s / doc.page_count if doc.page_count else float("nan")
            vs_r3 = f"{r.cell_diff_vs_r3['changed_cell_ratio']:.2%}" if r.cell_diff_vs_r3 else ""
            lines.append(
                f"| {doc.label} | {arm} | {doc.page_count} | {r.median_s:.1f} | {spp:.2f} "
                f"| {r.verdict} | {r.garbled} | {r.garble_ratio:.3f} "
                f"| {d.get('arm_cells', '')} | {d.get('cell_count_delta', '')} "
                f"| {d.get('changed_cell_ratio', 0.0):.2%} | {vs_r3} |"
            )
    lines += [
        "",
        "## Noise floor (baseline vs baseline, first two timed runs)",
        "",
        "Same settings, different runs -- how much of any r3_fast-vs-r3 diff "
        "could be non-determinism rather than a real TableFormer-mode effect.",
        "",
    ]
    for doc in docs:
        base = reports.get((doc.label, "baseline"))
        if base and base.noise_floor:
            lines.append(
                f"- {doc.label}: changed-cell ratio {base.noise_floor['changed_cell_ratio']:.2%} "
                f"across {len(base.runs)} baseline runs"
            )
        else:
            lines.append(f"- {doc.label}: not available (baseline errored or < 2 runs)")
    lines += [
        "",
        "## R4 AC4: may FAST become the default?",
        "",
        "Rule: on every document, the r3_fast-vs-r3 changed-cell ratio is <= "
        f"{FAST_MAX_CHANGED_CELL_RATIO:.0%}, no verdict gets worse and garble does "
        f"not increase by more than {GARBLE_NOISE_TOLERANCE} (float-noise tolerance). "
        "Baseline numbers above are context only -- they also differ in chunking "
        "and OCR policy, so they are not the gate input.",
        "",
        f"**Result: {verdict}" + ("" if verdict == "YES" else " -- FAST stays opt-in") + ".**",
        "",
    ]
    lines += [f"- {r}" for r in reasons] or ["- every condition holds"]
    lines += [
        "",
        "Per-run verdicts and seconds:",
        "",
        "```json",
        json.dumps(
            {
                f"{doc}/{arm}": (
                    {"error": r.error}
                    if r.error
                    else {
                        "verdicts": r.verdicts,
                        "seconds": [round(x.seconds, 1) for x in r.runs],
                    }
                )
                for (doc, arm), r in reports.items()
            },
            indent=2,
        ),
        "```",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"wrote {out}{' (PARTIAL)' if partial else ''}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    # R4 AC2 fixed plan: exactly these three documents, one flag each -- not
    # a repeatable --doc, so the run can never silently be missing one.
    ap.add_argument("--pocketbook", type=Path, required=True, help="text+table PDF")
    ap.add_argument("--scanned", type=Path, required=True, help="scanned or image-heavy PDF")
    ap.add_argument("--arabic", type=Path, required=True, help="Arabic or garble-prone PDF")
    ap.add_argument("--base-url", default=os.environ.get("DOCLING_BENCH_BASE_URL", ""))
    ap.add_argument("--runs", type=int, default=3, help="timed runs per arm (plus 1 warm-up)")
    ap.add_argument("--timeout-s", type=float, default=3600.0)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(f"audit/RFC052_CONVERSION_BENCH_{_dt.date.today().isoformat()}.md"),
    )
    args = ap.parse_args(argv)
    if not args.base_url:
        ap.error("name the remote backend: DOCLING_BENCH_BASE_URL or --base-url")
    base_url = args.base_url.rstrip("/")
    refuse_portfolio(base_url)
    require_hr3(base_url)

    from pageindex_mcp.config import settings
    from pageindex_mcp.storage.staging import delete_staging, upload_staging

    token = os.environ.get("DOCLING_BENCH_TOKEN") or settings.docling_service_bearer_token or ""
    require_bench_overrides_supported(base_url, token, timeout_s=30.0)

    docs = [
        Doc(label="pocketbook", path=args.pocketbook),
        Doc(label="scanned", path=args.scanned),
        Doc(label="arabic", path=args.arabic, expected_script="arabic"),
    ]
    reports: dict[tuple[str, str], ArmReport] = {}
    partial = False
    for doc in docs:
        classify(doc)
        key = upload_staging(f"bench-{uuid.uuid4().hex[:12]}", doc.path.name, doc.path.read_bytes())
        try:
            for arm, overrides in ARMS.items():
                report = ArmReport(doc=doc.label, arm=arm)
                # Finding 3: r3/r3_fast request page-class-driven behaviour --
                # when the doc actually has page classes, "applied" must show
                # them active, not just echo the "page_class" policy string.
                expect_active = arm in ("r3", "r3_fast") and doc.page_classes is not None
                try:
                    # Discarded warm-up: a cold model/converter cache must not
                    # pollute run 1's timing.
                    warmup = run_arm(base_url, token, doc, overrides, key, args.timeout_s)
                    verify_applied(arm, doc.label, overrides, warmup.applied, expect_active)
                    for i in range(args.runs):
                        run = run_arm(base_url, token, doc, overrides, key, args.timeout_s)
                        verify_applied(arm, doc.label, overrides, run.applied, expect_active)
                        print(f"[{doc.label}] {arm} run {i + 1}/{args.runs}: {run.seconds:.1f}s")
                        report.runs.append(run)
                    score(report, doc)
                except Exception as exc:  # one arm's failure must not abort the whole run
                    report.error = str(exc)
                    partial = True
                    print(f"[{doc.label}] {arm} ERRORED: {exc}", file=sys.stderr)
                reports[(doc.label, arm)] = report
        finally:
            delete_staging(key)  # HR2: the staged copy never outlives the run

        baseline = reports.get((doc.label, "baseline"))
        r3 = reports.get((doc.label, "r3"))
        r3_fast = reports.get((doc.label, "r3_fast"))
        if baseline and not baseline.error and len(baseline.runs) >= 2:
            baseline.noise_floor = table_cell_diff(
                baseline.runs[0].markdown, baseline.runs[1].markdown
            )
        for arm in ARMS:
            r = reports.get((doc.label, arm))
            if r is None or r.error or baseline is None or baseline.error:
                continue
            r.cell_diff = table_cell_diff(baseline.runs[0].markdown, r.runs[0].markdown)
        if r3 and not r3.error and r3_fast and not r3_fast.error:
            r3_fast.cell_diff_vs_r3 = table_cell_diff(r3.runs[0].markdown, r3_fast.runs[0].markdown)

    write_report(
        args.out, base_url=base_url, docs=docs, reports=reports, runs=args.runs, partial=partial
    )
    return 1 if partial else 0


if __name__ == "__main__":
    sys.exit(main())
