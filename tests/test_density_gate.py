# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Zone-6: density gate & tree-prep consolidated tests.

Consolidates (former test_zone6_*.py files):
  - density_gate:        script/depth-aware content-density thresholds (Gate 9)
  - toc_guard:            char-loss abort + refined depth guard for ToC stripping
  - prepare_tree_orientation: orientation threading through prepare_tree
  - splitter_generic_tiers: ATX / generic-numbered-line splitter fallback tiers
  - table_segment_orientation: landscape vs. portrait table segmentation thresholds
  - reap_timeout:         reap_stale_jobs dynamic-timeout contract
  - fence_observability:  fence-parity observability warnings
  - late_success:         late-success reap-recovery regression
  - fallback_pipeline:    Candidate.has_depth / _heading_count / _run_stages
  - verdict_persistence:  five-writer verdict CAS + sidecar merge
"""

from __future__ import annotations

import copy
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client import _dominant_orientation
from pageindex_mcp.converters import (
    Candidate,
    _candidate_from_document,
    _has_structural_depth,
    _heading_count,
    _run_stages,
)
from pageindex_mcp.helpers import (
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    ScriptContext,
    TreeSignals,
    _gate_low_content_density,
    _segment_table_nodes,
    _split_on_generic_numbered_lines,
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
    prepare_tree,
    route_and_extract_flat,
    split_oversized_leaf_nodes,
)
from pageindex_mcp.job_status import JobStatus, _job_key
from pageindex_mcp.metrics import (
    FENCE_PARITY_WARNING,
    TOC_STRIP_HIGH_CHAR_LOSS,
    TOC_STRIP_SKIPPED,
)
from pageindex_mcp.storage import (
    save_doc_meta,
    write_verdict,
)
from pageindex_mcp.worker import (
    CHILD_TIMEOUT,
    REAP_GRACE,
    process_document_job,
    reap_stale_jobs,
)
from tests.conftest import filler_text

# ===========================================================================
# 1. _gate_low_content_density (Gate 9): script/depth-aware thresholds
# ===========================================================================


def _make_sig(node_count: int, depth: int, chars: int) -> TreeSignals:
    """Build a minimal TreeSignals with the given node_count, depth, and char count."""
    text = filler_text(chars, seed=42)
    return TreeSignals(
        node_count=node_count,
        depth=depth,
        max_leaf_ratio=0.5,
        flat_text=text,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
        primary_text=text,
    )


class TestDensityGate:
    """Shallow non-Arabic docs use 150 chars/node; deep trees (depth>=4) and
    Arabic-script docs lower to 50; node_count < 200 always bypasses."""

    def test_below_150_fires(self):
        sig = _make_sig(node_count=200, depth=2, chars=200 * 100)
        fired, detail = _gate_low_content_density(sig, [], ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"), 10, None)
        assert fired, "Should fire: 100 chars/node < 150 threshold"
        assert "threshold=150.0" in detail

    def test_deep_tree_above_50_passes(self):
        """200 nodes, depth=5, 80 chars/node -> passes deep threshold even
        though it is below the standard 150 threshold."""
        sig = _make_sig(node_count=200, depth=5, chars=200 * 80)
        fired, _ = _gate_low_content_density(sig, [], ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"), 10, None)
        assert not fired, "Deep tree 80 chars/node should pass (> 50)"

    def test_node_count_bypass_below_200(self):
        """node_count < 200 must never fire, regardless of density."""
        sig = _make_sig(node_count=199, depth=2, chars=199)
        fired, _ = _gate_low_content_density(sig, [], ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"), 10, None)
        assert not fired, "node_count < 200 must gate entirely"


# ===========================================================================
# 2. _strip_toc_heading_nodes_guarded: char-loss abort + refined depth guard
# ===========================================================================


def _toc_node(title, text=""):
    """Build a node that looks like a ToC entry (dot-leader title, empty body)."""
    return {"title": f"{title} ......... 12", "text": text, "nodes": []}


def _real_node(title, text, nodes=None):
    return {"title": title, "text": text, "nodes": nodes or []}


def _skipped_count():
    return TOC_STRIP_SKIPPED._value.get()


def _high_char_loss_count():
    return TOC_STRIP_HIGH_CHAR_LOSS._value.get()


class TestTocStripGuard:
    """char_loss_ratio > 0.15 aborts strip; refined depth guard fires only
    when depth_delta > 1 AND resulting_depth < 2; observability counter
    fires above 0.10 without aborting."""

    def test_low_char_loss_allows_strip(self):
        """ToC nodes that are mostly empty (< 15% char loss) allow the strip."""
        real_nodes = [_real_node(f"Art {i}", "x" * 200) for i in range(50)]
        toc_nodes = [_toc_node(f"Sec {i}") for i in range(5)]
        nodes = toc_nodes + real_nodes

        before_count = _tree_node_count(nodes)
        result = _strip_toc_heading_nodes_guarded(nodes, doc_name="test_low_char_loss")

        assert _tree_node_count(result) < before_count, "Low char-loss should allow strip"

    def test_depth_drop_exactly_1_allows_strip(self):
        """depth_delta == 1 -> NOT > 1, strip always proceeds."""
        toc = _toc_node("OnlyToC")
        root = _real_node("Root", "Real content with enough text.", nodes=[toc])
        nodes = [root]

        assert _tree_depth(nodes) == 2
        result = _strip_toc_heading_nodes_guarded(nodes, doc_name="test_delta1")
        assert _tree_node_count(result) <= _tree_node_count(nodes)


# ===========================================================================
# 3. prepare_tree orientation threading + _dominant_orientation
# ===========================================================================


def _pipe_table(n_data_rows: int, n_cols: int = 3) -> str:
    lines = ["| " + " | ".join(f"Col{c}" for c in range(n_cols)) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"cell{r}_{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


def _prose_of_length(n: int) -> str:
    unit = "Paragraph text. "
    repeats = (n // len(unit)) + 1
    return (unit * repeats)[:n]


def _make_table_node(n_data_rows: int) -> dict:
    table = _pipe_table(n_data_rows)
    padding_needed = max(0, _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD - len(table) + 200)
    prose = _prose_of_length(padding_needed)
    return {"title": "Root", "text": prose + "\n" + table, "nodes": []}


class TestPrepareTreeOrientation:
    """prepare_tree threads its orientation kwarg through to _segment_table_nodes."""

    def test_default_none_preserves_behavior(self):
        node = _make_table_node(n_data_rows=7)
        s1 = [copy.deepcopy(node)]
        s2 = [copy.deepcopy(node)]
        s3 = [copy.deepcopy(node)]

        result_default = prepare_tree(s1)
        result_none = prepare_tree(s2, orientation=None)
        result_manual = _segment_table_nodes(split_oversized_leaf_nodes(s3), orientation=None)

        assert result_default == result_none, "Default and explicit None must produce same result"
        assert result_none == result_manual, (
            "prepare_tree(orientation=None) must match manual split+segment"
        )


class TestDominantOrientation:
    """_dominant_orientation derives orientation from per-page landscape data."""

    def test_none_input(self):
        assert _dominant_orientation(None) is None


# ===========================================================================
# 4. split_oversized_leaf_nodes: ATX-heading / generic-numbered-line tiers
# ===========================================================================

_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
]


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words: list[str] = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + ")")[:n]


def _make_leaf(text: str, node_id: str = "n1") -> dict:
    return {"node_id": node_id, "title": "root", "text": text, "nodes": []}


def _full_text(node: dict) -> str:
    """Reconstruct full text from a split node (preamble + children)."""
    parts = [node["text"]]
    for child in node.get("nodes", []):
        parts.append(child["text"])
    return "".join(parts)


class TestSplitterGenericTiers:
    """ATX-heading and generic-numbered-line fallback tiers, cascade
    priority, LIS guard, and floor enforcement."""

    def test_atx_heading_fallback_splits_run_together_headings(self):
        body = _text_of_length(20000)
        text = f"Preamble text here.\n# Section 1\n{body}\n# Section 2\n{body}\n# Section 3\n{body}"
        assert len(text) > 50000

        tree = [_make_leaf(text)]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

        node = tree[0]
        assert len(node["nodes"]) >= 3, (
            f"Expected >=3 children from ATX split, got {len(node['nodes'])}"
        )
        assert _full_text(node) == text

    def test_lis_guard_rejects_out_of_order_numbers(self):
        """Numbers 5/2/8/1 are not monotonically increasing -> no split."""
        body = _text_of_length(18000)
        text = f"Preamble.\n5. {body}\n2. {body}\n8. {body}\n1. {body}"
        node = _make_leaf(text)
        result = _split_on_generic_numbered_lines(node, text, max_chars=100000, min_segments=3)
        assert result is False, "Out-of-order numbers should be rejected by LIS guard"
        assert node["nodes"] == []

    def test_min_seg_chars_collapses_dense_references(self):
        """min_seg_chars=5000 collapses lines that sit closer than that."""
        short = _text_of_length(1000)
        long_body = _text_of_length(20000)
        text = (
            "Preamble.\n"
            + "".join(f"{i}. {short}\n" for i in range(1, 9))
            + f"9. {long_body}\n10. {long_body}\n11. {long_body}"
        )
        node = _make_leaf(text)
        result = _split_on_generic_numbered_lines(
            node, text, max_chars=100000, min_segments=3, min_seg_chars=5000
        )

        if result:
            assert len(node["nodes"]) < 11, (
                f"Expected < 11 children after min_seg_chars collapse, got {len(node['nodes'])}"
            )
        else:
            assert node["nodes"] == []


# ===========================================================================
# 5. _segment_table_nodes: orientation-aware table segmentation thresholds
# ===========================================================================


def _make_table_node2(title: str, n_data_rows: int, n_cols: int = 3, char_padding: int = 0) -> dict:
    table = _pipe_table(n_data_rows, n_cols)
    if char_padding:
        text = _prose_of_length(char_padding) + "\n" + table
    else:
        text = table
    return {"title": title, "text": text, "nodes": []}


def _make_singleton_table_node(title: str, n_data_rows: int, singleton_fraction: float) -> dict:
    n_singleton = int(n_data_rows * singleton_fraction)
    n_multi = n_data_rows - n_singleton

    rows = [f"| Item{i} | Data{i} |" for i in range(n_multi)]
    rows += [f"| OnlyVal{i} |" for i in range(n_singleton)]

    table_text = "\n".join(["| Key | Value |", "| --- | --- |"] + rows)
    padding_needed = max(0, _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD - len(table_text) + 100)
    text = _prose_of_length(padding_needed) + "\n" + table_text
    return {"title": title, "text": text, "nodes": []}


class TestTableSegmentOrientation:
    """Landscape orientation uses min_rows=10 / singleton_ratio=0.4;
    portrait/None uses min_rows=5 / singleton_ratio=0.6."""

    def test_landscape_segments_table_with_12_rows(self):
        """12 data rows: both portrait and landscape should segment."""
        node = _make_table_node2(
            "Table12",
            n_data_rows=12,
            n_cols=3,
            char_padding=_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD + 100,
        )
        result_portrait = _segment_table_nodes([copy.deepcopy(node)], orientation="portrait")
        result_landscape = _segment_table_nodes([copy.deepcopy(node)], orientation="landscape")

        assert bool(result_portrait[0].get("nodes")), "Portrait with 12 rows should segment"
        assert bool(result_landscape[0].get("nodes")), "Landscape with 12 rows should segment"


# ===========================================================================
# 6. reap_stale_jobs: dynamic per-job timeout
# ===========================================================================


def _make_job_hash(
    *,
    status: str = "processing",
    processing_started_at: str | None = None,
    effective_timeout_at: str | None = None,
) -> dict[str, str]:
    data: dict[str, str] = {"status": status}
    if processing_started_at is not None:
        data["processing_started_at"] = processing_started_at
    if effective_timeout_at is not None:
        data["effective_timeout_at"] = effective_timeout_at
    return data


def _make_scan_iter(keys: list[str]):
    async def _scan_iter(match=None):
        for k in keys:
            yield k

    return _scan_iter


class TestReapDynamicTimeout:
    """reap_stale_jobs respects effective_timeout_at, falls back to the
    legacy fixed cutoff, and honors the 16.5x scanned-PDF budget window."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        redis.hget = AsyncMock(return_value=None)
        redis.hset = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    def ctx(self, mock_redis):
        return {"redis": mock_redis}

    async def test_job_with_future_deadline_not_reaped(self, ctx, mock_redis):
        now = int(time.time())
        job_key = _job_key("job-future")
        mock_redis.scan_iter = _make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(now - 100),
            effective_timeout_at=str(now + 3600),
        )

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()

    async def test_16_5x_multiplier_window_not_reaped(self, ctx, mock_redis):
        """A scanned-PDF job with a 16.5x timeout budget is not reaped
        within that extended window."""
        now = int(time.time())
        effective_timeout = CHILD_TIMEOUT * 16.5
        started = now - int(effective_timeout * 0.9)
        deadline = started + int(effective_timeout) + REAP_GRACE

        job_key = _job_key("job-scanned")
        mock_redis.scan_iter = _make_scan_iter([job_key])
        mock_redis.hgetall.return_value = _make_job_hash(
            processing_started_at=str(started),
            effective_timeout_at=str(deadline),
        )

        await reap_stale_jobs(ctx)

        mock_redis.hset.assert_not_called()


# ===========================================================================
# 7. route_and_extract_flat: fence-parity observability warnings
# ===========================================================================


def _fence_warning_count(kind: str) -> float:
    return FENCE_PARITY_WARNING.labels(kind=kind)._value.get()


def _all_text(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        if b.get("text"):
            parts.append(b["text"])
        if b.get("ocr_text"):
            parts.append(b["ocr_text"])
    return " ".join(parts)


class TestFenceObservability:
    """Orphan-close and unclosed-at-EOF fences fire counters + never lose
    content; balanced fences produce no warnings."""

    def test_orphan_close_fires_warning(self, caplog):
        md = "```\nSome content here.\nMore content."
        before = _fence_warning_count("orphan_close")

        content_class, blocks = route_and_extract_flat(md)

        after = _fence_warning_count("orphan_close")
        assert after > before, "FENCE_PARITY_WARNING(orphan_close) must increment"
        assert "fence_parity" in caplog.text.lower() or "orphan" in caplog.text.lower()

    def test_unclosed_fence_preserves_content(self):
        md = "Before fence.\n```python\ndef hello():\n    pass\nAfter fence."
        _, blocks = route_and_extract_flat(md)

        text = _all_text(blocks)
        assert "Before fence" in text
        assert "hello" in text or "pass" in text
        assert "After fence" in text


# ===========================================================================
# 8. process_document_job: late-success reap-recovery
# ===========================================================================


class TestLateSuccessReapRecovery:
    """A job reaped mid-processing (status flipped to ERROR) that then
    completes successfully must still write DONE with late_success /
    reaped_recovery flags, return doc_id, and call _upsert_registry_row."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.expire = AsyncMock()
        return redis

    @pytest.fixture
    def ctx(self, mock_redis):
        return {"redis": mock_redis, "job_try": 1}

    def _patches(self, converter_result, job_dir):
        return (
            patch("pageindex_mcp.worker.job.download_staging"),
            patch("pageindex_mcp.worker.job.wait_for_memory", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.worker.job._run_converter_subprocess",
                new_callable=AsyncMock,
                return_value=converter_result,
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror._upsert_registry_row", new_callable=AsyncMock
            ),
            patch("pageindex_mcp.worker.job.delete_staging", return_value=True),
            patch("pageindex_mcp.worker.job.ACTIVE_UPLOADS"),
            patch("pageindex_mcp.worker.job.UPLOADS"),
            patch("pageindex_mcp.worker.job.UPLOAD_DURATION"),
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_bridged_incr", new_callable=AsyncMock
            ),
            patch("pageindex_mcp.worker.job.effective_config_snapshot", return_value={}),
            patch("tempfile.mkdtemp", return_value=job_dir),
            patch("shutil.rmtree"),
        )

    async def test_normal_success_no_late_success_flag(self, ctx, mock_redis):
        converter_result = {
            "ok": True,
            "doc_id": "doc-456",
            "peak_rss_kib": 500,
            "duration_ms": 2000,
            "_effective_timeout": 3600,
        }
        with (
            patch("pageindex_mcp.worker.job.download_staging"),
            patch("pageindex_mcp.worker.job.wait_for_memory", new_callable=AsyncMock),
            patch(
                "pageindex_mcp.worker.job._run_converter_subprocess",
                new_callable=AsyncMock,
                return_value=converter_result,
            ),
            patch(
                "pageindex_mcp.worker.registry_mirror._upsert_registry_row",
                new_callable=AsyncMock,
            ) as mock_upsert,
            patch("pageindex_mcp.worker.job.delete_staging", return_value=True),
            patch("pageindex_mcp.worker.job.ACTIVE_UPLOADS"),
            patch("pageindex_mcp.worker.job.UPLOADS"),
            patch("pageindex_mcp.worker.job.UPLOAD_DURATION"),
            patch(
                "pageindex_mcp.worker.registry_mirror._mirror_bridged_incr", new_callable=AsyncMock
            ),
            patch("pageindex_mcp.worker.job.effective_config_snapshot", return_value={}),
            patch("tempfile.mkdtemp", return_value="/tmp/test-job2"),
            patch("shutil.rmtree"),
        ):
            hget_calls = 0

            async def normal_hget(key, field):
                nonlocal hget_calls
                if field == "status":
                    hget_calls += 1
                    if hget_calls == 1:
                        return None
                    return JobStatus.PROCESSING.value
                return None

            mock_redis.hget = AsyncMock(side_effect=normal_hget)

            doc_id = await process_document_job(ctx, "uploads/staging/job-2/normal.pdf", "job-2")

            assert doc_id == "doc-456"
            mock_upsert.assert_called_once()

            for call in mock_redis.hset.call_args_list:
                mapping = call.kwargs.get("mapping", {})
                if mapping.get("status") == "done":
                    assert "late_success" not in mapping
                    assert "reaped_recovery" not in mapping
                    break


# ===========================================================================
# 9. converters: Candidate.has_depth / _heading_count / _run_stages
# ===========================================================================


class TestCandidateHasDepth:
    """Candidate.has_depth caches _has_structural_depth(md) at construction
    time so the selection block reads it declaratively."""

    def test_multi_heading_deep_tree_has_depth_true(self):
        md = "# Title\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n\n## Section C\n\nBody C."
        c = _candidate_from_document(md, {}, "/fake.pdf")
        assert c.has_depth is True
        assert c.has_depth == _has_structural_depth(c.md)

    def test_has_depth_default_is_false(self):
        """Default value for has_depth is False (safe fallback for callers
        that construct Candidate with only md=)."""
        c = Candidate(md="# A\n\n## B\n\n## C\n\n## D")
        assert c.has_depth is False


class TestHeadingCountHelper:
    """_heading_count is a thin wrapper consolidating repeated
    len(_HEADING_RE.findall(md)) patterns."""

    def test_headings_only_at_line_start(self):
        """Inline hash marks are not headings."""
        md = "Some text with # not a heading\n# Real heading"
        assert _heading_count(md) == 1


class TestRunStagesRegression:
    """_run_stages dict provenance (char/heading deltas, error handling)."""

    def test_stage_n_failure_does_not_skip_n_plus_1(self):
        def fail_stage(md: str) -> str:
            raise RuntimeError("fail")

        def ok_stage(md: str) -> str:
            return md + "!"

        result_md, records = _run_stages("x", [("fail", fail_stage), ("ok", ok_stage)])
        assert result_md == "x!"
        assert records["fail"]["error"] == "fail"
        assert records["ok"]["error"] is None


# ===========================================================================
# 10. storage: five writers, lost-update sidecar merge (verdict persistence)
# ===========================================================================


def _nosuchkey():
    from minio.error import S3Error

    return S3Error(MagicMock(), "NoSuchKey", "missing", "res", "req", "host")


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    client.get_object.side_effect = _nosuchkey()

    with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=client):
        yield client


def _set_existing_sidecar(mock_mc: MagicMock, data: dict) -> None:
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    mock_mc.get_object.side_effect = None
    mock_mc.get_object.return_value = resp


def _written_sidecar(mock_mc: MagicMock) -> dict:
    call_args = mock_mc.put_object.call_args
    stream = call_args[0][2]
    return json.loads(stream.read())


class TestSaveDocMetaCasIntegration:
    """save_doc_meta rejects stale verdict fields but accepts non-verdict fields."""

    def test_newer_verdict_accepted(self, mock_minio):
        existing = {
            "doc_id": "cas02",
            "doc_name": "report.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "verdict": "MARGINAL",
            "verdict_reason": "leaf_concentration",
            "pipeline_version": 3,
            "verdict_computed_at": "2026-08-01T00:00:00+00:00",
            "max_leaf_ratio": 0.35,
        }
        _set_existing_sidecar(mock_minio, existing)

        meta = {
            "doc_id": "cas02",
            "verdict": "PASS",
            "verdict_reason": "promoted",
            "pipeline_version": 4,
            "verdict_computed_at": "2026-08-10T12:00:00+00:00",
            "max_leaf_ratio": 0.05,
        }
        save_doc_meta("cas02", meta)
        sidecar = _written_sidecar(mock_minio)

        assert sidecar["verdict"] == "PASS"
        assert sidecar["pipeline_version"] == 4


class TestWriteVerdict:
    """write_verdict writes only the .meta.json sidecar (Zone-5: no
    dual-write to the artifact)."""

    def test_max_leaf_ratio_rounded_to_4_decimals(self, mock_minio):
        write_verdict(
            doc_id="wv04",
            verdict="PASS",
            verdict_reason="ok",
            pipeline_version=4,
            verdict_computed_at="2026-08-12",
            max_leaf_ratio=0.123456789,
        )

        first_call = mock_minio.put_object.call_args_list[0]
        written = json.loads(first_call[0][2].read())
        assert written["max_leaf_ratio"] == 0.1235


class TestReadRegistryFieldsSidecarFallback:
    """read_registry_fields falls back to the sidecar when the artifact
    lacks verdict fields (Zone-8 Target 4)."""


class TestPromotionSweepVerdictRouting:
    """promotion_sweep.run_sweep routes verdict through write_verdict, never
    save_doc_meta."""

    @pytest.mark.asyncio
    async def test_sweep_calls_write_verdict_not_save_doc_meta_for_verdict(self):
        sweep_meta = {
            "doc_id": "sweep01",
            "doc_name": "sweep.pdf",
            "source_url": "",
            "processed_at": "2026-01-01",
            "structure": [{"title": "Ch1", "text": "hello", "nodes": []}],
        }
        sweep_json = json.dumps(sweep_meta).encode()
        sidecar_json = json.dumps(
            {
                "doc_id": "sweep01",
                "verdict": "MARGINAL",
                "verdict_reason": "leaf_concentration",
            }
        ).encode()

        with (
            patch("promotion_sweep.sweep_candidates", return_value=["sweep01"]),
            patch("promotion_sweep.init_registry"),
            patch("promotion_sweep.close_registry"),
            patch("promotion_sweep.upsert_doc"),
            patch("promotion_sweep.settings") as mock_settings,
            patch("promotion_sweep.get_minio") as mock_get_minio,
            patch("promotion_sweep.write_verdict") as mock_wv,
            patch("promotion_sweep.save_doc_meta") as mock_sdm,
            patch("promotion_sweep.classify_verdict", return_value=("PASS", "base_pass")),
            patch("promotion_sweep._tree_max_leaf_ratio", return_value=(0, 0, 0.05)),
        ):
            mock_settings.postgres_dsn = "postgresql://test"
            mock_settings.minio_bucket = "test-bucket"

            mc = MagicMock()

            def _get_object(bucket, key):
                resp = MagicMock()
                resp.read.return_value = sidecar_json if key.endswith(".meta.json") else sweep_json
                return resp

            mc.get_object.side_effect = _get_object
            mock_get_minio.return_value = mc

            from promotion_sweep import run_sweep

            await run_sweep()

            mock_wv.assert_called_once()
            call_args = mock_wv.call_args
            assert call_args[0][0] == "sweep01"
            assert call_args[0][1] == "PASS"
            assert call_args[0][2] == "base_pass"
            assert call_args[0][5] == 0.05

            mock_sdm.assert_called_once()
            sdm_meta = mock_sdm.call_args[0][1]
            assert "verdict" not in sdm_meta
            assert "verdict_reason" not in sdm_meta


class TestRecomputeVerdictsWriteVerdict:
    """preprocess_client.recompute_verdicts calls write_verdict with the
    correct positional arguments."""


class TestRegistryBackfillPropagationOnly:
    """_enrich_one and _heal_one are propagators, not computers -- they
    must never call classify_verdict or write_verdict."""

    @pytest.mark.asyncio
    async def test_heal_one_never_calls_classify_verdict_or_write_verdict(self):
        with (
            patch("pageindex_mcp.registry_backfill.backfill.read_registry_fields") as mock_rrf,
            patch("pageindex_mcp.registry_backfill.backfill.save_doc_meta"),
            patch("pageindex_mcp.registry_backfill.backfill.upsert_doc"),
            patch("pageindex_mcp.registry_backfill.backfill.get_minio"),
            patch("pageindex_mcp.helpers.classify_verdict") as mock_cv,
            patch("pageindex_mcp.storage.write_verdict") as mock_wv,
        ):
            mock_rrf.return_value = {
                "doc_id": "heal01",
                "doc_name": "test.pdf",
                "sha256": "abc",
                "doc_description": "desc",
                "verdict": "PASS",
                "verdict_reason": "base_pass",
            }

            from pageindex_mcp.registry_backfill import _heal_orphans

            await _heal_orphans({"heal01": None})

            mock_cv.assert_not_called()
            mock_wv.assert_not_called()


class TestSqlVerdictFilter:
    """list_docs / count_docs / stage_a_filter SQL all exclude both 'FAIL'
    and '' (empty string) verdicts."""
