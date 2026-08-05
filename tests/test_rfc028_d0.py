"""Tests for RFC-028 Task 1.1 (D0): wire `chunked_docling_timeout_s` into
`worker.py`'s `_run_converter_subprocess` via the child's startup handshake,
instead of leaving it dead code.

Validates Design Property 1 (dynamic timeout scales with chunk count).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from pageindex_mcp.converters import (
    _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S,
    chunked_docling_timeout_s,
)
from pageindex_mcp.worker import (
    CHILD_GRACE_SECONDS,
    CHILD_TIMEOUT,
    JOB_TIMEOUT,
    _run_converter_subprocess,
)


class _RecordingTimeout:
    """Stand-in for `asyncio.timeout` that records every `seconds` value it
    is called with, then behaves as a real no-op async context manager so
    the wrapped `await` still runs to completion."""

    def __init__(self, sink: list):
        self.sink = sink

    def __call__(self, seconds):
        self.sink.append(seconds)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _fake_proc(handshake: dict | None, result: dict, returncode: int = 0):
    proc = MagicMock()
    proc.stdout = MagicMock()
    if handshake is not None:
        proc.stdout.readline = AsyncMock(return_value=(json.dumps(handshake) + "\n").encode())
    else:
        proc.stdout.readline = AsyncMock(return_value=b"")
    stdout = json.dumps(result).encode()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


class TestChunkedDoclingTimeoutConstants:
    def test_per_chunk_constant_raised_to_1500(self):
        assert _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S == 1500

    def test_two_chunk_pdf_timeout_covers_observed_range(self):
        # RFC-028: world-stats observed 24-49 minutes (1440-2940s); the
        # 2-chunk formula must clear that with margin.
        assert chunked_docling_timeout_s(2) >= 3000

    def test_job_timeout_raised_and_accommodates_dynamic_maximum(self):
        assert JOB_TIMEOUT == 3630
        assert chunked_docling_timeout_s(2) + CHILD_GRACE_SECONDS < JOB_TIMEOUT


class TestDynamicTimeoutWiring:
    """Property 1: effective_timeout = max(CHILD_TIMEOUT, chunked_docling_timeout_s(N))
    on a Docling route; CHILD_TIMEOUT unconditionally on a non-Docling route."""

    async def test_docling_route_uses_dynamic_timeout(self):
        handshake = {"handshake": True, "chunk_count": 2, "is_docling_route": True}
        result = {"ok": True, "doc_id": "d1"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            out = await _run_converter_subprocess("/tmp/big.pdf")
        assert out["doc_id"] == "d1"
        # NOTE: with CHILD_TIMEOUT = JOB_TIMEOUT - CHILD_GRACE_SECONDS = 3600
        # and chunked_docling_timeout_s(2) = 3300, max() resolves to
        # CHILD_TIMEOUT here — this case pins the world-stats 2-chunk shape;
        # the discriminating dynamic>fixed case is the 3-chunk test below.
        expected = max(CHILD_TIMEOUT, chunked_docling_timeout_s(2))
        # sink[0] is the handshake-read timeout (fixed 60s); sink[1] is the
        # remaining budget for communicate(), which is expected minus the
        # (near-zero) elapsed handshake time.
        assert len(sink) == 2
        assert expected - 5 <= sink[1] <= expected

    async def test_docling_route_dynamic_timeout_exceeds_child_timeout(self):
        # Discriminating case: pick a chunk_count whose dynamic timeout is
        # STRICTLY GREATER than CHILD_TIMEOUT, so this test fails if the D0
        # wiring is removed (a bare CHILD_TIMEOUT fallback would land 3600,
        # not chunked_docling_timeout_s(3) = 4800).
        assert chunked_docling_timeout_s(3) > CHILD_TIMEOUT
        handshake = {"handshake": True, "chunk_count": 3, "is_docling_route": True}
        result = {"ok": True, "doc_id": "d1b"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/bigger.pdf")
        expected = chunked_docling_timeout_s(3)
        assert expected - 5 <= sink[1] <= expected
        assert sink[1] > CHILD_TIMEOUT

    async def test_non_docling_route_falls_back_to_child_timeout_unconditionally(self):
        handshake = {"handshake": True, "chunk_count": 5, "is_docling_route": False}
        result = {"ok": True, "doc_id": "d2"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/small.pdf")
        assert CHILD_TIMEOUT - 5 <= sink[1] <= CHILD_TIMEOUT

    async def test_missing_handshake_falls_back_to_child_timeout(self):
        # Child died / handshake read failed -> no handshake line at all.
        result = {"ok": True, "doc_id": "d3"}
        proc = _fake_proc(None, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/nohandshake.pdf")
        assert CHILD_TIMEOUT - 5 <= sink[1] <= CHILD_TIMEOUT

    async def test_page_count_read_failure_reports_chunk_count_one_non_docling(self):
        # RFC-028 D0 edge case: pymupdf page-count read failure ->
        # probe_conversion_route
        # reports (1, False); worker must still land on CHILD_TIMEOUT, not
        # a lower dynamic value derived from chunk_count=1 on a Docling route.
        handshake = {"handshake": True, "chunk_count": 1, "is_docling_route": False}
        result = {"ok": True, "doc_id": "d4"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/unreadable.pdf")
        assert CHILD_TIMEOUT == JOB_TIMEOUT - CHILD_GRACE_SECONDS
        assert CHILD_TIMEOUT - 5 <= sink[1] <= CHILD_TIMEOUT

    async def test_bad_chunk_count_in_handshake_defaults_to_one(self):
        handshake = {"handshake": True, "chunk_count": "not-an-int", "is_docling_route": True}
        result = {"ok": True, "doc_id": "d5"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/badchunk.pdf")
        expected = max(CHILD_TIMEOUT, chunked_docling_timeout_s(1))
        assert expected - 5 <= sink[1] <= expected


class TestProbeConversionRoute:
    """`probe_conversion_route` is what the converter child calls to build
    the startup handshake worker.py reads -- covering it here keeps the
    handshake's producer and consumer tested against the same contract."""

    def test_non_pdf_input_reports_non_docling(self):
        from pageindex_mcp.converters import probe_conversion_route

        assert probe_conversion_route("notes.txt") == (1, False)

    def test_pymupdf_failure_reports_non_docling(self):
        from pageindex_mcp import converters

        # `converters.probe_conversion_route` does a function-local
        # `import fitz`, so `fitz.open` is the patch seam.
        with patch("fitz.open", side_effect=RuntimeError("bad pdf")):
            assert converters.probe_conversion_route("broken.pdf") == (1, False)

    def test_oversized_pdf_reports_chunked_docling_route(self):
        from pageindex_mcp import converters

        # `fitz.open(...)` is used as a context manager and read via
        # `doc.page_count`.
        fake_doc = MagicMock()
        fake_doc.page_count = 292
        fake_doc.__enter__.return_value = fake_doc
        fake_doc.__exit__.return_value = False
        with (
            patch("fitz.open", return_value=fake_doc) as fake_open,
            patch("pageindex_mcp.config.MAX_DOCLING_PAGES", 150),
        ):
            chunk_count, is_docling_route = converters.probe_conversion_route("world-stats.pdf")
        fake_open.assert_called_once_with("world-stats.pdf")
        assert is_docling_route is True
        assert chunk_count == 2
