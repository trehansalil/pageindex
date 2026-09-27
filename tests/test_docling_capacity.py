# ALLOW-NEW-TEST-FILE: not tests/test_docling_service_cancel.py because capacity, not cancellation
"""RFC-052 P3, tasks 7.1-7.3: docling-service capacity reporting.

- 7.1 ``GET /capacity`` (R5 AC1): free memory, ``safe_procs``, slots, the
  ``spp_ewma`` tracker and ``build_sha``.
- 7.2 the planner clamps its worker count by free-memory ``safe_procs`` (R5 AC2).
- 7.3 ``page_start``/``page_end`` on ``PdfConvertRequest`` (R5 AC3).

Endpoint coroutines are called directly, or through a lifespan-less
TestClient, and the converter is always a fake: no Docling model ever loads.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from pageindex_mcp.converters import docling_resources as dr

_MIB = 1024**2
_GIB = 1024**3


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.fixture
def capacity_mod(docling_service_app):
    """``services/docling-service/capacity.py``, importable once ``app`` is."""
    import capacity  # type: ignore[import-not-found]

    return capacity


# ---------------------------------------------------------------------------
# 7.1 readers, formula and tracker
# ---------------------------------------------------------------------------


def test_7_1_free_memory_and_effective_cpus_readers(monkeypatch, capacity_mod):
    """7.1: Linux free memory is min(cgroup max - current, MemAvailable);
    macOS is vm_stat free + inactive + speculative. effective_cpus is the
    cgroup quota (not floored) on Linux, P-cores + 0.5 E-cores on macOS."""
    files = {
        "/proc/meminfo": "MemTotal: 8000000 kB\nMemAvailable: 2000000 kB",
        "/sys/fs/cgroup/memory.max": str(3 * _GIB),
        "/sys/fs/cgroup/memory.current": str(2 * _GIB),
    }
    monkeypatch.setattr(dr, "_read", files.get)
    # The cgroup has 1 GiB left, the host 2000000 kB: the smaller wins.
    assert dr.free_memory_bytes() == 1 * _GIB
    # An unlimited cgroup leaves the host's MemAvailable (158% overcommit).
    files["/sys/fs/cgroup/memory.max"] = "max"
    assert dr.free_memory_bytes() == 2000000 * 1024
    # cgroup v1.
    del files["/sys/fs/cgroup/memory.max"], files["/sys/fs/cgroup/memory.current"]
    files["/sys/fs/cgroup/memory/memory.limit_in_bytes"] = str(4 * _GIB)
    files["/sys/fs/cgroup/memory/memory.usage_in_bytes"] = str(int(3.5 * _GIB))
    assert dr.free_memory_bytes() == _GIB // 2
    # available_memory_bytes() keeps its meaning: the total, for plan sizing.
    assert dr.available_memory_bytes() == 4 * _GIB

    vm_stat = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               100000.\n"
        "Pages active:                             999999.\n"
        "Pages inactive:                           50000.\n"
        "Pages speculative:                        10000.\n"
        "Pages wired down:                         888888.\n"
    )
    assert dr.parse_vm_stat(vm_stat) == (100000 + 50000 + 10000) * 16384
    assert dr.parse_vm_stat("garbage") is None
    files.clear()  # no /proc on macOS
    monkeypatch.setattr(dr.sys, "platform", "darwin")
    monkeypatch.setattr(dr, "_run_vm_stat", lambda: vm_stat)
    assert dr.free_memory_bytes() == (100000 + 50000 + 10000) * 16384

    cm = capacity_mod
    sysctl = {"hw.perflevel0.physicalcpu": "10", "hw.perflevel1.physicalcpu": "4"}
    monkeypatch.setattr(cm, "_sysctl_int", lambda key: int(sysctl[key]) if key in sysctl else None)
    assert cm.effective_cpus(platform="darwin") == 12.0  # M4 Pro: 10 P + 4 E
    monkeypatch.setattr(cm, "_read", {"/sys/fs/cgroup/cpu.max": "250000 100000"}.get)
    monkeypatch.setattr(cm.os, "sched_getaffinity", lambda _pid: set(range(8)), raising=False)
    assert cm.effective_cpus(platform="linux") == 2.5
    monkeypatch.setattr(cm, "_read", {"/sys/fs/cgroup/cpu.max": "max 100000"}.get)
    assert cm.effective_cpus(platform="linux") == 8.0


def test_7_1_effective_cpus_malformed_cgroup_falls_back(monkeypatch, capacity_mod):
    """Review finding 3 (repair cycle 2: + zero-period cases): a malformed
    cpu.max/cfs value -- garbage, or a zero period -- must not raise. Both
    turned into a 500 on /capacity. Falls back to the affinity/os CPU count.
    """
    cm = capacity_mod
    monkeypatch.setattr(cm.os, "sched_getaffinity", lambda _pid: set(range(6)), raising=False)
    cases = [
        {"/sys/fs/cgroup/cpu.max": "garbage 100000"},
        {"/sys/fs/cgroup/cpu.max": "250000 not-a-number"},
        {"/sys/fs/cgroup/cpu.max": "250000 0"},  # zero period: ZeroDivisionError
        {
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "nope",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
        },
        {
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "250000",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "0",  # zero period: ZeroDivisionError
        },
    ]
    for files in cases:
        monkeypatch.setattr(cm, "_read", files.get)
        assert cm.effective_cpus(platform="linux") == 6.0


def test_7_1_safe_procs_formula():
    """7.1: safe_procs = min(floor(effective_cpus), floor((free - reserve) /
    per_proc_peak)), per_proc_peak = WORKER_BASE_BYTES + chunk_pages *
    PER_PAGE_BYTES; never negative, and can be 0."""
    peak = dr.per_proc_peak_bytes(11)
    assert peak == dr.WORKER_BASE_BYTES + 11 * dr.PER_PAGE_BYTES
    reserve = 4 * _GIB
    assert dr.compute_safe_procs(12.0, 41_000_000_000, reserve, 11) == 12  # cpu-bound
    assert dr.compute_safe_procs(12.5, 64 * _GIB, 0, 11) == 12  # floor of the quota
    assert dr.compute_safe_procs(4.0, reserve + 2 * peak + 1, reserve, 11) == 2  # memory-bound
    assert dr.compute_safe_procs(4.0, reserve + peak - 1, reserve, 11) == 0
    assert dr.compute_safe_procs(4.0, reserve // 2, reserve, 11) == 0  # below the reserve


def test_7_1_spp_tracker_ewma_from_docling_chunk_records(monkeypatch, capacity_mod):
    """7.1: spp_ewma falls back to the prior with no samples, and is fed by
    the ``docling_chunk`` records (only ``ok`` chunks, seconds / pages)."""
    from pageindex_mcp.converters import docling_conv

    cm = capacity_mod
    tracker = cm.SppTracker(prior=19.0)
    assert tracker.snapshot() == (19.0, 0)
    tracker.record(duration_s=100.0, pages=10)
    assert tracker.snapshot() == (10.0, 1)  # the first sample replaces the prior
    tracker.record(duration_s=200.0, pages=10)
    ewma, n = tracker.snapshot()
    assert n == 2 and ewma == pytest.approx(10.0 + cm.SPP_EWMA_ALPHA * (20.0 - 10.0))

    # Wired to the converter's record: a chunk timed 30 s over pages 4..9.
    tracker = cm.SppTracker(prior=40.0)
    filt = cm.install_spp_filter(tracker)
    conv_logger = logging.getLogger(docling_conv.__name__)
    monkeypatch.setattr(conv_logger, "level", logging.INFO)
    try:
        common = {"do_table_structure": True, "do_ocr": False, "peak_rss_bytes": None}
        docling_conv.emit_docling_chunk(
            chunk="1/2", page_start=4, page_end=9, duration_s=30.0, outcome="ok", **common
        )
        docling_conv.emit_docling_chunk(
            chunk="2/2", page_start=10, page_end=11, duration_s=999.0, outcome="timeout", **common
        )
    finally:
        conv_logger.removeFilter(filt)
    assert tracker.snapshot() == (5.0, 1)
    assert cm.spp_prior({"DOCLING_SPP_PRIOR": "19"}) == 19.0
    assert cm.spp_prior({"DOCLING_SPP_PRIOR": "nonsense"}) == cm.DEFAULT_SPP_PRIOR


# ---------------------------------------------------------------------------
# 7.1 endpoint
# ---------------------------------------------------------------------------

_CAPACITY_KEYS = {
    "backend": str,
    "build_sha": str,
    "effective_cpus": float,
    "total_mem_bytes": int,
    "free_mem_bytes": int,
    "reserve_bytes": int,
    "chunk_pages": int,
    "per_proc_peak_bytes": int,
    "safe_procs": int,
    "busy_slots": int,
    "max_slots": int,
    "spp_ewma": float,
    "spp_samples": int,
}


def test_7_1_capacity_endpoint_is_bearer_authed_with_the_design_schema(
    docling_service_app, capacity_mod, monkeypatch
):
    """7.1: GET /capacity needs the same bearer token as /convert and returns
    exactly the design's fields -- nothing about any document (HR3)."""
    from fastapi.testclient import TestClient

    app = docling_service_app
    monkeypatch.setattr(app, "BEARER_TOKEN", "sekret")
    monkeypatch.setattr(app, "ALLOW_ANONYMOUS", False)
    monkeypatch.setenv("BUILD_SHA", "abc123")
    monkeypatch.setenv("DOCLING_BACKEND_NAME", "mac")
    monkeypatch.setenv("DOCLING_RESERVE_BYTES", str(4 * _GIB))
    monkeypatch.setattr(capacity_mod, "effective_cpus", lambda platform=None: 12.0)
    monkeypatch.setattr(capacity_mod, "available_memory_bytes", lambda: 64 * _GIB)
    monkeypatch.setattr(capacity_mod, "free_memory_bytes", lambda: 41_000_000_000)
    client = TestClient(app.app)  # no `with`: the lifespan (warm-up) never runs

    assert client.get("/capacity").status_code == 401
    assert client.get("/capacity", headers={"Authorization": "Bearer nope"}).status_code == 403
    resp = client.get("/capacity", headers={"Authorization": "Bearer sekret"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == set(_CAPACITY_KEYS)
    assert {k: type(v) for k, v in body.items()} == _CAPACITY_KEYS
    chunk_pages = dr.MIN_CHUNK_PAGES
    assert body["backend"] == "mac" and body["build_sha"] == "abc123"
    assert body["chunk_pages"] == chunk_pages
    assert body["per_proc_peak_bytes"] == dr.per_proc_peak_bytes(chunk_pages)
    assert body["safe_procs"] == dr.compute_safe_procs(12.0, 41_000_000_000, 4 * _GIB, chunk_pages)
    assert (body["busy_slots"], body["max_slots"]) == (0, app.MAX_CONCURRENT)
    assert body["spp_samples"] == 0 and body["spp_ewma"] == capacity_mod.spp_prior()

    # A held slot is busy; an unreadable free memory reports 0 procs, never a guess.
    monkeypatch.setattr(capacity_mod, "free_memory_bytes", lambda: None)
    asyncio.run(app._convert_slots.acquire())
    try:
        body = client.get("/capacity", headers={"Authorization": "Bearer sekret"}).json()
    finally:
        app._convert_slots.release()
    assert body["busy_slots"] == 1 and body["free_mem_bytes"] is None and body["safe_procs"] == 0


# ---------------------------------------------------------------------------
# 7.2 planner clamp
# ---------------------------------------------------------------------------


def test_7_2_planner_clamps_workers_by_free_memory_safe_procs(monkeypatch, caplog):
    """7.2: the plan is sized from total memory as before, then its worker
    count is clamped by the free-memory safe_procs; threads follow. Below one
    safe process it still converts with one, and says so at WARNING."""
    monkeypatch.delenv("DOCLING_RESERVE_BYTES", raising=False)
    total = 6783 * _MIB  # the cx33 row: 4 workers x 1 thread, 10-page chunks
    base = dr.plan_docling(292, cpus=4, memory_bytes=total)
    assert (base.workers, base.threads_per_worker, base.pages_per_chunk) == (4, 1, 10)

    peak = dr.per_proc_peak_bytes(base.pages_per_chunk)
    free = dr.reserve_bytes() + 2 * peak + 1
    with caplog.at_level(logging.INFO, logger=dr.__name__):
        plan = dr.plan_docling(292, cpus=4, memory_bytes=total, free_bytes=free)
    assert (plan.workers, plan.threads_per_worker, plan.pages_per_chunk) == (2, 2, 10)
    assert plan.safe_procs == 2 and plan.memory_bytes == total
    assert any("clamp" in r.getMessage() for r in caplog.records)

    # Plenty free: the plan is unchanged, and nothing is logged.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=dr.__name__):
        roomy = dr.plan_docling(292, cpus=4, memory_bytes=total, free_bytes=64 * _GIB)
    assert (roomy.workers, roomy.threads_per_worker) == (4, 1)
    assert not [r for r in caplog.records if "clamp" in r.getMessage()]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=dr.__name__):
        tight = dr.plan_docling(292, cpus=4, memory_bytes=total, free_bytes=peak // 2)
    assert (tight.workers, tight.threads_per_worker, tight.safe_procs) == (1, 4, 0)
    assert any(r.levelno == logging.WARNING for r in caplog.records)

    # A live call (the service's) reads the free memory itself.
    monkeypatch.setattr(dr, "available_cpus", lambda: 4)
    monkeypatch.setattr(dr, "available_memory_bytes", lambda: total)
    monkeypatch.setattr(dr, "free_memory_bytes", lambda: free)
    assert dr.plan_docling(292).workers == 2
    monkeypatch.setattr(dr, "free_memory_bytes", lambda: None)  # unreadable: no clamp
    assert dr.plan_docling(292).workers == 4

    # Review finding 1: every case above has pages_per_chunk == 10, which is
    # both MIN_CHUNK_PAGES and chunk_pages()'s default, so a regression that
    # clamps against one of those instead of plan.pages_per_chunk would go
    # unnoticed. This plan's pages_per_chunk is 18 -- neither of those.
    big = dr.plan_docling(100, cpus=2, memory_bytes=4268 * _MIB)
    assert (big.workers, big.pages_per_chunk) == (2, 18)
    assert big.pages_per_chunk != dr.MIN_CHUNK_PAGES

    # Chosen so that clamping on the correct pages_per_chunk (18, the bigger
    # per-proc peak) allows only 1 safe process, while clamping on
    # MIN_CHUNK_PAGES (10, a smaller peak) would wrongly allow 2 -- so a
    # regression that used the wrong chunk size changes this test's result.
    big_free = dr.reserve_bytes() + 2 * dr.per_proc_peak_bytes(dr.MIN_CHUNK_PAGES) + 1
    wrong_safe = dr.compute_safe_procs(2, big_free, dr.reserve_bytes(), dr.MIN_CHUNK_PAGES)
    right_safe = dr.compute_safe_procs(2, big_free, dr.reserve_bytes(), big.pages_per_chunk)
    assert wrong_safe == 2 and right_safe == 1

    clamped_big = dr.plan_docling(100, cpus=2, memory_bytes=4268 * _MIB, free_bytes=big_free)
    assert clamped_big.workers == 1
    assert clamped_big.safe_procs == right_safe
    assert clamped_big.safe_procs == dr.compute_safe_procs(
        clamped_big.cpus, big_free, dr.reserve_bytes(), big.pages_per_chunk
    )


# ---------------------------------------------------------------------------
# 7.3 page ranges
# ---------------------------------------------------------------------------


def _make_pdf(path, pages: int) -> str:
    import fitz

    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page-marker-{i}")
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def svc(docling_service_app, monkeypatch, tmp_path):
    """The service with the download and the converter faked; the page count
    and slicing are real (fitz), the converter sees the path it is handed."""
    app = docling_service_app
    seen: list[dict] = []
    downloads: list[str] = []
    _make_pdf(tmp_path / "doc.pdf", 8)

    async def fake_download(url, suffix=".pdf"):
        dst = tmp_path / f"download-{len(downloads)}.pdf"
        dst.write_bytes((tmp_path / "doc.pdf").read_bytes())
        downloads.append(str(dst))
        return str(dst)

    def fake_convert(path, **kw):
        import fitz

        with fitz.open(path) as doc:
            texts = [p.get_text().strip() for p in doc]
        seen.append({"path": path, "texts": texts, **kw})
        # Pictures come back in the converter's own (1-based, per-file) pages.
        return "md", [{"page": 1, "ocr_text": "fig"}], {}

    monkeypatch.setattr(app, "_download_to_temp", fake_download)
    monkeypatch.setattr(
        app,
        "plan_docling",
        lambda _n: SimpleNamespace(pages_per_chunk=100, workers=1, threads_per_worker=1),
    )
    monkeypatch.setattr("pageindex_mcp.converters.pdf_to_markdown_docling", fake_convert)
    return SimpleNamespace(app=app, seen=seen, downloads=downloads)


# 8 pages: 0-1 text-only, 2-5 tables, 6-7 scanned.
_CLASSES = [[0, 1, "T--"], [2, 5, "T-t"], [6, 7, "---"]]


def test_7_3_page_range_slices_the_pdf_and_rebases_page_classes(svc):
    """7.3: pages [3, 6] (0-based, inclusive, the docling_chunk convention)
    are cut out with fitz; the page classes and pages_with_tables are rebased
    to the slice; the response pages are the slice's own (the coordinator
    adds page_start when it merges), and the range is echoed in ``applied``."""
    app = svc.app
    req = app.PdfConvertRequest(
        presigned_url="http://minio/x.pdf",
        page_classes=_CLASSES,
        pages_with_tables=[2, 3, 5, 7],
        page_start=3,
        page_end=6,
    )
    resp = asyncio.run(app.convert_pdf(req, _FakeRequest()))
    (call,) = svc.seen
    assert call["path"] != svc.downloads[0]
    assert call["texts"] == [f"page-marker-{i}" for i in (3, 4, 5, 6)]
    assert [pc.flags for pc in call["page_classes"]] == ["T-t", "T-t", "T-t", "---"]
    assert call["pages_with_tables"] == {0, 2}
    assert [p.page for p in resp.picture_results] == [1]
    assert resp.applied["page_range"] == [3, 6]
    assert resp.applied["page_classes_active"] is True


def test_7_3_no_range_takes_todays_path_untouched(svc, monkeypatch):
    """7.3 regression: without page_start/page_end nothing is sliced; the
    converter gets the downloaded file, the page classes and
    pages_with_tables exactly as today, and ``applied`` has no range."""
    app = svc.app
    monkeypatch.setattr(app, "_slice_pdf", lambda *a: pytest.fail("sliced without a range"))
    req = app.PdfConvertRequest(
        presigned_url="http://minio/x.pdf", page_classes=_CLASSES, pages_with_tables=[2, 7]
    )
    resp = asyncio.run(app.convert_pdf(req, _FakeRequest()))
    (call,) = svc.seen
    assert call["path"] == svc.downloads[0]
    assert call["texts"] == [f"page-marker-{i}" for i in range(8)]
    assert [pc.flags for pc in call["page_classes"]] == ["T--"] * 2 + ["T-t"] * 4 + ["---"] * 2
    assert call["pages_with_tables"] == {2, 7}
    assert "page_range" not in resp.applied
    assert set(resp.applied) == {
        "tableformer_mode",
        "pageclass_chunking",
        "do_ocr_policy",
        "page_classes_active",
        "route",
        "chunk_count",
    }


def test_7_3_invalid_page_ranges_are_422(svc):
    """7.3: both or neither, 0 <= page_start <= page_end < page count;
    anything else is a 422, never a silent whole-document conversion."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    app = svc.app
    bad_ranges = (
        {"page_start": 1},
        {"page_end": 3},
        {"page_start": -1, "page_end": 2},
        {"page_start": 4, "page_end": 3},
    )
    for bad in bad_ranges:
        with pytest.raises(ValidationError):
            app.PdfConvertRequest(presigned_url="http://minio/x.pdf", **bad)
    req = app.PdfConvertRequest(presigned_url="http://minio/x.pdf", page_start=5, page_end=8)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app.convert_pdf(req, _FakeRequest()))
    assert exc.value.status_code == 422
    assert svc.seen == []
