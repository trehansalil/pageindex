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

    # Review finding 3 (repair cycle 2: + zero-period cases): a malformed
    # cpu.max/cfs value -- garbage, or a zero period -- must not raise. Both
    # turned into a 500 on /capacity. Falls back to the affinity/os CPU count.
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

    # 7.1: safe_procs = min(floor(effective_cpus), floor((free - reserve) /
    # per_proc_peak)), per_proc_peak = WORKER_BASE_BYTES + chunk_pages *
    # PER_PAGE_BYTES; never negative, and can be 0.
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
    "slice_slots": int,
    "busy_slice_slots": int,
    "spp_ewma": float,
    "spp_samples": int,
    "leaked_slots": int,
    "overdue_s": float,
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
    # Slice slots are clamped to what free memory fits at full slice size.
    fit = dr.compute_safe_procs(12.0, 41_000_000_000, 4 * _GIB, dr.SLICE_MAX_PAGES)
    assert body["busy_slice_slots"] == 0
    assert body["slice_slots"] == max(1, min(app.SLICE_SLOTS, fit))
    monkeypatch.setattr(app, "SLICE_SLOTS", 64)
    monkeypatch.setattr(capacity_mod, "free_memory_bytes", lambda: 4 * _GIB + 1)
    tight = client.get("/capacity", headers={"Authorization": "Bearer sekret"}).json()
    assert tight["slice_slots"] == 1  # nothing fits beyond the reserve: one, never zero
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


def _make_pdf(path, pages: int, table_page: int | None = None) -> str:
    """*pages* marker pages; *table_page* also gets a captioned, ruled 4x3 table."""
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page-marker-{i}")
        if i == table_page:
            page.insert_text((72, 110), "Table 1. Ruled figures")
            for r in range(4):
                for c in range(3):
                    x, y = 72 + c * 120, 120 + r * 24
                    page.draw_rect(fitz.Rect(x, y, x + 120, y + 24), color=(0, 0, 0), width=1)
                    text = ("Name", "2019", "2020")[c] if r == 0 else f"v{r}{c}"
                    page.insert_text((x + 4, y + 16), text)
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
    monkeypatch.setattr(app, "_pdf_cache", {})  # module-level: one per test
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
    # Today's keys plus RFC-052 R9's switch echo / per-chunk decisions /
    # prior-pass flag (design "Logging") -- and still no page_range.
    assert set(resp.applied) == {
        "tableformer_mode",
        "pageclass_chunking",
        "do_ocr_policy",
        "page_classes_active",
        "route",
        "chunk_count",
        "tables_ocr_bypass",
        "tables_ocr_bypass_min_filled",
        "tableformer_skip_enabled",
        "chunks",
        "prior_pass_received",
    }
    assert resp.applied["prior_pass_received"] is False


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


def test_a_p5_5_split_chunks_share_a_download_and_a_group_slot(svc, monkeypatch):
    """A-P5-5: chunks carrying one presigned URL download the PDF once and
    each converts as one process on its share of the cores; up to SLICE_SLOTS
    run at once and, as a group, hold every whole-conversion slot."""
    import os
    import time

    app = svc.app
    for start, end in ((0, 3), (4, 7)):
        req = app.PdfConvertRequest(
            presigned_url="http://minio/x.pdf?sig=1", page_start=start, page_end=end
        )
        asyncio.run(app.convert_pdf(req, _FakeRequest()))
    assert len(svc.downloads) == 1
    assert [c["texts"][0] for c in svc.seen] == ["page-marker-0", "page-marker-4"]
    share = max(1, app.CPUS // app.SLICE_SLOTS)
    assert {(c["workers"], c["num_threads"], c["max_pages"]) for c in svc.seen} == {(1, share, 4)}
    # A renewed signature is a new download: the cache never serves a request
    # MinIO did not authorise.
    req = app.PdfConvertRequest(presigned_url="http://minio/x.pdf?sig=2", page_start=0, page_end=1)
    asyncio.run(app.convert_pdf(req, _FakeRequest()))
    assert len(svc.downloads) == 2
    # HR2: an idle copy is deleted.
    monkeypatch.setattr(app, "PDF_CACHE_IDLE_S", 0.0)
    app._evict_idle_pdfs(time.time() + 1)
    assert app._pdf_cache == {}
    assert not any(os.path.exists(p) for p in svc.downloads)

    # Two whole-conversion slots: the group must hold both, not just one.
    monkeypatch.setattr(app, "MAX_CONCURRENT", 2)
    monkeypatch.setattr(app, "_convert_slots", app._CountingSemaphore(2))
    monkeypatch.setattr(app, "_slice_slots", app._CountingSemaphore(2))

    async def group():
        await app._acquire_slice_slot()
        await app._acquire_slice_slot()
        assert (app._slice_slots.held, app._convert_slots.held, app._slice_active) == (2, 2, 2)
        third = asyncio.ensure_future(app._acquire_slice_slot())
        whole = asyncio.ensure_future(app._convert_slots.acquire())
        await asyncio.sleep(0.01)
        assert not third.done() and not whole.done()
        app._release_slice_slot()
        await asyncio.sleep(0.01)
        assert third.done() and not whole.done()  # the group still holds it
        app._release_slice_slot()
        app._release_slice_slot()
        await asyncio.wait_for(whole, 1)
        app._convert_slots.release()

    asyncio.run(group())
    assert (app._slice_slots.held, app._convert_slots.held) == (0, 0)

    # A chunk cancelled while the group waits for its slots gives back the
    # part it already took.
    import threading
    import types

    monkeypatch.setattr(app, "CLIENT_POLL_S", 0.005)
    conv = types.SimpleNamespace(cancel_event=threading.Event())

    async def cancelled_while_queued():
        # Fresh primitives: the previous asyncio.run bound the old ones.
        monkeypatch.setattr(app, "_convert_slots", app._CountingSemaphore(2))
        monkeypatch.setattr(app, "_slice_slots", app._CountingSemaphore(2))
        monkeypatch.setattr(app, "_slice_group_lock", asyncio.Lock())
        await app._convert_slots.acquire()  # a whole conversion holds one of two
        admit = asyncio.ensure_future(
            app._admit_unless_cancelled(app._acquire_slice_slot, app._release_slice_slot, conv)
        )
        await asyncio.sleep(0.02)
        assert app._convert_slots.held == 2 and not admit.done()  # took the other
        conv.cancel_event.set()
        with pytest.raises(app.DoclingCancelled):
            await asyncio.wait_for(admit, 1)
        assert (app._convert_slots.held, app._slice_slots.held, app._slice_active) == (1, 0, 0)
        app._convert_slots.release()

    asyncio.run(cancelled_while_queued())


# --------------------------------------------------------------------------- 9.1 table capture


def test_9_1_tables_schema_roundtrip_links_and_title():
    """9.1 schema (tables.json v1): table_id reading order, containment links
    (symmetric, both records kept, P14), caption/title/coverage rules, and a
    lossless JSON round-trip that rejects other schema versions."""
    from dataclasses import replace

    from pageindex_mcp.tables import schema as ts

    def rec(page, bbox, source=ts.SOURCE_PYMUPDF, strategy="lines"):
        return ts.TableRecord(
            table_id="", page=page, page_label=str(page + 1), bbox=bbox, rows=2, cols=2,
            header=("A", "B"), cells=(("A", "B"), ("1", "")), markdown="|A|B|",
            source=source, strategy=strategy, coverage=0.5,
        )  # fmt: skip

    low, high = rec(104, (36.0, 300.0, 559.0, 411.0)), rec(104, (36.0, 92.0, 559.0, 200.0))
    twin = rec(104, (30.0, 80.0, 560.0, 210.0), ts.SOURCE_TABLEFORMER, None)  # contains `high`
    sliver = rec(104, (500.0, 190.0, 600.0, 320.0))  # overlaps both, contained < 0.5
    other = rec(7, (36.0, 92.0, 559.0, 200.0), ts.SOURCE_TABLEFORMER, None)  # same box, other page
    ids = ts.assign_table_ids([low, twin, sliver, high, other])
    assert [r.table_id for r in ids] == ["p0007-f0", "p0104-t0", "p0104-t1", "p0104-t2", "p0104-f0"]
    assert ids[1].bbox == high.bbox and ids[2].bbox == sliver.bbox  # top first, then left
    assert ts.containment((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert ts.containment((0, 0, 10, 10), (5, 5, 5, 20)) == 0.0  # degenerate box

    linked = {r.table_id: r for r in ts.link_tables(ids, min_overlap=0.5)}
    assert len(linked) == 5  # nothing dropped
    assert [lk.table_id for lk in linked["p0104-t0"].links] == ["p0104-f0"]
    assert [lk.table_id for lk in linked["p0104-f0"].links] == ["p0104-t0"]
    assert linked["p0104-t0"].links[0].overlap == linked["p0104-f0"].links[0].overlap == 1.0
    assert linked["p0104-t1"].links == linked["p0104-t2"].links == linked["p0007-f0"].links == ()
    assert ts.link_tables(ts.link_tables(ids)) == ts.link_tables(ids)  # recomputed, idempotent

    # Coverage: word characters whose box centre is inside, over all word characters.
    words = [(40, 100, 60, 110, "abcd"), (40, 500, 60, 510, "efghijkl")]
    assert ts.coverage(words, (36, 92, 559, 200)) == pytest.approx(4 / 12, abs=1e-4)
    assert ts.coverage([], (0, 0, 1, 1)) == 0.0
    # Caption: nearest matching block within 40 pt above or below, else None.
    bbox = (36.0, 100.0, 559.0, 300.0)
    blocks = [
        (36, 40, 300, 55, "Table 9. Too far above", 0, 0),
        (36, 70, 300, 90, "Table 3.  Economic\nindicators", 1, 0),
        (36, 320, 300, 335, "Tabelle 4 below, farther", 2, 0),
        (36, 305, 300, 318, "Source: not a caption", 3, 0),
    ]
    assert ts.find_caption(blocks, bbox) == "Table 3. Economic indicators"
    assert ts.find_caption(blocks[3:], bbox) is None
    assert ts.find_caption([(36, 320, 300, 335, "Tab. 2", 0, 0)], bbox) == "Tab. 2"
    # Title: caption, else header joined (<= 80 chars), else "Table p.<label>".
    assert ts.table_title("Table 3. X", ("a", "b"), "105") == "Table 3. X"
    assert ts.table_title(None, ("Indicator", "", "2019"), "105") == "Indicator | 2019"
    long_title = ts.table_title(None, tuple(f"column-{i:02d}" for i in range(20)), "105")
    assert len(long_title) == ts.TITLE_MAX_CHARS and long_title.endswith("…")
    assert ts.table_title(None, ("", " "), "105") == "Table p.105"

    result = ts.CaptureResult(
        tables=list(linked.values()), failed=[(288, 291, "deadline")], procs=2,
        duration_s=131.44, peak_rss_bytes=91226112,
    )  # fmt: skip
    doc = ts.TablesDocument(
        doc_id="d1",
        page_count=292,
        capture=ts.CaptureMeta.from_result(
            result, strategies=("lines", "text"), pymupdf_version="1.27.2.2"
        ),
        tables=tuple(
            replace(t, node_id="0042", node_id_method="heading_page") for t in result.tables
        ),
    )
    payload = doc.to_dict()
    assert payload["schema_version"] == ts.SCHEMA_VERSION == 1
    assert payload["capture"] == {
        "procs": 2, "duration_s": 131.4, "peak_rss_bytes": 91226112, "pymupdf": "1.27.2.2",
        "strategies": ["lines", "text"], "capture_failed": [[288, 291, "deadline"]],
    }  # fmt: skip
    assert set(payload["tables"][0]) == {
        "table_id", "page", "page_label", "bbox", "rows", "cols", "header", "cells", "markdown",
        "source", "strategy", "coverage", "node_id", "tree_node_id", "node_id_method", "caption",
        "title", "description", "description_source", "links",
    }  # fmt: skip
    assert ts.TablesDocument.from_json(doc.to_json()) == doc
    with pytest.raises(ValueError, match="schema_version"):
        ts.TablesDocument.from_dict({**payload, "schema_version": 2})
    with pytest.raises(ValueError):
        ts.TableRecord.from_dict({**payload["tables"][0], "table_id": "t0"})
    assert ts.normalise_cells([["a", None]]) == (("a", ""),)

    # ---- 9.2 anchor.py: TableFormer records, descriptions, node_id (P13),
    # tree insertion (post-gate, copy only -- P11) and garbled_pages (R9 AC7).
    import copy

    from pageindex_mcp.tables import anchor as ta

    def cells_rec(page, bbox, cells, source=ts.SOURCE_PYMUPDF):
        base = rec(page, bbox, source, "lines" if source == ts.SOURCE_PYMUPDF else None)
        return replace(base, header=cells[0], cells=cells, markdown=f"|{cells[0][0]}|")

    tarif = (("Tarif", "Beitrag"), ("A", "10"))
    (tf,) = ta.tableformer_records(
        [
            {"page": 4, "bbox": [30, 80, 560, 210], "rows": 2, "cols": 2,
             "cells": [list(r) for r in tarif], "markdown": "|Tarif|"},
            {"page": 5, "bbox": [0, 0, 1]},  # malformed: dropped
        ],
        page_info=lambda p: ("v", [(40, 100, 60, 110, "abcd")], []),
    )  # fmt: skip
    assert (tf.source, tf.page_label, tf.coverage, tf.header) == (
        ts.SOURCE_TABLEFORMER, "v", 1.0, ("Tarif", "Beitrag"),
    )  # fmt: skip
    pm = cells_rec(4, (36.0, 92.0, 559.0, 200.0), tarif)  # inside the TableFormer box
    pm_end = cells_rec(7, (36.0, 92.0, 559.0, 200.0), (("Kosten", "Wert"), ("x", "1")))
    pm_front = cells_rec(0, (36.0, 92.0, 559.0, 200.0), (("", ""), ("y", "2")))
    recs = ta.merge_sources([pm, pm_end, pm_front], [tf], 0.5)
    by = {r.table_id: r for r in recs}
    assert sorted(by) == ["p0000-t0", "p0004-f0", "p0004-t0", "p0007-t0"]
    assert [lk.table_id for lk in by["p0004-f0"].links] == ["p0004-t0"]

    described = ta.apply_descriptions(
        recs,
        {
            "p0004-t0": "Monthly premium per tariff.",
            "p0007-t0": ta.fallback_description(by["p0007-t0"]),  # C's fallback text
            "p0000-t0": ("Row values.", "llm"),  # describe_with_sources pair
        },
    )
    got = {r.table_id: (r.description, r.description_source) for r in described}
    assert got["p0004-t0"] == got["p0004-f0"] == ("Monthly premium per tariff.", "llm")  # twin
    assert got["p0007-t0"] == ("Table with columns: Kosten, Wert", "fallback")
    assert got["p0000-t0"] == ("Row values.", "llm")
    assert ta.apply_descriptions([pm_front], None)[0].description == "Table on page 1"

    structure = [
        {"node_id": "0001", "title": "Intro", "text": "x", "nodes": []},
        {"node_id": "0002", "title": "## Leistungen", "text": "", "nodes": [
            {"node_id": "0003", "title": "Tarife", "text": "", "nodes": [
                {"node_id": "0003_seg0", "title": "Tarife", "text": "prose", "nodes": []},
                {"node_id": "0003_seg1", "title": "Tarif | Beitrag", "nodes": [],
                 "text": "| Tarif | Beitrag |\n|---|---|\n| A | 10 |"},
            ]},
        ]},
        {"node_id": "0004", "title": "Anhang", "text": "y", "nodes": []},
    ]  # fmt: skip
    heading_pages = [("Intro", 1), ("Leistungen", 3), ("Tarife", 4), ("Anhang", 6)]
    resolved = ta.resolve_heading_pages(structure, heading_pages)
    ids = {n["node_id"]: n for n, _ in ta._walk(structure)}
    assert {k: resolved.get(id(n)) for k, n in ids.items()} == {
        "0001": 1, "0002": 3, "0003": 4, "0003_seg0": None, "0003_seg1": None, "0004": 6,
    }  # fmt: skip
    # No heading_pages: forward-only title search over the normalized page text.
    texts = ["", "INTRO", "", "x Leistungen:", "Tarife", "", "Anhang", ""]
    by_text = ta.resolve_heading_pages(structure, [], page_texts=texts)
    assert [by_text[id(ids[k])] for k in ("0001", "0002", "0003", "0004")] == [1, 3, 4, 6]
    # 9.5: a title whose own heading is missing must not prefix-match a far
    # later heading and skip every title in between; "&amp;" is decoded.
    oceania = [
        {"node_id": k, "title": t, "text": "x", "nodes": []}
        for k, t in (
            ("r1", "Micronesia"),
            ("c1", "Afghanistan"),
            ("c2", "Latin America &amp; the Caribbean"),
            ("c3", "Micronesia (Federated States of)"),
        )
    ]
    heads = [
        ("Afghanistan", 42),
        ("Latin America & the Caribbean", 50),
        ("Micronesia (Federated States of)", 177),
    ]
    aligned = ta.resolve_heading_pages(oceania, heads)
    assert [aligned.get(id(n)) for n in oceania] == [None, 42, 50, 177]

    anchored = {r.table_id: r for r in ta.assign_nodes(described, structure, resolved, 8)}
    assert (anchored["p0004-t0"].node_id, anchored["p0004-t0"].node_id_method) == (
        "0003", "heading_page",
    )  # fmt: skip  # deepest span containing page 4
    assert anchored["p0007-t0"].node_id == "0004"
    assert (anchored["p0000-t0"].node_id, anchored["p0000-t0"].node_id_method) == (
        "0001", "nearest",
    )  # fmt: skip  # before the first heading
    # A tie on a shared page (Intro spans to page 3, where Leistungen starts):
    # the heading below the table loses to the one above it.
    shared = cells_rec(3, (36.0, 92.0, 559.0, 200.0), tarif)
    (below,) = ta.assign_nodes([shared], structure, resolved, 8, heading_y=lambda t, p: 500.0)
    (above,) = ta.assign_nodes([shared], structure, resolved, 8, heading_y=lambda t, p: 50.0)
    assert (below.node_id, above.node_id) == ("0001", "0002")

    before = copy.deepcopy(structure)
    tree, placed = ta.insert_tables(structure, list(anchored.values()))
    assert structure == before  # the caller's node_count stays pre-insertion (P11)
    placed = {r.table_id: r for r in placed}
    assert placed["p0004-t0"].tree_node_id == placed["p0004-f0"].tree_node_id == "0003_seg1"
    tids = {n["node_id"]: n for n, _ in ta._walk(tree)}
    seg = tids["0003_seg1"]
    assert (seg["type"], seg["table_id"], seg["start_index"], seg["end_index"]) == (
        "table", "p0004-t0", 5, 5,
    )  # fmt: skip
    assert seg["title"] == "Tarif | Beitrag" and seg["description"] == got["p0004-t0"][0]
    assert seg["coverage"] == 0.5  # the search budget drops lowest coverage first
    new = tids["0004_t0"]  # unmatched PyMuPDF table: a new child, markdown as text
    assert (new["type"], new["table_id"], new["text"], new["start_index"], new["coverage"]) == (
        "table", "p0007-t0", "|Kosten|", 8, 0.5,
    )  # fmt: skip
    assert placed["p0000-t0"].tree_node_id == "0001_t0"
    assert len(tids) == len(ids) + 2  # the TableFormer twin is not a node of its own
    for r in placed.values():  # P13
        assert tids[r.node_id].get("type") != "table" and tids[r.tree_node_id]["type"] == "table"

    # garbled_pages: flagged nodes -> [hp[i], hp[i+1]-1], last to page_count-1;
    # None (unknown, never []) with no heading_pages or nothing anchored.
    def flag(*node_ids):
        return lambda n: n.get("node_id") in node_ids

    assert ta.garbled_pages(structure, heading_pages, 8, flag("0002", "0004")) == [3, 6, 7]
    assert ta.garbled_pages(structure, heading_pages, 8, flag("0003_seg1")) is None
    assert ta.garbled_pages(structure, [], 8, flag("0002")) is None
    chunks = [{"page_start": 0, "page_end": 3}, {"page_start": 4, "page_end": 7}]
    assert ta.attach_garbled_pages(chunks, [3, 6, 7]) == [
        {"page_start": 0, "page_end": 3, "garbled_pages": [3]},
        {"page_start": 4, "page_end": 7, "garbled_pages": [6, 7]},
    ]
    assert ta.attach_garbled_pages(chunks, None) == chunks


def _hog_capture_child(conn, pdf_path, pages, strategies):
    """Stand-in capture process for P8: finishes its first page, then touches
    96 MiB (past the test's 64 MiB TABLES_PROC_BYTES) and would sit for 30 s."""
    import time as _time

    conn.send(("page", pages[0], []))
    _time.sleep(0.3)  # let the parent read the finished page first
    ballast = b"x" * (96 * _MIB)
    _time.sleep(30)
    conn.send(("done", len(ballast)))


def _error_then_ok_capture_child(conn, pdf_path, pages, strategies):
    """Stand-in capture process for repair-cycle-2's HIGH regression: one bad
    page must not settle the worker (that would let the main loop break and
    the ``finally`` SIGKILL a still-scanning child, turning every later,
    would-have-succeeded page into "crash" too). The first page in its range
    errors; the rest scan cleanly and the process finishes normally."""
    _bad, *rest = pages
    conn.send(("error", "boom"))
    # Let the parent poll the error on its own first: a parent that settles
    # the worker on "error" would then kill it before the later pages arrive.
    import time

    time.sleep(0.5)
    for p in rest:
        conn.send(("page", p, []))
    conn.send(("done", 1024))


def _slow_capture_child(conn, pdf_path, pages, strategies):
    """Stand-in capture process for the capture budget: 0.3 s per page, so a
    fixed short join would cut it off."""
    import time as _time

    for p in pages:
        _time.sleep(0.3)
        conn.send(("page", p, []))
    conn.send(("done", 1024))


def test_9_1_capture_pool_bound_page_partition_and_rss_kill(tmp_path, monkeypatch):
    """9.1: P7 pool bound (formula and pod-wide fcntl slots), P9 page
    partition, P8 RSS kill of a real spawned process, a real 2-process
    capture of a ruled table, and ``start`` never blocking (P6)."""
    import time

    from pageindex_mcp.converters.preclassify import PageClass
    from pageindex_mcp.tables import capture as cap

    # P7 formula: max(1, min(cpu, mem, pages, slots_free)).
    size = {"reserve_bytes": 512 * _MIB, "proc_bytes": 256 * _MIB, "min_pages_per_proc": 30}
    assert cap.pool_size(292, cpus=2, free_bytes=8 * _GIB, slots_free=4, **size) == 2  # cpu
    assert (
        cap.pool_size(292, cpus=4, free_bytes=(512 + 2 * 256 + 1) * _MIB, slots_free=4, **size) == 2
    )
    assert cap.pool_size(40, cpus=4, free_bytes=8 * _GIB, slots_free=4, **size) == 2  # pages
    assert cap.pool_size(292, cpus=4, free_bytes=8 * _GIB, slots_free=1, **size) == 1  # slots
    assert cap.pool_size(292, cpus=4, free_bytes=100 * _MIB, slots_free=4, **size) == 1  # floor 1
    assert cap.pool_size(292, cpus=4, free_bytes=None, slots_free=4, **size) == 1  # unknown
    # P9: contiguous ranges partition [0, N); cheap positives are scanned first.
    for n, procs in ((292, 2), (7, 3), (3, 8), (1, 1)):
        ranges = cap.split_ranges(n, procs)
        assert [p for s, e in ranges for p in range(s, e + 1)] == list(range(n))
    classes = [PageClass.from_flags(f) for f in ("T--", "T--", "T-t", "T--", "T-t")]
    assert cap.scan_order(1, 4, classes) == [2, 4, 1, 3]
    assert cap.failed_runs([5, 1, 2, 4], "crash") == [(1, 2, "crash"), (4, 5, "crash")]

    # P7 pod bound: slots are fcntl locks shared by every job in the pod.
    monkeypatch.setattr(cap, "SLOT_LOCK_TEMPLATE", str(tmp_path / "slot-{i}.lock"))
    monkeypatch.setattr(cap, "available_cpus", lambda: 4)
    monkeypatch.setattr(cap, "free_memory_bytes", lambda: 8 * _GIB)
    monkeypatch.setenv("TABLES_POD_SLOTS", "2")
    monkeypatch.setenv("TABLES_MIN_PAGES_PER_PROC", "2")
    other_job = cap.acquire_slots(1, 2, deadline=0)
    mine = cap.acquire_slots(2, 2, deadline=0)
    assert (len(other_job), len(mine)) == (1, 1)  # one slot left for this document
    try:
        handle = cap.start(
            "/nonexistent.pdf", page_count=5, page_classes=None, deadline_monotonic=0
        )
        blocked = asyncio.run(handle.join(5))
    finally:
        cap.release_slots(other_job + mine)
    assert (blocked.procs, blocked.failed, blocked.tables) == (0, [(0, 4, "no_slot")], [])

    # A real capture: 2 spawned processes over 4 pages, the ruled table on page 2.
    # A spawned child's baseline RSS sits near the 256 MiB default on a loaded
    # host; this step tests capture, not the kill (P8 below), so give it room.
    monkeypatch.setenv("TABLES_PROC_BYTES", str(1024 * _MIB))
    pdf = _make_pdf(tmp_path / "t.pdf", 4, table_page=2)
    t0 = time.monotonic()
    handle = cap.start(pdf, page_count=4, page_classes=None, deadline_monotonic=t0 + 60)
    assert time.monotonic() - t0 < 0.5  # P6: start never waits on capture
    result = asyncio.run(handle.join(60))
    assert (result.procs, result.failed) == (2, [])
    (table,) = result.tables
    assert (table.table_id, table.page, table.rows, table.cols) == ("p0002-t0", 2, 4, 3)
    assert table.header == ("Name", "2019", "2020") and table.cells[1] == ("v10", "v11", "v12")
    assert table.caption == table.title == "Table 1. Ruled figures"
    # 9.1: markdown is built from the extracted cells, not Table.to_markdown().
    assert table.markdown.startswith("|Name|2019|2020|\n|---|---|---|\n|v10|v11|v12|\n")
    assert (
        table.strategy == "lines"
        and 0 < table.coverage < 1
        and table.source == "pymupdf_find_tables"
    )
    assert 0 < result.peak_rss_bytes < 1024 * _MIB

    # P8 + P9: a process over TABLES_PROC_BYTES is SIGKILLed; only its
    # unfinished pages fail, as contiguous rss_limit runs.
    monkeypatch.setattr(cap, "_child_target", _hog_capture_child)
    monkeypatch.setenv("TABLES_PROC_BYTES", str(64 * _MIB))
    monkeypatch.setenv("TABLES_RSS_POLL_S", "0.05")
    monkeypatch.setenv("TABLES_MIN_PAGES_PER_PROC", "30")  # one process, pages 0..5
    classes = [PageClass.from_flags("T-t" if p == 3 else "T--") for p in range(6)]
    handle = cap.start(
        pdf, page_count=6, page_classes=classes, deadline_monotonic=time.monotonic() + 60
    )
    killed = asyncio.run(handle.join(60))
    assert killed.procs == 1 and killed.duration_s < 10  # not the hog's 30 s
    assert killed.failed == [(0, 2, "rss_limit"), (4, 5, "rss_limit")]  # page 3 was scanned
    assert killed.peak_rss_bytes > 64 * _MIB

    # Repair cycle 2 (HIGH): a page that errors must not settle its worker --
    # only that page fails; every later page the same process scans cleanly
    # afterward is kept, and the call returns quickly (not the deadline).
    monkeypatch.setattr(cap, "_child_target", _error_then_ok_capture_child)
    monkeypatch.setenv("TABLES_MIN_PAGES_PER_PROC", "30")  # one process, pages 0..2
    t1 = time.monotonic()
    handle = cap.start(pdf, page_count=3, page_classes=None, deadline_monotonic=t1 + 60)
    partial = asyncio.run(handle.join(60))
    assert time.monotonic() - t1 < 5  # settled on the next poll tick, not the 60s deadline
    assert partial.procs == 1
    assert partial.failed == [(0, 0, "crash")]

    # TABLES_CAPTURE=0: no process, no slot, an empty result at once.
    monkeypatch.setenv("TABLES_CAPTURE", "0")
    off = asyncio.run(
        cap.start("/nonexistent.pdf", page_count=3, page_classes=None, deadline_monotonic=0).join(1)
    )
    assert (off.procs, off.failed, off.tables) == (0, [], [])

    # 9.2 PendingTables: TABLES_CAPTURE=0 starts nothing (so nothing is written).
    import sys
    import types

    from pageindex_mcp.tables import anchor as ta

    assert (
        ta.start_pending(
            "/nonexistent.pdf", page_count=3, page_class_ranges=None, deadline_monotonic=0
        )
        is None
    )

    joins: list = []

    class _Handle:
        async def join(self, grace_s):
            joins.append(grace_s)
            return result

    seen: list = []

    async def _describe_with_sources(tables, *, model):
        seen.append(([t.table_id for t in tables], model))
        return {t.table_id: ("Ruled figures by year.", "llm") for t in tables}

    async def _describe(tables, *, model):  # superseded when with_sources exists
        raise AssertionError("describe_with_sources must be preferred")

    fake = types.ModuleType("pageindex_mcp.tables.describe")
    fake.describe = _describe
    fake.describe_with_sources = _describe_with_sources
    monkeypatch.setitem(sys.modules, "pageindex_mcp.tables.describe", fake)
    monkeypatch.setattr("pageindex_mcp.tables.describe", fake, raising=False)

    async def _lifecycle():
        pend = ta.PendingTables(_Handle(), pdf, 4, ("lines",), 0.5)
        await pend.collect(30.0, describe=True, model="filter-model")
        tree = [{"node_id": "0001", "title": "Nowhere in the PDF", "text": "x", "nodes": []}]
        saved, doc = await pend.finalize(
            doc_id="d9", structure=tree, heading_pages=[], tableformer_results=[]
        )
        idle = ta.PendingTables(_Handle(), pdf, 4, ("lines",), 0.5)
        await idle.aclose()  # never collected: the capture is still joined (killed)
        return tree, saved, doc

    tree, saved, doc = asyncio.run(_lifecycle())
    assert joins == [30.0, 0.0] and seen == [(["p0002-t0"], "filter-model")]
    (t,) = doc.tables
    assert (doc.doc_id, doc.page_count, doc.capture.procs) == ("d9", 4, 2)
    assert (t.node_id, t.node_id_method, t.tree_node_id) == ("0001", "nearest", "0001_t0")
    assert (t.description, t.description_source) == ("Ruled figures by year.", "llm")
    assert tree[0]["nodes"] == [] and saved[0]["nodes"][0]["table_id"] == "p0002-t0"

    # P4 follow-up: PendingTables.finalize must not wait unboundedly on a
    # hanging describe call -- with a tiny TABLES_DESC_DEADLINE_S (no
    # converter-child deadline known in this test), finalize returns
    # promptly and every record gets the deterministic fallback description.
    async def _hang_describe_with_sources(tables, *, model):
        await asyncio.sleep(3600)
        return {}

    fake.describe_with_sources = _hang_describe_with_sources
    monkeypatch.delenv("PAGEINDEX_CHILD_DEADLINE_EPOCH", raising=False)
    monkeypatch.setenv("TABLES_DESC_DEADLINE_S", "0.05")

    async def _timeout_lifecycle():
        pend2 = ta.PendingTables(_Handle(), pdf, 4, ("lines",), 0.5)
        await pend2.collect(30.0, describe=True, model="filter-model")
        tree2 = [{"node_id": "0001", "title": "Nowhere in the PDF", "text": "x", "nodes": []}]
        t0 = time.monotonic()
        _, doc2 = await pend2.finalize(
            doc_id="d10", structure=tree2, heading_pages=[], tableformer_results=[]
        )
        return doc2, time.monotonic() - t0

    doc2, elapsed = asyncio.run(_timeout_lifecycle())
    assert elapsed < 5  # promptly bounded, not the 3600s hang
    assert doc2.tables and all(t2.description_source == "fallback" for t2 in doc2.tables)


def test_capture_budget_runs_to_deadline_and_grows_pool(tmp_path, monkeypatch):
    """11.7 follow-up: capture used to get "conversion time + 30 s", so the
    faster split arm captured 19 fewer pocketbook pages (150 vs 169 of 292)
    and node_count moved with wall time. A ``None`` join now runs capture to
    its own deadline; ranges come from a queue and the pool grows as memory
    frees up; an abandoned document stops its capture at once."""
    import time

    from pageindex_mcp.tables import anchor as ta
    from pageindex_mcp.tables import capture as cap

    monkeypatch.setattr(cap, "SLOT_LOCK_TEMPLATE", str(tmp_path / "slot-{i}.lock"))
    monkeypatch.setattr(cap, "available_cpus", lambda: 2)
    monkeypatch.setattr(cap, "_child_target", _slow_capture_child)
    monkeypatch.setenv("TABLES_POD_SLOTS", "2")
    monkeypatch.setenv("TABLES_MIN_PAGES_PER_PROC", "2")
    monkeypatch.setenv("TABLES_RSS_POLL_S", "0.05")
    monkeypatch.setenv("TABLES_PROC_BYTES", str(256 * _MIB))
    monkeypatch.setenv("TABLES_RESERVE_BYTES", str(512 * _MIB))
    monkeypatch.delenv("TABLES_JOIN_GRACE_S", raising=False)

    # Room for one process at the start (the converter child holds the rest),
    # for two once it has gone: the pool grows from 1 to 2 and takes a slot.
    t0 = time.monotonic()
    monkeypatch.setattr(
        cap,
        "free_memory_bytes",
        lambda: (512 + 256 + 1) * _MIB if time.monotonic() - t0 < 0.5 else 8 * _GIB,
    )
    handle = cap.start("/x.pdf", page_count=8, page_classes=None, deadline_monotonic=t0 + 60)
    full = asyncio.run(handle.join(None))
    assert (full.failed, full.procs) == ([], 2)

    # The legacy setting still works: a configured TABLES_JOIN_GRACE_S, fed
    # through collect() as the indexer does, cuts the same capture short;
    # ranges never started fail as "deadline", joined into one run.
    from pageindex_mcp.tables.settings import capture_settings

    assert capture_settings().join_grace_s is None
    monkeypatch.setattr(cap, "free_memory_bytes", lambda: 8 * _GIB)
    monkeypatch.setenv("TABLES_JOIN_GRACE_S", "0")

    async def _legacy():
        pend = ta.start_pending(
            "/x.pdf", page_count=8, page_class_ranges=None, deadline_monotonic=None
        )
        await pend.collect(capture_settings().join_grace_s, describe=False, model="m")
        await pend.collect_task
        return pend

    # A page or two may finish before the stop lands; the rest, never
    # started ones included, fail as "deadline".
    failed = asyncio.run(_legacy()).result.failed
    assert {r for _, _, r in failed} == {"deadline"} and failed[-1][1] == 7
    assert sum(e - s + 1 for s, e, _ in failed) >= 6
    monkeypatch.delenv("TABLES_JOIN_GRACE_S")

    # A document that never reaches persist does not wait out the capture.
    async def _abandon():
        pend = ta.start_pending(
            "/x.pdf", page_count=200, page_class_ranges=None, deadline_monotonic=None
        )
        await pend.collect(None, describe=False, model="m")
        t = time.monotonic()
        await pend.aclose()
        return pend, time.monotonic() - t

    pend, waited = asyncio.run(_abandon())
    assert waited < 10 and pend.result.failed  # not the ~30 s full scan


def test_capture_pool_defaults_fit_two_procs_in_worker_pod(tmp_path, monkeypatch):
    """11.7 re-run: in the 1536 MiB worker pod, the converter child and arq
    leave ~614 MiB free, and the 512 MiB reserve with 256 MiB per process
    held capture at 1 process (~620 s, the job's critical path, +27% wall).
    The defaults must start two there."""
    from pageindex_mcp.tables import capture as cap

    monkeypatch.setattr(cap, "SLOT_LOCK_TEMPLATE", str(tmp_path / "slot-{i}.lock"))
    monkeypatch.setattr(cap, "available_cpus", lambda: 2)
    monkeypatch.setattr(cap, "_child_target", _slow_capture_child)
    monkeypatch.setattr(cap, "free_memory_bytes", lambda: 614 * _MIB)
    monkeypatch.setenv("TABLES_POD_SLOTS", "2")
    monkeypatch.setenv("TABLES_MIN_PAGES_PER_PROC", "2")
    monkeypatch.setenv("TABLES_RSS_POLL_S", "0.05")
    for key in ("TABLES_PROC_BYTES", "TABLES_RESERVE_BYTES", "TABLES_JOIN_GRACE_S"):
        monkeypatch.delenv(key, raising=False)

    import time

    handle = cap.start(
        "/x.pdf", page_count=8, page_classes=None, deadline_monotonic=time.monotonic() + 60
    )
    result = asyncio.run(handle.join(None))
    assert (result.failed, result.procs) == ([], 2)
