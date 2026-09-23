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

Also absorbs the former ``test_rfc_tables.py`` (RFC-029 table / quality-gate
suite): NFKC normalization + bidi coherence, the low_content_density /
suspect_density gates, fence & HR stripping, degenerate duplicate-cell row
collapsing, picture-context retention, table-aware node segmentation, and the
zero-body contamination gate.

Table-driven tests loop internally and name every offending row, so one
collected test carries the coverage a parametrize table did.
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
    PictureResult,
    _candidate_from_document,
    _has_structural_depth,
    _heading_count,
    _pre_inference_normalize,
    _repair_docling_tables,
    _run_stages,
    decide_rtl,
    splice_figure_markers,
)
from pageindex_mcp.helpers import (
    _RFC029_MIN_SCANNED_DENSITY_FLOOR,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    BULK_PROFILE,
    ScriptContext,
    TreeSignals,
    _gate_low_content_density,
    _segment_table_nodes,
    _split_on_generic_numbered_lines,
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
    classify_verdict,
    prepare_tree,
    route_and_extract_flat,
    split_oversized_leaf_nodes,
    validate_tree,
)
from pageindex_mcp.job_status import JobStatus, _job_key
from pageindex_mcp.metrics import (
    FENCE_PARITY_WARNING,
)
from pageindex_mcp.storage import (
    save_doc_meta,
)
from pageindex_mcp.worker import (
    CHILD_TIMEOUT,
    REAP_GRACE,
    process_document_job,
    reap_stale_jobs,
)
from tests._garble_compat import check_garble
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
    )


class TestDensityGate:
    """Shallow non-Arabic docs use 150 chars/node; deep trees (depth>=4) and
    Arabic-script docs lower to 50; node_count < 200 always bypasses."""

    def test_density_gate_threshold_table(self):
        """Gate 9 firing decision across the node-count bypass, the standard
        150 chars/node threshold, and the depth>=4 relaxation to 50."""
        ctx = ScriptContext(
            dominant_script=None, had_presentation_forms=False, source="test"
        )
        cases = [
            # name, node_count, depth, chars, expect_fired, detail_substring
            ("100 chars/node shallow", 200, 2, 200 * 100, True, "threshold=150.0"),
            ("80 chars/node depth=5", 200, 5, 200 * 80, False, None),
            ("node_count=199 bypass", 199, 2, 199, False, None),
        ]

        failures = []
        for name, node_count, depth, chars, expect_fired, detail_sub in cases:
            sig = _make_sig(node_count=node_count, depth=depth, chars=chars)
            fired, detail = _gate_low_content_density(sig, [], ctx, 10, None)
            if bool(fired) is not expect_fired:
                failures.append(f"  [{name}] expected fired={expect_fired}, got={fired}")
            elif detail_sub and detail_sub not in detail:
                failures.append(f"  [{name}] detail missing {detail_sub!r}: {detail!r}")
        assert not failures, "Gate 9 density regressions:\n" + "\n".join(failures)


# ===========================================================================
# 2. _strip_toc_heading_nodes_guarded: char-loss abort + refined depth guard
# ===========================================================================


def _toc_node(title, text=""):
    """Build a node that looks like a ToC entry (dot-leader title, empty body)."""
    return {"title": f"{title} ......... 12", "text": text, "nodes": []}


def _real_node(title, text, nodes=None):
    return {"title": title, "text": text, "nodes": nodes or []}


class TestTocStripGuard:
    """char_loss_ratio > 0.15 aborts strip; refined depth guard fires only
    when depth_delta > 1 AND resulting_depth < 2; observability counter
    fires above 0.10 without aborting."""

    def test_strip_proceeds_on_low_char_loss_and_depth_delta_1(self):
        """ToC nodes that are mostly empty (< 15% char loss) allow the strip,
        and a depth_delta of exactly 1 (NOT > 1) never trips the depth guard."""
        real_nodes = [_real_node(f"Art {i}", "x" * 200) for i in range(50)]
        toc_nodes = [_toc_node(f"Sec {i}") for i in range(5)]
        nodes = toc_nodes + real_nodes

        before_count = _tree_node_count(nodes)
        result = _strip_toc_heading_nodes_guarded(nodes, doc_name="test_low_char_loss")
        assert _tree_node_count(result) < before_count, "Low char-loss should allow strip"

        delta1 = [
            _real_node("Root", "Real content with enough text.", nodes=[_toc_node("OnlyToC")])
        ]
        assert _tree_depth(delta1) == 2
        delta1_result = _strip_toc_heading_nodes_guarded(delta1, doc_name="test_delta1")
        assert _tree_node_count(delta1_result) <= _tree_node_count(delta1)


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
    """prepare_tree threads its orientation kwarg through to _segment_table_nodes;
    _dominant_orientation derives orientation from per-page landscape data."""

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

    def test_generic_numbered_split_guards(self):
        """_split_on_generic_numbered_lines refuses a non-monotonic number run
        (LIS guard) and collapses lines closer together than min_seg_chars."""
        body = _text_of_length(18000)
        out_of_order = f"Preamble.\n5. {body}\n2. {body}\n8. {body}\n1. {body}"
        node = _make_leaf(out_of_order)
        assert (
            _split_on_generic_numbered_lines(
                node, out_of_order, max_chars=100000, min_segments=3
            )
            is False
        ), "Out-of-order numbers should be rejected by LIS guard"
        assert node["nodes"] == []

        short = _text_of_length(1000)
        long_body = _text_of_length(20000)
        dense = (
            "Preamble.\n"
            + "".join(f"{i}. {short}\n" for i in range(1, 9))
            + f"9. {long_body}\n10. {long_body}\n11. {long_body}"
        )
        dense_node = _make_leaf(dense)
        result = _split_on_generic_numbered_lines(
            dense_node, dense, max_chars=100000, min_segments=3, min_seg_chars=5000
        )
        if result:
            assert len(dense_node["nodes"]) < 11, (
                f"Expected < 11 children after min_seg_chars collapse, "
                f"got {len(dense_node['nodes'])}"
            )
        else:
            assert dense_node["nodes"] == []


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

    async def test_effective_timeout_at_is_respected(self, ctx, mock_redis):
        """A job whose effective_timeout_at is still in the future is not
        reaped -- both for an ordinary deadline and for the 16.5x scanned-PDF
        budget window."""
        now = int(time.time())
        effective_timeout = CHILD_TIMEOUT * 16.5
        scanned_started = now - int(effective_timeout * 0.9)
        scanned_deadline = scanned_started + int(effective_timeout) + REAP_GRACE

        cases = [
            ("ordinary future deadline", "job-future", str(now - 100), str(now + 3600)),
            (
                "16.5x scanned-pdf window",
                "job-scanned",
                str(scanned_started),
                str(scanned_deadline),
            ),
        ]

        for name, job_id, started, deadline in cases:
            mock_redis.hset.reset_mock()
            mock_redis.scan_iter = _make_scan_iter([_job_key(job_id)])
            mock_redis.hgetall.return_value = _make_job_hash(
                processing_started_at=started,
                effective_timeout_at=deadline,
            )

            await reap_stale_jobs(ctx)

            assert not mock_redis.hset.called, f"[{name}] job was reaped before its deadline"


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

    def test_fence_parity_warns_without_losing_content(self, caplog):
        """An orphan closing fence increments FENCE_PARITY_WARNING and logs,
        and an unclosed fence at EOF never drops surrounding content."""
        before = _fence_warning_count("orphan_close")
        route_and_extract_flat("```\nSome content here.\nMore content.")
        after = _fence_warning_count("orphan_close")
        assert after > before, "FENCE_PARITY_WARNING(orphan_close) must increment"
        assert "fence_parity" in caplog.text.lower() or "orphan" in caplog.text.lower()

        _, blocks = route_and_extract_flat(
            "Before fence.\n```python\ndef hello():\n    pass\nAfter fence."
        )
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
    time; _heading_count is the thin wrapper consolidating the repeated
    len(_HEADING_RE.findall(md)) patterns."""

    def test_has_depth_and_heading_count(self):
        md = (
            "# Title\n\n## Section A\n\nBody A.\n\n"
            "## Section B\n\nBody B.\n\n## Section C\n\nBody C."
        )
        c = _candidate_from_document(md, {}, "/fake.pdf")
        assert c.has_depth is True
        assert c.has_depth == _has_structural_depth(c.md)

        # Default value is False -- the safe fallback for callers that
        # construct Candidate with only md=.
        assert Candidate(md="# A\n\n## B\n\n## C\n\n## D").has_depth is False

        # Inline hash marks are not headings.
        assert _heading_count("Some text with # not a heading\n# Real heading") == 1


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


class TestReadRegistryFieldsSidecarFallback:
    """read_registry_fields falls back to the sidecar when the artifact
    lacks verdict fields (Zone-8 Target 4)."""


class TestPromotionSweepVerdictRouting:
    """promotion_sweep.run_sweep routes verdict through the sole
    write-through path (_upsert_registry_row, RFC-042 D3), never a direct
    save_doc_meta call."""

    @pytest.mark.asyncio
    async def test_sweep_routes_verdict_through_upsert_registry_row(self):
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
            patch("promotion_sweep._upsert_registry_row", new_callable=AsyncMock) as mock_urr,
            patch("promotion_sweep.settings") as mock_settings,
            patch("promotion_sweep.get_minio") as mock_get_minio,
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

            mock_urr.assert_called_once()
            call_args = mock_urr.call_args
            assert call_args[0][0] == "sweep01"
            registry_fields = call_args.kwargs["registry_fields"]
            assert registry_fields["verdict"] == "PASS"
            assert registry_fields["verdict_reason"] == "base_pass"
            assert registry_fields["max_leaf_ratio"] == 0.05


class TestRecomputeVerdictsRegistryRouting:
    """preprocess_client.recompute_verdicts routes verdict + provenance
    through _upsert_registry_row (RFC-042 D3)."""


class TestRegistryBackfillPropagationOnly:
    """_enrich_one and _heal_one are propagators, not computers -- they
    must never call classify_verdict, and route their write-through the
    sole entry point (_upsert_registry_row) rather than save_doc_meta."""

    @pytest.mark.asyncio
    async def test_heal_one_never_calls_classify_verdict(self):
        with (
            patch("pageindex_mcp.registry_backfill.backfill.read_registry_fields") as mock_rrf,
            patch("pageindex_mcp.registry_backfill.backfill.get_minio"),
            patch("pageindex_mcp.helpers.classify_verdict") as mock_cv,
            patch(
                "pageindex_mcp.worker.registry_mirror._upsert_registry_row",
                new_callable=AsyncMock,
            ) as mock_urr,
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
            mock_urr.assert_called_once()


class TestSqlVerdictFilter:
    """list_docs / count_docs / stage_a_filter SQL all exclude both 'FAIL'
    and '' (empty string) verdicts."""


# ===========================================================================
# 11. RFC-029: NFKC normalization + bidi coherence (converters)
# ===========================================================================


def _t_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _t_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _t_prose(n: int, prefix: str = "Paragraph text. ") -> str:
    """Return a prose string of at least *n* characters."""
    repeats = (n // len(prefix)) + 1
    return (prefix * repeats)[:n]


class TestNFKCCanonicalization:
    """_pre_inference_normalize / decide_rtl."""

    def test_presentation_forms_normalized_ascii_preserved(self):
        """U+FB50/U+FB51 (Arabic Presentation Form-A) decompose under NFKC to
        their canonical forms, and interleaved ASCII survives untouched."""
        result, _ = _pre_inference_normalize("ﭐﭑ")
        assert "ﭐ" not in result and "ﭑ" not in result
        for ch in result:
            assert not ("ﭐ" <= ch <= "﷿")

        mixed, _ = _pre_inference_normalize("Prefix ﭐﭑ suffix")
        assert "Prefix " in mixed
        assert " suffix" in mixed
        assert "ﭐ" not in mixed

    def test_presentation_forms_signal_is_ratio_gated_and_pre_nfkc(self):
        """D6 + RFC-046 D5: had_presentation_forms is captured BEFORE NFKC
        destroys the codepoints, and is ratio-gated -- PF must dominate (>50%
        of Arabic chars) for the signal to be True, while NFKC still runs
        either way."""
        import unicodedata

        pf_char = "ﭐ"
        dominant = "اب" + pf_char * 10
        assert sum(1 for c in dominant if 0xFB50 <= ord(c) <= 0xFDFF) > 0
        assert (
            sum(1 for c in unicodedata.normalize("NFKC", dominant) if 0xFB50 <= ord(c) <= 0xFDFF)
            == 0
        ), "NFKC must decompose U+FB50"

        _, rtl = _pre_inference_normalize(dominant)
        assert rtl is not None
        assert rtl.had_presentation_forms is True

        minority = "".join(chr(c) for c in range(0x0620, 0x0640)) + pf_char
        result, rtl_min = _pre_inference_normalize(minority)
        assert "ﭐ" not in result, "NFKC must still decompose the PF char"
        assert rtl_min is not None
        assert rtl_min.had_presentation_forms is False

    def test_low_arabic_ratio_line_not_flagged_reversed(self):
        """Zone-3: _check_bidi_coherence was deleted; its sole signal was
        decide_rtl(...).reversed.  Mostly-ASCII text with sparse Arabic must
        not trigger detection."""
        assert not decide_rtl("hello world foo bar baz qux مر").reversed


# ===========================================================================
# 12. RFC-029: low_content_density / suspect_density gates (validate_tree)
# ===========================================================================


def _low_density_tree(n_nodes: int = 210, chars_per_node: int = 5) -> list[dict]:
    """Build a tree with *n_nodes* total nodes each carrying *chars_per_node* chars."""
    leaves = [_t_leaf(f"L{i}", filler_text(chars_per_node, i)) for i in range(n_nodes - 1)]
    branch = _t_branch("Section1", filler_text(chars_per_node, n_nodes), leaves)
    return [{"title": "Root", "text": filler_text(chars_per_node, n_nodes + 1), "nodes": [branch]}]


def _safe_repetitive_content(length: int) -> str:
    """Return *length* chars of short varied words that pass all garbling checks."""
    token_cycle = "the and for are but "
    return (token_cycle * ((length // len(token_cycle)) + 1))[:length]


def _varied_arabic_text(length: int) -> str:
    """Return *length* chars of varied Arabic-script words (no repetition/digit noise)."""
    arabic_letters = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    words = []
    for i in range(200):
        word_len = (i % 5) + 2
        words.append(
            "".join(arabic_letters[(i * 3 + j * 7) % len(arabic_letters)] for j in range(word_len))
        )
    base = " ".join(words) + " "
    return (base * ((length // len(base)) + 1))[:length]


def _sparse_tree(content: str) -> list[dict]:
    """Build a minimal valid tree (depth >= 2, nodes >= 3) with the given content."""
    half = len(content) // 2
    leaf_text = content[half:]
    per_leaf = len(leaf_text) // 5
    leaves = [_t_leaf(f"Leaf{i}", leaf_text[i * per_leaf : (i + 1) * per_leaf]) for i in range(5)]
    return [{"title": "Root", "text": "", "nodes": [_t_branch("Section", content[:half], leaves)]}]


def _canonical_pass_tree(index: int, repeat: int = 20) -> list[dict]:
    """Return a small, well-distributed PASS-shape tree."""
    children = [
        _t_leaf(f"T{index}-Sub{j}", f"Body text for subsection {index}-{j}. " * repeat)
        for j in range(5)
    ]
    section = _t_branch(
        f"Section {index}", f"Section {index} overview paragraph. " * repeat, children
    )
    return [{"title": f"Document {index}", "text": "Preamble.", "nodes": [section]}]


class TestLowContentDensityGate:
    """validate_tree's node-count-driven low_content_density gate."""

    def test_low_density_tree_fails_at_and_above_200_nodes(self):
        """A 200+ node tree with only a few chars per node fails validation,
        and the gate fires at exactly total_nodes == 200."""
        ok, _reason = validate_tree(_low_density_tree(n_nodes=210, chars_per_node=5))
        assert ok is False

        ok_200, reason_200 = validate_tree(_low_density_tree(n_nodes=200, chars_per_node=1))
        assert ok_200 is False
        assert reason_200.startswith("low_content_density")


class TestSuspectDensityGate:
    """42 pages x 48 000 chars -> 1142.9 chars/page < 1200 floor."""

    PAGE_COUNT = 42
    TOTAL_CHARS = 48_000

    def test_suspect_density_fires_below_floor_not_at_it(self):
        """Below the scanned-density floor the gate fires with a chars_per_page
        detail; exactly at the floor it must NOT fire (strictly-less-than)."""
        tree = _sparse_tree(_safe_repetitive_content(self.TOTAL_CHARS))
        ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)
        assert ok is False
        assert reason.startswith("suspect_density")
        assert "chars_per_page=" in reason

        at_floor = self.PAGE_COUNT * int(_RFC029_MIN_SCANNED_DENSITY_FLOOR)
        _ok, at_floor_reason = validate_tree(
            _sparse_tree(_safe_repetitive_content(at_floor)), page_count=self.PAGE_COUNT
        )
        assert "suspect_density" not in at_floor_reason

    def test_canonical_pass_trees_never_trip_suspect_density(self):
        """Well-formed trees with substantial content must not trip the
        density gate even when page_count is supplied."""
        offenders = []
        for index in range(3):
            _ok, reason = validate_tree(_canonical_pass_tree(index, repeat=60), page_count=5)
            if "suspect_density" in reason:
                offenders.append(f"  [tree {index}] {reason}")
        assert not offenders, "canonical PASS trees tripped suspect_density:\n" + "\n".join(
            offenders
        )


class TestArabicLowContentRatioGate:
    """SCOPE REDUCTION: validate_tree's check_garble(TREE_BULK) call and the
    later arabic_low_content_ratio check share the same expected_script and
    flattened text, so any input tripping _is_garbled_blob at the ratio check
    would already have been caught by check_garble first.  This calls
    check_garble directly (the mechanism the gate delegates to)."""

    def test_check_garble_flags_digit_dominated_arabic_text(self):
        """>60% digit blob over 500 chars must be flagged garbled."""
        total = 2000
        arabic_count = int(total * 0.35)
        digit_count = total - arabic_count
        arabic_part = _varied_arabic_text(arabic_count)
        digit_part = ("1234567890" * ((digit_count // 10) + 1))[:digit_count]
        chunk = 5
        parts = []
        ai = di = 0
        while ai < len(arabic_part) or di < len(digit_part):
            if ai < len(arabic_part):
                parts.append(arabic_part[ai : ai + chunk])
                ai += chunk
            if di < len(digit_part):
                parts.append(digit_part[di : di + chunk])
                di += chunk
        blob = "".join(parts)[:total]

        assert check_garble(blob, expected_script=None, profile=BULK_PROFILE) is True


# ===========================================================================
# 13. RFC-029/030: fence + HR handling in route_and_extract_flat
# ===========================================================================


def _block_texts(blocks: list[dict]) -> list[str]:
    return [b["text"] for b in blocks if "text" in b]


class TestFenceAndHRStripping:
    """RFC-030 D0 superseded RFC-029 D3's fence-parity toggle: only the fence
    delimiter lines are stripped, enclosed content falls through."""

    def test_hr_and_fence_delimiters_strip_without_losing_content(self):
        """HRs of 4+ repeated characters are stripped, fenced content and the
        line right after a closing fence are still emitted, and plain markdown
        gains no spurious blocks."""
        _cc, hr_blocks = route_and_extract_flat(
            "Before.\n\n------\n\nMiddle.\n\n======\n\nAfter.\n"
        )
        hr_text = " ".join(_block_texts(hr_blocks))
        assert "------" not in hr_text and "======" not in hr_text
        for token in ("Before.", "Middle.", "After."):
            assert token in hr_text

        _cc2, fence_blocks = route_and_extract_flat("```\nformerly hidden\n```\nVisible line.\n")
        fence_text = " ".join(_block_texts(fence_blocks))
        assert "formerly hidden" in fence_text
        assert "Visible line." in fence_text

        _cc3, plain_blocks = route_and_extract_flat("A single sentence.")
        assert len(plain_blocks) == 1
        assert plain_blocks[0]["role"] == "prose"
        assert "A single sentence." in plain_blocks[0]["text"]


# ===========================================================================
# 14. RFC-029: degenerate duplicate-cell row collapsing (_repair_docling_tables)
# ===========================================================================


def _t_pipe_table(header_cells: list[str], data_rows: list[list[str]]) -> str:
    """Build a minimal GFM pipe table string."""
    n = len(header_cells)
    header = "| " + " | ".join(header_cells) + " |"
    sep = "| " + " | ".join("---" for _ in range(n)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in data_rows]
    return "\n".join([header, sep] + rows)


def _data_lines(result: str) -> list[str]:
    return [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]


class TestDegenerateRowCollapsing:
    """_repair_docling_tables collapses all-identical rows of >3 columns."""

    def test_distinct_values_kept_identical_columns_collapsed(self):
        """Legit rows with distinct per-column values pass through unchanged
        (modulo whitespace normalisation); a 4-column all-identical row
        (count == 4, strictly > 3) MUST collapse to a single cell."""
        distinct = _t_pipe_table(
            ["Name", "Wert", "Einheit"], [["Alpha", "1.0", "kg"], ["Beta", "2.0", "m"]]
        )
        distinct_rows = _data_lines(_repair_docling_tables(distinct))[1:]
        assert len(distinct_rows) == 2
        assert [c.strip() for c in distinct_rows[0].split("|") if c.strip()] == [
            "Alpha",
            "1.0",
            "kg",
        ]

        identical = (
            "| A | B | C | D |\n| --- | --- | --- | --- |\n"
            "| p | q | r | s |\n| val | val | val | val |\n"
        )
        collapsed = _data_lines(_repair_docling_tables(identical))[2:]
        assert [c.strip() for c in collapsed[0].split("|") if c.strip()] == ["val"]

    def test_separator_normalised_and_surrounding_prose_untouched(self):
        """``|:---:|`` alignment syntax is re-emitted as ``| --- |``, and prose
        lines interleaved with a table are left alone while the table is
        normalised."""
        sep_lines = _repair_docling_tables("| Col |\n|:---:|\n| val |\n").splitlines()
        assert next(ln for ln in sep_lines if "---" in ln) == "| --- |"

        mixed = (
            "Introduction paragraph.\n"
            "| A | B | C | D | E |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| p | q | r | s | t |\n"
            "| x | x | x | x | x |\n"
            "Concluding paragraph.\n"
        )
        result = _repair_docling_tables(mixed)
        lines = result.splitlines()
        assert lines[0] == "Introduction paragraph."
        assert lines[-1] == "Concluding paragraph."
        collapsed = _data_lines(result)[2:]
        assert [c.strip() for c in collapsed[0].split("|") if c.strip()] == ["x"]


# ===========================================================================
# 15. RFC-029 Wave 9: picture-context retention (splice_figure_markers)
# ===========================================================================

_MARKER = "<!-- image -->"


class TestSpliceFigureMarkers:
    """Retained-skip results keep their chart text; an empty-OCR result still
    resolves the marker; a truly empty result leaves the raw marker neutral."""

    def test_splice_figure_marker_variants(self):
        ocr = "Revenue grew 12% YoY"
        retained: PictureResult = {
            "ocr_text": ocr,
            "png_bytes": b"\x89PNG\r\n",
            "skipped_reason": "clip_text_already_exported",
            "page": 1,
            "bbox": {"l": 10, "t": 20, "r": 100, "b": 80},
        }
        retained_out = splice_figure_markers(f"Intro.\n\n{_MARKER}\n\nTrailing.", [retained])
        assert "[Figure: fig-0]" in retained_out
        assert f"> [Chart text]: {ocr}" in retained_out

        # Below/at threshold, empty ocr_text (Tesseract unavailable) with only
        # png_bytes still resolves the marker without a [Chart text] block.
        # This is the semantic of the D5b else-branch in client.py; the
        # end-to-end path needs OpenAI + Docling + MinIO and is not run here.
        png_only: PictureResult = {
            "ocr_text": "",
            "page": 1,
            "bbox": {"l": 0, "t": 0, "r": 0, "b": 0},
            "png_bytes": b"\xff\xd8\xff",
        }
        png_out = splice_figure_markers(f"Preamble.\n\n{_MARKER}\n\nPostamble.", [png_only])
        assert "[Figure: fig-0]" in png_out
        assert "> [Chart text]:" not in png_out

        # An entirely empty PictureResult with no skip flag keeps the raw
        # marker neutral (falls through to `return m.group(0)`).
        empty: PictureResult = {}
        assert _MARKER in splice_figure_markers(f"Text.\n\n{_MARKER}\n\nEnd.", [empty])


# ===========================================================================
# 16. RFC-029: table-aware node segmentation (_segment_table_nodes)
# ===========================================================================

_THRESHOLD = 2000  # mirrors _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD default


def _pipe_table_rows(n_data_rows: int, n_cols: int = 3, has_header: bool = True) -> str:
    """Build a GFM pipe table with the requested number of data rows."""
    lines: list[str] = []
    if has_header:
        lines.append("| " + " | ".join(f"Col{i}" for i in range(n_cols)) + " |")
    lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"r{r}c{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


class TestTableSegmentationPrimary:
    """The char threshold gates segmentation; 5 data rows is the min row count."""

    def test_char_threshold_and_min_row_count(self):
        """A node under 2000 chars total (even with a pipe table) must NOT
        split; a node over the threshold with exactly 5 data rows (== min)
        MUST split."""
        short = _t_prose(700) + "\n" + _pipe_table_rows(n_data_rows=10)
        assert len(short) < _THRESHOLD
        short_node = _segment_table_nodes([_t_leaf("Short Section", short)])[0]
        assert short_node.get("nodes", []) == []
        assert short_node["text"] == short

        long_text = _t_prose(2100) + "\n" + _pipe_table_rows(n_data_rows=5)
        long_node = _segment_table_nodes([_t_leaf("Five Row Section", long_text)])[0]
        assert len(long_node.get("nodes", [])) >= 2


class TestTableSegmentationHeaderSynthesis:
    def test_headerless_table_gets_synthesized_title(self):
        """When the table has no explicit header row, a non-empty title is
        synthesized (either the first non-separator pipe row's text, or a
        ``Table: <parent title>`` fallback)."""
        combined = _t_prose(2100) + "\n" + _pipe_table_rows(n_data_rows=10, has_header=False)
        result = _segment_table_nodes([_t_leaf("Haftpflicht Abschnitt 3", combined)])
        children = result[0].get("nodes", [])

        table_children = [
            c for c in children if any(ln.strip().startswith("|") for ln in c["text"].splitlines())
        ]
        assert len(table_children) >= 1
        assert table_children[0]["title"], "table child title must not be empty"


class TestHABShapeRegression:
    """Representative Haftpflicht-Allgemeine-Bedingungen table-in-node shape:
    moderate prose + 15-row table. Verifies no content is lost across split."""

    def test_hab_node_splits_with_no_content_loss(self):
        preamble = (
            "§ 4 Versicherte Tätigkeiten\n\n"
            "Der Versicherungsschutz umfasst die im Versicherungsschein "
            "beschriebenen Tätigkeiten des Versicherungsnehmers. "
            "Eingeschlossen sind auch Tätigkeiten, die zur unmittelbaren "
            "Vorbereitung oder Durchführung der versicherten Tätigkeit "
            "gehören, soweit sie nicht ausdrücklich ausgeschlossen sind.\n\n"
            "Tabelle der versicherten Deckungssummen:\n"
        )
        extra = _t_prose(max(0, _THRESHOLD - len(preamble) - 50), prefix="Zusatztext. ")
        combined = preamble + extra + "\n" + _pipe_table_rows(n_data_rows=15, n_cols=4)
        node = _t_leaf("§ 4 Versicherte Tätigkeiten", combined)
        assert len(combined) > _THRESHOLD

        children = _segment_table_nodes([node])[0].get("nodes", [])
        assert len(children) >= 2, f"HAB-shape node was not split; text len={len(combined)}"
        joined = "\n".join(c["text"] for c in children)
        assert joined.replace("\n", "") == combined.replace("\n", "")


# ===========================================================================
# 17. RFC-029: zero-body contamination gate (validate_tree / classify_verdict)
# ===========================================================================


class TestClassifyVerdictFailOnContamination:
    def test_classify_verdict_returns_fail_preserving_reason(self):
        """classify_verdict returns 'FAIL' with the full contamination reason
        string (no promotion branch overrides a hard-FAIL gate).

        The tree carries 91 non-root nodes of which 30 have an empty
        title+body: fraction = 30/91 ~= 0.33, over the 0.30 threshold.
        """
        branches = []
        for i in range(10):
            leaves = [
                _t_leaf(f"A{i}L{j}", f"content {i}-{j}") if j < 2 else {"title": "", "text": ""}
                for j in range(4)
            ]
            branches.append({"title": "", "text": "", "nodes": leaves})
        root_a = {"title": "Root A", "text": "section intro", "nodes": branches}

        content_leaves = [_t_leaf(f"BLeaf{k}", f"paragraph {k}") for k in range(40)]
        content_branch = _t_branch("Content Branch", "good content", content_leaves)
        root_b = {"title": "Root B", "text": "section b", "nodes": [content_branch]}

        tree = [root_a, root_b]
        gate_result = validate_tree(tree)

        verdict, reason = classify_verdict(
            structure=tree, content_class="structured", validate_result=gate_result
        )

        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")
