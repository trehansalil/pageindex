"""Page-class census (RFC-052 R2 AC3, AC8).

Runs the production page classifier (``preclassify.classify_pages``, the
cheap-signal path the worker uses) on one or more PDFs and compares its table
signals with PyMuPDF ``find_tables()`` run on EVERY page as ground truth.

Checks, per document:
  * AC3  -- the column-alignment signal marks <= ``--max-ratio`` (default
            1.10) times the ``find_tables()`` positive pages (``None`` = n/a
            when ``find_tables()`` finds no table at all);
  * time -- classification wall time <= ``--budget-s`` (default 30 s, the
            design's P1 budget for the pocketbook).
Exit status is 1 when any check fails.

Usage::

    uv run python scripts/page_class_census.py doc_store/world-stats-pocketbook-2023.pdf
    uv run python scripts/page_class_census.py a.pdf b.pdf --json
    uv run python scripts/page_class_census.py a.pdf --no-find-tables   # timing only

``find_tables()`` costs ~0.8 s/page on portfolio (~4-5 min for the
pocketbook). Needs the PyMuPDF (AGPL, HR4) dependency; run it on a dev host.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter


def _ranges(pages) -> str:
    from pageindex_mcp.converters.preclassify import _compact_ranges

    return _compact_ranges(pages) or "-"


def census(pdf_path: str, *, find_tables: bool, max_ratio: float, budget_s: float) -> dict:
    import fitz

    from pageindex_mcp.converters.preclassify import _classify_document_pages, classify_pages

    t0 = time.perf_counter()
    classes, method = classify_pages(pdf_path)
    classify_s = time.perf_counter() - t0
    if classes is None:
        raise SystemExit(f"{pdf_path}: classification failed (see WARNING log)")

    with fitz.open(pdf_path) as doc:
        unpadded = _classify_document_pages(doc, label=pdf_path)
        ft_pages: set[int] | None = None
        find_tables_s = None
        if find_tables:
            t1 = time.perf_counter()
            ft_pages = {i for i in range(len(doc)) if doc[i].find_tables().tables}
            find_tables_s = time.perf_counter() - t1

    signals = Counter(sig for _pc, sig in unpadded if sig)
    column = {i for i, (_pc, sig) in enumerate(unpadded) if sig == "column_alignment"}
    cheap = {i for i, (_pc, sig) in enumerate(unpadded) if sig in ("column_alignment", "vector")}
    tables = {i for i, pc in enumerate(classes) if pc.has_tables}
    report: dict = {
        "pdf": pdf_path,
        "pages": len(classes),
        "detection_method": method,
        "classify_s": round(classify_s, 1),
        "class_counts": dict(sorted(Counter(pc.flags for pc in classes).items())),
        "text_layer_pages": sum(pc.has_text_layer for pc in classes),
        "image_pages": sum(pc.has_images for pc in classes),
        "needs_ocr_pages": sum(pc.needs_ocr for pc in classes),
        "table_pages_padded": len(tables),
        "table_signal_counts": dict(sorted(signals.items())),
        "column_alignment_pages": len(column),
        "no_table_pages": _ranges(set(range(len(classes))) - tables),
    }
    checks = {"classify_time_ok": classify_s <= budget_s}
    if ft_pages is not None:
        # No find_tables() positives: any column positive is an unbounded ratio.
        ratio = len(column) / len(ft_pages) if ft_pages else (float("inf") if column else 0.0)
        report |= {
            "find_tables_s": round(find_tables_s, 1),
            "find_tables_pages": len(ft_pages),
            "column_vs_find_tables_ratio": round(ratio, 3),
            "cheap_vs_find_tables": {
                "true_pos": len(cheap & ft_pages),
                "false_pos": len(cheap - ft_pages),
                "false_neg": len(ft_pages - cheap),
                "false_neg_after_padding": len(ft_pages - tables),
                "false_neg_pages": _ranges(ft_pages - tables),
            },
        }
        # AC3's bound is relative to find_tables(); with no positives it is
        # undefined (n/a), not failed. The false_pos count still shows it.
        checks["ac3_column_ratio_ok"] = ratio <= max_ratio if ft_pages else None
    report["checks"] = checks
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--json", action="store_true", help="print one JSON object per PDF")
    ap.add_argument("--no-find-tables", action="store_true", help="skip the find_tables() pass")
    ap.add_argument("--max-ratio", type=float, default=1.10)
    ap.add_argument("--budget-s", type=float, default=30.0)
    args = ap.parse_args(argv)

    ok = True
    for pdf in args.pdfs:
        report = census(
            pdf,
            find_tables=not args.no_find_tables,
            max_ratio=args.max_ratio,
            budget_s=args.budget_s,
        )
        ok &= all(v for v in report["checks"].values() if v is not None)
        if args.json:
            print(json.dumps(report))
            continue
        print(f"== {pdf}")
        for key, value in report.items():
            if key != "pdf":
                print(f"  {key:30s} {value}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
