#!/usr/bin/env python3
"""Source-code invariant gate.

Every check here is a *static* invariant over the repository's own source
text: "no module imports X", "this call ordering holds", "the package facade
matches the frozen surface", "no naive ``block.get('text')``".  None of them
exercises runtime behaviour, so none of them belongs in pytest: they run
faster here, report every violation at once instead of one assertion per
collection slot, and fail with a ``path:line: ID: message`` line a reviewer
can act on directly.

Provenance: this script replaces six pytest files (202 collected tests)
consolidated by the ICR-97 test-budget work --
``test_architecture_guards.py``, ``test_facade_surface_guard.py``,
``test_rfc042_measurement_guard.py``, ``test_rfc045_facade_measurement.py``,
``test_no_naive_block_text.py`` and ``test_rfc_lifecycle_lint.py``.  The
invariants are unchanged; only their home is.  ``tests/test_source_invariants.py``
now tests *this script*, which is the correct test surface.

Usage::

    uv run python scripts/gates/source_invariants.py          # whole repo
    uv run python scripts/gates/source_invariants.py --root . # explicit root

Exit code 0 when clean, 1 when any invariant is violated.

Stdlib only by design -- importing ``pageindex_mcp`` would make a static gate
depend on the package being importable, which is exactly the failure mode a
static gate is supposed to survive.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from dataclasses import dataclass
from functools import cache

# ===========================================================================
# FROZEN_SURFACE -- the frozen package facade (RFC-045 Requirement 4)
# ===========================================================================
#
# THIS IS THE CANONICAL HOME of FROZEN_SURFACE. It moved here from
# tests/test_facade_surface_guard.py when that file was folded into this gate.
# Edit it HERE (and nowhere else) when a facade export changes deliberately;
# the RFC-045 facade shrink updates this constant and REMOVED_SURFACE below
# in the same commit that removes the export.
#

# Frozen facade surface as of RFC-045 (2026-09-07), pre-shrink baseline.
# 412 entries across 9 packages:
#   client               13
#   converters          131
#   helpers              94
#   metrics              63
#   registry             19
#   registry_backfill    19
#   storage              35
#   tools                 5
#   worker               37
#
# NOTE: the RFC-045 measurement covered 8 packages / 407 entries and did not
# include ``tools`` (5 entries, all consumed by attribute access from
# ``server.py:29-33``). The freeze covers all 9.
# RFC-045 removals, recorded per wave as each executes. Property 2 has two
# halves: the entry leaves ``__all__`` (covered by the frozen-literal check
# above) *and* the binding leaves the package namespace. Only the first half was
# tested before RFC-045 wave 0; ``TestRemovedBindingsAreGone`` covers the second.
REMOVED_SURFACE: dict[str, tuple[str, ...]] = {
    # wave 1 -- client
    "client": (
        "MIN_STANDALONE_IMAGE_MD_CHARS",
        "RecoveryMixin",
        "TREE_PATH_PICTURE_SPLICE_ENABLED",
    ),
    # wave 2 -- storage
    "storage": ("SIDECAR_VERSION",),
    # wave 3 -- registry_backfill
    "registry_backfill": (
        "_is_fat",
        "_load_meta",
        "_preflight_checks",
        "_prepare_metas",
        "_record_reconcile_heartbeat",
        "main",
        "read_registry_fields",
        "upsert_doc",
    ),
    # wave 4 -- worker
    "worker": (
        "JOB_TTL",
        "KILL_GRACE_SECONDS",
        "_VERDICT_RETRY_KEY_PREFIX",
        "_VERDICT_RETRY_TTL_S",
        "_dlq_push_on_final_attempt",
        "_enqueue_verdict_retry",
        "_mirror_bridged_incr",
        "_mirror_bridged_set",
        "_reconcile_registry_drift_cron",
    ),
    # wave 5 -- helpers
    "helpers": (
        "ExtractionSnapshot",
        "_GateFn",
        "_JOINING_TYPE",
        "_count_empty_body_nodes",
        "_flat_is_pipe_row",
        "_flat_is_separator_row",
        "_flat_split_pipe_row",
        "_flat_verbalize_rows",
        "_looks_like_toc_page",
        "_rag_inner",
        "_walk_leaves",
        "flag_empty_cells",
    ),
}

FROZEN_SURFACE: dict[str, tuple[str, ...]] = {
    "client": (
        "CustomPageIndexClient",
        "LLMTransientFailure",
        "_remote_image_to_markdown",
        "_remote_pdf_to_markdown",
        "apply_image_ext_content_class_override",
        "configure_litellm",
        "flush_litellm_tracing",
        "get_openai_client",
        "resolve_llm_provider",
        "validate_llm_config",
    ),
    "converters": (
        "BIDI_NORM_VERSION",
        "BlobKind",
        "Candidate",
        "ConverterChainEntry",
        "ConverterFailurePolicy",
        "FuturesTimeoutError",
        "LANDSCAPE_CHAR_THRESHOLD",
        "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS",
        "MAX_LANDSCAPE_PAGES",
        "PictureResult",
        "PreClassification",
        "RtlDecision",
        "ScriptContext",
        "StageRecord",
        "TessdataUnavailableError",
        "_AR_ARTICLE_RE",
        "_AR_COMMON_WORDS",
        "_AR_LETTER_RE",
        "_AR_MARKER_CAPTURE_RE",
        "_AR_PART_RE",
        "_AR_SCRIPT_RE",
        "_AR_WORD_RE",
        "_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S",
        "_COVERAGE_EXEMPT_NO_TEXT_LAYER",
        "_D7_FITZ_FALLBACK_ENABLED",
        "_DECORATIVE_ICON_MIN_DIM_PT",
        "_DOC_TEXT_FALLBACK_MIN_CHARS",
        "_GATE_CONFIG",
        "_HEADING_RE",
        "_IMAGE_ENRICH_CONCURRENCY",
        "_IMAGE_MARKER",
        "_LATIN_LANGS",
        "_MAX_FULLPAGE_PICTURE_OCR_REGIONS",
        "_PAGE_ROTATION_DETECTION_ENABLED",
        "_PICTURE_OCR_MIN_CHARS",
        "_PICTURE_PAGE_COVERAGE_THRESHOLD",
        "_RFC029_TABLE_DEDUP_ENABLED",
        "_RFC029_TABLE_MIN_COLLAPSE_COLS",
        "_VERDICT_RANK",
        "_add_vlm_descriptions",
        "_apply_outline_levels",
        "_arabic_readability_score",
        "_bbox_to_fitz_rect",
        "_build_candidate",
        "_build_pdf_pipeline_options",
        "_candidate_from_document",
        "_clip_text_contained",
        "_collapse_spaced",
        "_collect_heading_pages",
        "_collect_picture_regions",
        "_containment_depths",
        "_crop_page_region",
        "_detect_pdf",
        "_docling_chunk_worker",
        "_docling_converter",
        "_document_level_text_fallback",
        "_fallback_and_recover_pictures",
        "_figure_desc_inline",
        "_fix_fi_hash_substitution",
        "_has_structural_depth",
        "_heading_count",
        "_inject_arabic_structural_headings",
        "_inject_english_article_headings",
        "_inject_german_clause_headings",
        "_is_arabic_char",
        "_is_numeric_extension",
        "_landscape_pages_below_threshold",
        "_landscape_rasterize_rotate_reextract",
        "_max_heading_level",
        "_md_to_structure",
        "_normalize_for_containment",
        "_normalize_indented_headings",
        "_normalize_pdf_page_rotation",
        "_outline_norm",
        "_page_rotation_correction_info",
        "_patch_hierarchical_infer",
        "_pdf_inspector_available",
        "_pdf_to_markdown_docling_chunked",
        "_pre_inference_normalize",
        "_rasterize_rotate_page",
        "_read_pdf_outline",
        "_recover_heading_depth",
        "_recover_picture_results",
        "_recover_picture_text",
        "_relevel_by_containment",
        "_relevel_headings",
        "_repair_docling_tables",
        "_repromote_numbered_headings",
        "_run_docling_chunk_with_timeout",
        "_run_pdf_inspector",
        "_run_stages",
        "_segment_label",
        "_splice_landscape_fallback",
        "_split_alnum",
        "_split_run_together_headings",
        "_tag_landscape_pages_for_fallback",
        "_tesseract_ocr_image",
        "_text_layer_has_content",
        "_title_matches",
        "_try_download_tessdata",
        "_word_has_reversed_morphology",
        "apply_rtl",
        "as_chain_entry",
        "chunked_docling_timeout_s",
        "decide_rtl",
        "detect_garble",
        "detect_lang_from_text_layer",
        "detect_ocr_langs",
        "docx_to_markdown",
        "ensure_tessdata",
        "html_to_markdown_with_images",
        "image_to_markdown",
        "libreoffice_to_pdf",
        "merge_lang_sources",
        "normalize_dashes",
        "numbering_depth",
        "pdf_markdown_converters",
        "pdf_to_markdown",
        "pdf_to_markdown_docling",
        "pptx_to_markdown",
        "preclassify_document",
        "probe_conversion_route",
        "rasterize_pdf_pages",
        "rasterize_pdf_pages_fitz",
        "reconstruct_bidi_order",
        "splice_figure_markers",
        "splice_picture_text_for_tree",
        "tesseract_ocr_pdf_pages",
        "vlm_extract_markdown",
        "xlsx_to_markdown",
        "zdr_egress_gate",
    ),
    "helpers": (
        "BULK_PROFILE",
        "BlobKind",
        "BlockTextPurpose",
        "Candidate",
        "ENGINE_RELIABILITY_ORDER",
        "ExtractionState",
        "FEATURE_WIRINGS",
        "FLAT_MARKDOWN_PROFILE",
        "FeatureWiring",
        "GATES",
        "GATE_TABLE",
        "GarbleConfig",
        "GarbleProfile",
        "GarbleReport",
        "GateOutcome",
        "GateSpec",
        "HALLUCINATION_CHAR_RATIO",
        "HARD_FAIL_DEFECTS",
        "LowQualityTreeError",
        "REASON_POLICY",
        "RecoveryOutcome",
        "Route",
        "ScriptContext",
        "TreeDefect",
        "TreeGateResult",
        "TreeSignals",
        "VerdictResult",
        "VerdictThresholds",
        "_GATE_PRIORITY",
        "_ReasonPolicy",
        "_UNSET",
        "_Unset",
        "_extract_page_hits",
        "_flat_block_primary_text",
        "_flat_parse_table",
        "_flat_search_text",
        "_flatten_tree_text",
        "_forward_fill_leading_column",
        "_garble_check_nodes",
        "_garble_config",
        "_garble_ratio",
        "PF_SIGNAL_RATIO",
        "_has_any_presentation_form",
        "_infer_presentation_forms",
        "_pf_ratio",
        "_infer_script",
        "_is_morphologically_nonsense",
        "_llm",
        "_node_char_count",
        "_node_text_parts",
        "_rag",
        "_script_from_filename",
        "_segment_table_nodes",
        "_strip_text",
        "_strip_toc_heading_nodes_guarded",
        "_tree_depth",
        "_tree_is_reordered",
        "_tree_max_leaf_ratio",
        "_tree_node_count",
        "_try_content_class_promotion",
        "_try_flat_promotion",
        "_try_ocr_promotion",
        "_try_small_doc_promotion",
        "apply_promotions",
        "arbitrate",
        "block_text",
        "classify_verdict",
        "compute_image_enrichment_ratio",
        "compute_verdict",
        "decide_route",
        "decide_rtl",
        "detect_garble",
        "detect_regression",
        "doc_text",
        "evaluate_gates",
        "finalize_gate_and_route",
        "flat_doc_view",
        "hash_pipe_ratio",
        "normalize_dashes",
        "normalize_for_garble",
        "ocr_noise_ratio",
        "prepare_tree",
        "reset_verdict_thresholds",
        "route_and_extract_flat",
        "split_oversized_leaf_nodes",
        "stitch_continuation_tables",
        "table_is_rtl",
        "validate_feature_wirings",
        "validate_tree",
    ),
    "metrics": (
        "ACTIVE_UPLOADS",
        "AGPL_FALLBACK_TOTAL",
        "ARABIC_HEADING_INJECTION_REVERTED",
        "ARQ_QUEUE_DEPTH",
        "BIDI_RENORM_SKIPPED",
        "CACHE_ERRORS",
        "CONTENT_TYPE",
        "CONVERTER_CHILD_OOM_TOTAL",
        "CONVERTER_CHILD_TIMEOUT_TOTAL",
        "CONVERTER_PEAK_RSS_KIB",
        "DOCLING_VERSION_SKEW",
        "DOCUMENTS_TOTAL",
        "FENCE_PARITY_WARNING",
        "FLAT_DOCS_TOTAL",
        "HR3_EGRESS_BLOCKED_TOTAL",
        "IMAGE_DESCRIBE_FAILURES",
        "LLM_CALLS",
        "LLM_DURATION",
        "LOW_QUALITY_TREES",
        "MCP_AUTH_DISABLED",
        "MINIO_DURATION",
        "MINIO_OPS",
        "OCR_ESCALATION_TOTAL",
        "PDF_EXTRACT_FALLBACKS",
        "PDF_INSPECTOR_CLASSIFICATIONS",
        "PDF_INSPECTOR_FORCED_OCR",
        "PDF_INSPECTOR_LATENCY",
        "PDF_PRIMARY_CONVERTER_FAILURES",
        "RAG_DURATION",
        "RAG_PARSE_FAILURES",
        "RAG_SEARCHES",
        "RAW_UPLOAD_FAILURES",
        "REGISTRY",
        "REGISTRY_CONSISTENCY_DEGRADED",
        "REGISTRY_FALLBACK_TOTAL",
        "REGISTRY_LAST_WRITE_SUCCESS_TIMESTAMP",
        "REGISTRY_METRICS_SYNC_INTERVAL_S",
        "REGISTRY_WRITE_FAILURES_TOTAL",
        "REMOTE_MD_RENORMALIZED",
        "STAGING_DELETE_FAILURES",
        "TESSDATA_LATIN_FALLBACK_TOTAL",
        "TESSDATA_SYSTEM_CHECK_TOTAL",
        "TESSERACT_OCR_FAILURE_TOTAL",
        "TOC_STRIP_HIGH_CHAR_LOSS",
        "TOC_STRIP_SKIPPED",
        "TOOL_CALLS",
        "TOOL_DURATION",
        "TOOL_ERRORS",
        "UPLOADS",
        "UPLOAD_DURATION",
        "VLM_FALLBACK_TOTAL",
        "WRITE_BARRIER_EXHAUSTED",
        "WRITE_BARRIER_RETRIES",
        "_BRIDGED_METRICS",
        "_BRIDGE_REDIS_PREFIX",
        "_REGISTRY_LAST_WRITE_SUCCESS_REDIS_KEY",
        "_REGISTRY_WRITE_FAILURES_REDIS_KEY",
        "_sync_bridged_metrics_from_redis",
        "_sync_registry_metrics_from_redis",
        "bridge_redis_key",
        "generate_latest",
        "metrics_response",
        "registry_metrics_sync_loop",
    ),
    "registry": (
        "_KNOWN_FACETS",
        "_pool",
        "close_registry",
        "count_docs",
        "count_docs_all",
        "delete_doc",
        "get_doc_sha256",
        "get_pool",
        "init_registry",
        "is_registry_complete",
        "list_all_doc_ids_with_timestamps",
        "list_docs",
        "refresh_known_facets",
        "set_registry_complete",
        "stage_a_filter",
        "stage_b_candidates",
        "sweep_candidates",
        "upsert_doc",
        "upsert_verdict",
    ),
    "registry_backfill": (
        "_backfill",
        "_delete_stale_rows",
        "_drain_verdict_retry_queue",
        "_enrich_one",
        "_heal_orphans",
        "_list_meta_entries",
        "_list_meta_keys",
        "_upsert_all",
        "cleanup_protect_empty_processed_at",
        "reconcile_registry_drift",
        "run_auto_backfill",
    ),
    "storage": (
        "DEFAULT_PRESIGN_REGION",
        "HASH_CACHE_KEY",
        "HASH_OBJECT",
        "PersistenceNotVisibleError",
        "RECONCILE_ETAG_KEY",
        "_WRITE_BARRIER_DELAYS",
        "_apply_route_prefix",
        "_confirm_write_visible",
        "_get_presign_minio",
        "_load_legacy_minio_hash_cache",
        "_read_existing_sidecar",
        "delete_doc",
        "delete_staging",
        "download_staging",
        "get_flat_doc",
        "get_minio",
        "hash_cache_delete",
        "hash_cache_get",
        "hash_cache_set",
        "list_processed_docs",
        "load_doc",
        "presigned_get_url",
        "read_registry_fields",
        "reconcile_etag_delete",
        "reconcile_etag_get_all",
        "reconcile_etag_prune",
        "reconcile_etag_set_many",
        "save_doc",
        "save_doc_meta",
        "save_figure",
        "save_flat_doc",
        "save_raw",
        "upload_staging",
        "wipe_processed",
    ),
    "tools": (
        "find_relevant_documents",
        "get_document",
        "get_document_structure",
        "get_page_content",
        "recent_documents",
    ),
    "worker": (
        "CHILD_GRACE_SECONDS",
        "CHILD_TIMEOUT",
        "ChildErrorClassification",
        "ConverterChildError",
        "ConverterOOMError",
        "DLQ_KEY",
        "JOB_TIMEOUT",
        "MAX_JOBS",
        "MAX_JOBS_CEILING",
        "MAX_JOBS_DEFAULT",
        "MAX_TRIES",
        "REAP_GRACE",
        "WorkerSettings",
        "_CHILD_ERROR_REGISTRY",
        "_DEFAULT_CHILD_CLASSIFICATION",
        "_LLM_TERMINAL_INDICATORS",
        "_TERMINAL_CHILD_REASONS",
        "_classify_llm_failure",
        "_kill_group",
        "_mirror_registry_metric_to_redis",
        "_mirror_registry_write_failure_to_redis",
        "_run_converter_subprocess",
        "_upsert_registry_row",
        "process_document_job",
        "reap_stale_jobs",
        "resolve_max_jobs",
        "shutdown",
        "startup",
    ),
}


# Names reached through a facade by code the test suite never imports, so the
# frozen list is the *only* thing standing between a removal and a runtime
# break. Each entry is (module, name, consumer location).
UNEXERCISED_CONSUMERS: tuple[tuple[str, str, str], ...] = (
    # services/docling-service/ -- a separate deployable. Its Dockerfile copies
    # the full source ("the service imports from pageindex_mcp.converters",
    # services/docling-service/Dockerfile:16) and it is built and shipped
    # independently, so nothing in this suite exercises these imports.
    ("pageindex_mcp.converters", "_docling_converter", "services/docling-service/app.py:87"),
    ("pageindex_mcp.config", "CURRENT_PIPELINE_VERSION", "services/docling-service/app.py:144"),
    ("pageindex_mcp.converters", "pdf_to_markdown_docling", "services/docling-service/app.py:159"),
    ("pageindex_mcp.converters", "image_to_markdown", "services/docling-service/app.py:189"),
    # issue/ -- in-repo reproduction scripts, imported by nothing.
    ("pageindex_mcp.helpers", "_tree_depth", "issue/verify_corpus.py:19"),
    ("pageindex_mcp.helpers", "_tree_node_count", "issue/verify_corpus.py:19"),
    ("pageindex_mcp.helpers", "validate_tree", "issue/verify_corpus.py:19"),
    # issue/ attribute access via ``from pageindex_mcp import converters as C``.
    ("pageindex_mcp.converters", "_build_pdf_pipeline_options", "issue/probe_toc.py:65"),
    ("pageindex_mcp.converters", "_HEADING_RE", "issue/repro_katzen.py:82"),
    ("pageindex_mcp.converters", "_patch_hierarchical_infer", "issue/repro_katzen.py:66"),
    ("pageindex_mcp.converters", "_repromote_numbered_headings", "issue/repro_katzen.py:73"),
    ("pageindex_mcp.converters", "normalize_dashes", "issue/repro_katzen.py:89"),
    ("pageindex_mcp.converters", "_relevel_headings", "issue/repro_katzen.py:89"),
    ("pageindex_mcp.converters", "_relevel_by_containment", "issue/repro_katzen.py:89"),
    ("pageindex_mcp.converters", "_max_heading_level", "issue/repro_katzen.py:95"),
    # Container entrypoint: docker-compose.yml:188, Dockerfile:110, Makefile:84,
    # and hetzner-deployment-service apps/pageindex-mcp/worker-deployment.yaml:27.
    ("pageindex_mcp.worker", "WorkerSettings", "docker-compose.yml:188"),
    # issue/repro_katzen.py reaches _relevel_by_numbering through the SUBMODULE,
    # not the facade: the monolith decomposition (06b2bae) never re-exported it,
    # so the old ``C._relevel_by_numbering`` form was an AttributeError on the
    # ``_max_heading_level(md) < 2`` branch. Fixed 2026-09-08 by importing from
    # converters.headings rather than growing the barrel RFC-045 is shrinking --
    # which is why this pin names a submodule while every other names a facade.
    ("pageindex_mcp.converters.headings", "_relevel_by_numbering", "issue/repro_katzen.py:19"),
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    invariant: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.invariant}: {self.message}"


CHECKS: list[tuple[str, object]] = []


def check(invariant_id: str):
    """Register a check.  The function yields ``Violation``s (or nothing)."""

    def deco(fn):
        CHECKS.append((invariant_id, fn))
        return fn

    return deco


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------


def src_root() -> pathlib.Path:
    return ROOT / "src" / "pageindex_mcp"


def rel(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:  # pragma: no cover - defensive
        return str(path)


@cache
def read(path: pathlib.Path) -> str:
    """Source text of *path*, or ``""`` if it is missing or unreadable.

    A gate must survive a partial tree: a check whose target file is absent
    should report that as a violation, not crash the whole run and take the
    other 34 invariants down with it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


@cache
def tree_of(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(read(path), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
        return None


@cache
def src_files() -> tuple[pathlib.Path, ...]:
    return tuple(sorted(src_root().rglob("*.py")))


def find_def(node: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """First function/method named *name* anywhere under *node*."""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == name:
            return sub
    return None


def find_class(node: ast.AST, name: str) -> ast.ClassDef | None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.ClassDef) and sub.name == name:
            return sub
    return None


def node_text(path: pathlib.Path, node: ast.AST) -> str:
    """Raw source lines spanning *node* -- comments and docstrings included."""
    lines = read(path).splitlines()
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", len(lines))
    return "\n".join(lines[start:end])


def body_text(node: ast.AST) -> str:
    """``ast.unparse`` of a function with its docstring removed.

    Prose that *describes* a banned pattern (several docstrings in this repo
    do) must not be reported as an instance of it.
    """
    node = _copy_without_docstring(node)
    return ast.unparse(node)


def _copy_without_docstring(node):
    import copy

    clone = copy.deepcopy(node)
    if isinstance(clone, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if ast.get_docstring(clone) is not None:
            clone.body = clone.body[1:]
    return clone


def called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def positional_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = fn.args
    return [a.arg for a in (*args.posonlyargs, *args.args)]


def kwonly_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [a.arg for a in fn.args.kwonlyargs]


def all_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return positional_params(fn) + kwonly_params(fn)


# ---------------------------------------------------------------------------
# Module-binding resolution -- the trickiest helper here
# ---------------------------------------------------------------------------
#
# The facade guards ask "does ``pageindex_mcp.converters.ScriptContext``
# resolve?".  A runtime guard answers with ``hasattr`` after an import; a
# static gate has to answer it from the source text.  ``module_bindings``
# does that: it returns every name a module binds at its top level --
# imports (respecting ``as`` aliases), assignments (including tuple targets,
# annotated assignments and walrus-free augmented ones), ``def``/``class``,
# and, for a package, the names of its own submodules on disk.
#
# It deliberately does NOT execute anything, so a name bound by a dynamic
# ``globals().update(...)`` would be missed.  No package barrel in this repo
# does that, and a guard that under-reports bindings fails *loudly* (it
# claims a live name is missing) rather than silently, which is the right
# direction for the error to point.


def module_path(dotted: str) -> pathlib.Path | None:
    """Filesystem path of the module/package named by a dotted path."""
    parts = dotted.split(".")
    if not parts or parts[0] != "pageindex_mcp":
        return None
    base = ROOT / "src" / pathlib.Path(*parts)
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    mod = base.with_suffix(".py")
    return mod if mod.is_file() else None


@cache
def module_bindings(dotted: str) -> frozenset[str]:
    path = module_path(dotted)
    if path is None:
        return frozenset()
    node = tree_of(path)
    if node is None:  # pragma: no cover - defensive
        return frozenset()

    names: set[str] = set()
    for stmt in node.body:
        _collect_bindings(stmt, names)

    if "__getattr__" in names:
        # PEP 562 module-level ``__getattr__``: the package serves some names
        # dynamically, so they are bound at runtime but appear nowhere as a
        # static assignment target.  ``registry/__init__.py`` does exactly
        # this, proxying ``_pool`` and ``_KNOWN_FACETS`` through to their
        # submodules so that ``registry._pool = x`` writes through.  Treat the
        # string keys of every top-level dict literal as served names -- that
        # is the shape this repo's proxies take, and over-reporting here only
        # makes the facade guard more permissive about names that do resolve.
        for stmt in node.body:
            value = getattr(stmt, "value", None)
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(value, ast.Dict):
                names.update(
                    k.value
                    for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )

    if path.name == "__init__.py":
        # A package also binds each submodule name that any import pulls in.
        for sibling in path.parent.iterdir():
            if sibling.name == "__init__.py":
                continue
            if sibling.suffix == ".py":
                names.add(sibling.stem)
            elif (sibling / "__init__.py").is_file():
                names.add(sibling.name)
    return frozenset(names)


def _collect_bindings(stmt: ast.stmt, names: set[str]) -> None:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(stmt.name)
    elif isinstance(stmt, ast.Import):
        for alias in stmt.names:
            names.add(alias.asname or alias.name.split(".")[0])
    elif isinstance(stmt, ast.ImportFrom):
        for alias in stmt.names:
            if alias.name == "*":
                # A star-import re-binds everything the source module binds.
                if stmt.module:
                    names.update(module_bindings(stmt.module))
                continue
            names.add(alias.asname or alias.name)
    elif isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            _collect_target(target, names)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        _collect_target(stmt.target, names)
    elif isinstance(stmt, (ast.If, ast.Try)):
        # Conditional imports (TYPE_CHECKING blocks, optional deps) still bind.
        _collect_nested_bindings(stmt, names)


def _collect_nested_bindings(stmt: ast.stmt, names: set[str]) -> None:
    """Descend into ``if``/``try`` bodies -- a conditional import still binds."""
    for attr in ("body", "orelse", "finalbody", "handlers"):
        for sub in getattr(stmt, attr, []) or []:
            if isinstance(sub, ast.ExceptHandler):
                for inner in sub.body:
                    _collect_bindings(inner, names)
            elif isinstance(sub, ast.stmt):
                _collect_bindings(sub, names)


def _collect_target(target: ast.expr, names: set[str]) -> None:
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target(elt, names)


@cache
def declared_all(package: str) -> tuple[str, ...] | None:
    """The literal ``__all__`` of ``pageindex_mcp.<package>``, in file order."""
    path = module_path(f"pageindex_mcp.{package}")
    if path is None:
        return None
    node = tree_of(path)
    if node is None:  # pragma: no cover - defensive
        return None
    for stmt in ast.walk(node):
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        return tuple(
            e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        )
    return None


def all_lineno(package: str) -> int:
    path = module_path(f"pageindex_mcp.{package}")
    if path is None:
        return 1
    node = tree_of(path)
    if node is None:  # pragma: no cover - defensive
        return 1
    for stmt in ast.walk(node):
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets
        ):
            return stmt.lineno
    return 1


# ---------------------------------------------------------------------------
# 1. Naive block text  (was test_no_naive_block_text.py / RFC-042 D5 guard)
# ---------------------------------------------------------------------------

_NAIVE_BLOCK_TEXT_RE = re.compile(r"""block\s*\.\s*get\s*\(\s*['"]text['"]\s*[,)]""")

#: Canonical measurement helpers, the only place allowed to read the raw key.
_BLOCK_TEXT_APPROVED: dict[str, set[str]] = {
    "helpers/flat.py": {
        "block_text",
        "doc_text",
        "_flat_block_primary_text",
        "_flat_search_text",
    },
}


def _inside_approved_block_text_func(path: pathlib.Path, lineno: int) -> bool:
    key = path.relative_to(src_root()).as_posix()
    allowed = _BLOCK_TEXT_APPROVED.get(key)
    if not allowed:
        return False
    node = tree_of(path)
    if node is None:  # pragma: no cover - defensive
        return False
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name in allowed:
            if sub.lineno <= lineno <= getattr(sub, "end_lineno", sub.lineno):
                return True
    return False


@check("naive-block-text")
def check_naive_block_text():
    """RFC-041 D2 / RFC-042 D5: flat table blocks carry content in
    ``row_records``/``headers``, not ``text``.  A raw ``block.get('text')``
    outside the canonical helpers silently under-counts table content."""
    for path in src_files():
        for i, line in enumerate(read(path).splitlines(), 1):
            stripped = line.lstrip()
            for match in _NAIVE_BLOCK_TEXT_RE.finditer(line):
                if stripped.startswith("#"):
                    continue
                prefix = line[: match.start()]
                if prefix.count('"""') % 2 == 1 or prefix.count("'''") % 2 == 1:
                    continue
                if _inside_approved_block_text_func(path, i):
                    continue
                yield Violation(
                    rel(path),
                    i,
                    "naive-block-text",
                    "block.get('text') outside the canonical helpers -- use "
                    "block_text(block, purpose) / doc_text(data, purpose)",
                )


# ---------------------------------------------------------------------------
# 2. _persist_flat_result pipeline call ordering
# ---------------------------------------------------------------------------

_INDEXER = "client/indexer.py"


def _indexer_method(name: str):
    path = src_root() / "client" / "indexer.py"
    node = tree_of(path)
    cls = find_class(node, "CustomPageIndexClient") if node else None
    return path, (find_def(cls, name) if cls else None)


@check("persist-flat-call-order")
def check_persist_flat_call_order():
    """Zone-1 wiring: splice_figure_markers -> route_and_extract_flat ->
    _garble_check_flat_blocks -> _apply_picture_enrichment."""
    path, fn = _indexer_method("_persist_flat_result")
    if fn is None:
        yield Violation(rel(path), 1, "persist-flat-call-order", "_persist_flat_result not found")
        return
    targets = {
        "splice_figure_markers",
        "route_and_extract_flat",
        "_garble_check_flat_blocks",
        "_apply_picture_enrichment",
    }
    hits: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = called_name(node)
            if name in targets:
                hits.append((node.lineno, name))
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in targets:
                    hits.append((arg.lineno, arg.id))
    hits.sort(key=lambda t: t[0])
    seen: list[str] = []
    for _, name in hits:
        if name not in seen:
            seen.append(name)
    expected = [
        "splice_figure_markers",
        "route_and_extract_flat",
        "_garble_check_flat_blocks",
        "_apply_picture_enrichment",
    ]
    if seen != expected:
        yield Violation(
            rel(path),
            fn.lineno,
            "persist-flat-call-order",
            f"expected call ordering {expected}, got {seen}",
        )


# ---------------------------------------------------------------------------
# 3. Removed symbols (RFC-037 D4/D5 and defence-in-depth aliases)
# ---------------------------------------------------------------------------


@check("removed-symbol")
def check_removed_symbols():
    """Symbols an RFC deleted must not come back.

    ``apply_verdict_hysteresis`` (RFC-037 D4) is banned repo-wide in ``src/``.
    The sidecar CAS guard symbols (RFC-037 D5) are banned only in
    ``storage/verdict.py`` -- ``worker/registry_mirror.py`` owns its own,
    unrelated ``_VERDICT_CAS_FIELDS`` for the Postgres write-through path.
    """
    for path in src_files():
        for i, line in enumerate(read(path).splitlines(), 1):
            if "apply_verdict_hysteresis" in line:
                yield Violation(
                    rel(path),
                    i,
                    "removed-symbol",
                    "apply_verdict_hysteresis was removed by RFC-037 D4",
                )

    verdict_path = src_root() / "storage" / "verdict.py"
    node = tree_of(verdict_path)
    if node is None:  # pragma: no cover - defensive
        return
    banned = {"_verdict_cas_guard", "_VERDICT_CAS_FIELDS", "_save_doc_meta"}
    bound: set[str] = set()
    for stmt in node.body:
        _collect_bindings(stmt, bound)
    for name in sorted(banned & bound):
        yield Violation(
            rel(verdict_path),
            1,
            "removed-symbol",
            f"{name} must not exist in storage/verdict.py "
            "(D5: the sidecar is a passive archive; save_doc_meta is the sole entry point)",
        )


# ---------------------------------------------------------------------------
# 4. Verdict-function signatures (tree/flat verdict unification)
# ---------------------------------------------------------------------------

_VERDICT_PY = "helpers/verdict.py"


def _verdict_def(name: str):
    path = src_root() / "helpers" / "verdict.py"
    node = tree_of(path)
    return path, (find_def(node, name) if node else None)


@check("verdict-signature")
def check_verdict_signatures():
    """The tree/flat verdict split left three signatures that must stay shut:
    no ``flat=`` kwarg, no ``validate_result`` positional on
    ``apply_promotions``, and ``validate_result`` positional-or-keyword with a
    ``None`` default on ``compute_verdict``."""
    path, evaluate_gates = _verdict_def("evaluate_gates")
    if evaluate_gates is None:
        yield Violation(rel(path), 1, "verdict-signature", "evaluate_gates not found")
    else:
        params = all_params(evaluate_gates)
        if "flat" in params:
            yield Violation(
                rel(path),
                evaluate_gates.lineno,
                "verdict-signature",
                "evaluate_gates must not accept a flat= keyword",
            )
        pos = positional_params(evaluate_gates)
        if len(pos) != 4:
            yield Violation(
                rel(path),
                evaluate_gates.lineno,
                "verdict-signature",
                f"evaluate_gates takes {len(pos)} positional params, expected 4 "
                "(structure, validate_result, expected_script, th)",
            )

    path, apply_promotions = _verdict_def("apply_promotions")
    if apply_promotions is None:
        yield Violation(rel(path), 1, "verdict-signature", "apply_promotions not found")
    else:
        if "validate_result" in all_params(apply_promotions):
            yield Violation(
                rel(path),
                apply_promotions.lineno,
                "verdict-signature",
                "apply_promotions must not accept validate_result",
            )
        pos = positional_params(apply_promotions)
        if len(pos) != 6:
            yield Violation(
                rel(path),
                apply_promotions.lineno,
                "verdict-signature",
                f"apply_promotions takes {len(pos)} positional params, expected 6",
            )
        if "source_selection" not in kwonly_params(apply_promotions):
            yield Violation(
                rel(path),
                apply_promotions.lineno,
                "verdict-signature",
                "apply_promotions must keep source_selection keyword-only",
            )

    path, compute_verdict = _verdict_def("compute_verdict")
    if compute_verdict is None:
        yield Violation(rel(path), 1, "verdict-signature", "compute_verdict not found")
    else:
        if "flat" in all_params(compute_verdict):
            yield Violation(
                rel(path),
                compute_verdict.lineno,
                "verdict-signature",
                "compute_verdict must not accept a flat= keyword",
            )
        pos = positional_params(compute_verdict)
        if "validate_result" not in pos:
            yield Violation(
                rel(path),
                compute_verdict.lineno,
                "verdict-signature",
                "compute_verdict.validate_result must be positional-or-keyword, not keyword-only",
            )
        else:
            idx = pos.index("validate_result")
            defaults = compute_verdict.args.defaults
            offset = len(pos) - len(defaults)
            default = defaults[idx - offset] if idx >= offset else None
            if not (isinstance(default, ast.Constant) and default.value is None):
                yield Violation(
                    rel(path),
                    compute_verdict.lineno,
                    "verdict-signature",
                    "compute_verdict.validate_result must default to None",
                )


@check("gatespec-no-flat-applicable")
def check_gatespec_fields():
    """``GateSpec`` must not carry ``flat_applicable`` after the unification."""
    path = src_root() / "helpers" / "types.py"
    node = tree_of(path)
    cls = find_class(node, "GateSpec") if node else None
    if cls is None:
        yield Violation(rel(path), 1, "gatespec-no-flat-applicable", "GateSpec not found")
        return
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "flat_applicable":
                yield Violation(
                    rel(path),
                    stmt.lineno,
                    "gatespec-no-flat-applicable",
                    "GateSpec.flat_applicable was removed with the tree/flat split",
                )


@check("gate-result-threading")
def check_gate_result_threading():
    """The flat path must pass ``None`` (not ``state.gate_result``) as
    ``validate_result`` so tree defects cannot leak into a flat verdict (D4);
    the tree path must thread ``state.gate_result`` through."""
    path, flat = _indexer_method("_persist_flat_result")
    if flat is not None:
        src = node_text(path, flat)
        match = re.search(r"compute_verdict\(\s*flat_structure,\s*content_class,\s*(\w+)", src)
        if match is None:
            yield Violation(
                rel(path),
                flat.lineno,
                "gate-result-threading",
                "compute_verdict(flat_structure, content_class, ...) call not found",
            )
        elif match.group(1) != "None":
            yield Violation(
                rel(path),
                flat.lineno,
                "gate-result-threading",
                f"flat path must pass None as validate_result, got {match.group(1)!r}",
            )

    path, tree_fn = _indexer_method("_persist_tree_result")
    if tree_fn is not None:
        src = node_text(path, tree_fn)
        if "state.gate_result" not in src or "compute_verdict" not in src:
            yield Violation(
                rel(path),
                tree_fn.lineno,
                "gate-result-threading",
                "_persist_tree_result must pass state.gate_result to compute_verdict",
            )


@check("structural-ok-unified")
def check_structural_ok():
    """``_structural_ok`` is the all_defects ``isdisjoint`` check, not the old
    ``sig.node_count >= 3`` heuristic."""
    path, fn = _verdict_def("_try_structural_pass")
    if fn is None:
        yield Violation(rel(path), 1, "structural-ok-unified", "_try_structural_pass not found")
    else:
        src = node_text(path, fn)
        for token in ("isdisjoint", "NODE_COUNT_LOW", "DEPTH_LOW"):
            if token not in src:
                yield Violation(
                    rel(path),
                    fn.lineno,
                    "structural-ok-unified",
                    f"_try_structural_pass must use the unified expression (missing {token!r})",
                )
    path, promo = _verdict_def("apply_promotions")
    if promo is not None:
        for offset, line in enumerate(node_text(path, promo).splitlines()):
            if "_structural_ok" in line and "sig.node_count" in line:
                yield Violation(
                    rel(path),
                    promo.lineno + offset,
                    "structural-ok-unified",
                    f"_structural_ok still uses the sig-based heuristic: {line.strip()}",
                )


# ---------------------------------------------------------------------------
# 5. Redis singleton / duplicate priority maps / SQL CASE literals
# ---------------------------------------------------------------------------


@check("redis-singleton")
def check_redis_singleton():
    """RFC-012: exactly one ``aioredis.from_url`` in the worker package -- the
    startup site."""
    worker_dir = src_root() / "worker"
    sites = [
        (path, i)
        for path in sorted(worker_dir.glob("*.py"))
        for i, line in enumerate(read(path).splitlines(), 1)
        if re.search(r"aioredis\.from_url\(", line)
    ]
    if len(sites) != 1:
        where = ", ".join(f"{rel(p)}:{i}" for p, i in sites) or "none"
        yield Violation(
            rel(worker_dir),
            1,
            "redis-singleton",
            f"expected exactly 1 aioredis.from_url call (startup), found {len(sites)}: {where}",
        )


@check("no-duplicate-priority-map")
def check_duplicate_priority_maps():
    """Only ``helpers/types.py`` may define a dict mapping all four verdict
    keys to integers -- a second one silently forks the ranking."""
    keys = {"PASS", "MARGINAL", "FAIL", "ERROR"}
    for path in src_files():
        if path.relative_to(src_root()).as_posix() == "helpers/types.py":
            continue
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            found = {
                k.value
                for k in sub.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            all_ints = all(
                isinstance(v, ast.Constant) and isinstance(v.value, int) for v in sub.values
            )
            if keys.issubset(found) and all_ints:
                yield Violation(
                    rel(path),
                    sub.lineno,
                    "no-duplicate-priority-map",
                    "duplicate verdict priority map -- import it from helpers/types.py",
                )


@check("sql-case-literals")
def check_sql_case_literals():
    """``registry/queries.py`` must not grow hand-written verdict CASE
    expressions beside the generated constant."""
    path = src_root() / "registry" / "queries.py"
    hits = [
        i
        for i, line in enumerate(read(path).splitlines(), 1)
        if "CASE" in line and "'PASS'" in line and "THEN" in line
    ]
    if len(hits) > 2:
        yield Violation(
            rel(path),
            hits[0],
            "sql-case-literals",
            f"{len(hits)} hardcoded CASE...'PASS' lines (expected at most 2: the "
            f"generated constant and one f-string) at lines {hits}",
        )


# ---------------------------------------------------------------------------
# 6. Scoring-harness Stage-2 guard
# ---------------------------------------------------------------------------

_HARNESS_JS = ".claude/workflows/corpus-ingest-score.js"
_STAGE2_GUARD_RE = re.compile(r"if \(!ingestResult \|\| ingestResult\.status === 'error'\)")


@check("harness-stage2-guard")
def check_harness_stage2_guard():
    """The scoring harness short-circuits to ERROR iff ``ingestResult`` is
    falsy or its ``status`` is exactly ``'error'`` -- never on a substring
    match against an unrelated string field."""
    path = ROOT / _HARNESS_JS
    if not path.is_file():
        return
    if not _STAGE2_GUARD_RE.search(read(path)):
        yield Violation(
            _HARNESS_JS,
            1,
            "harness-stage2-guard",
            "Stage 2 guard predicate not found -- it must be exactly "
            "`if (!ingestResult || ingestResult.status === 'error')`; a substring "
            "test against an unrelated field would ERROR a successful ingest",
        )


# ---------------------------------------------------------------------------
# 7. Presentation forms / ScriptContext
# ---------------------------------------------------------------------------


@check("pf-hardcoded-false")
def check_pf_hardcoded_false():
    """``had_presentation_forms=False`` hardcoded anywhere in ``src/`` defeats
    the NFKC presentation-forms compensation and makes normalised Arabic look
    clean (RFC-043 D3: no file is exempt any more)."""
    for path in src_files():
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            for kw in sub.keywords:
                if (
                    kw.arg == "had_presentation_forms"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                ):
                    yield Violation(
                        rel(path),
                        kw.value.lineno,
                        "pf-hardcoded-false",
                        "pass _infer_presentation_forms(<the text being checked>) instead",
                    )


@check("pf-indexer-contexts")
def check_pf_indexer_contexts():
    """The two sites commit e02ec93 missed: each fallback ScriptContext must
    infer presentation forms from the same text its garble gate then checks."""
    path = src_root() / "client" / "indexer.py"
    src = read(path)
    for source_tag, text_var in (
        ("flat_garble_gate", "flat_md"),
        ("vlm_fallback_garble", "vlm_md"),
    ):
        if f'source="{source_tag}"' not in src:
            yield Violation(
                rel(path),
                1,
                "pf-indexer-contexts",
                f"no ScriptContext with source={source_tag!r} in indexer.py",
            )
        if f"_infer_presentation_forms({text_var})" not in src:
            yield Violation(
                rel(path),
                1,
                "pf-indexer-contexts",
                f"the {source_tag} ScriptContext must infer presentation forms "
                f"from {text_var}, the text its garble gate checks",
            )


@check("pf-from-script-str")
def check_pf_from_script_str():
    """RFC-043 D3: ``ScriptContext.from_script_str`` takes
    ``had_presentation_forms`` as a required parameter -- it no longer
    hardcodes ``False``."""
    path = ROOT / "src" / "pageindex_mcp" / "script.py"
    node = tree_of(path)
    cls = find_class(node, "ScriptContext") if node else None
    fn = find_def(cls, "from_script_str") if cls else None
    if fn is None:
        yield Violation(rel(path), 1, "pf-from-script-str", "from_script_str not found")
        return
    src = node_text(path, fn)
    if "had_presentation_forms=False" in src:
        yield Violation(
            rel(path),
            fn.lineno,
            "pf-from-script-str",
            "from_script_str must not hardcode had_presentation_forms=False",
        )
    if "had_presentation_forms: bool" not in src:
        yield Violation(
            rel(path),
            fn.lineno,
            "pf-from-script-str",
            "had_presentation_forms must stay a declared parameter of from_script_str",
        )
    if 'source="legacy"' not in src:
        yield Violation(
            rel(path),
            fn.lineno,
            "pf-from-script-str",
            'from_script_str must tag its context source="legacy"',
        )


_PF_CALLEES = {"_infer_presentation_forms", "_infer_pf", "_pf_ratio"}


@check("no-post-nfkc-script-context")
def check_no_post_nfkc_script_context():
    """Property 10 (RFC-046 D10): a ScriptContext built with a PF *inference
    call* may be reading post-NFKC text, where the signal is already
    destroyed.  Genuinely pre-NFKC sites carry a ``# pre-NFKC`` marker."""
    for path in src_files():
        node = tree_of(path)
        if node is None:
            continue
        lines = read(path).splitlines()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            is_sc = (isinstance(func, ast.Name) and func.id == "ScriptContext") or (
                isinstance(func, ast.Attribute) and func.attr == "ScriptContext"
            )
            if not is_sc:
                continue
            for kw in sub.keywords:
                if kw.arg != "had_presentation_forms" or not isinstance(kw.value, ast.Call):
                    continue
                callee = called_name(kw.value)
                if callee not in _PF_CALLEES:
                    continue
                line_text = lines[kw.value.lineno - 1] if kw.value.lineno <= len(lines) else ""
                if "pre-NFKC" in line_text:
                    continue
                yield Violation(
                    rel(path),
                    sub.lineno,
                    "no-post-nfkc-script-context",
                    f"had_presentation_forms={callee}(...) on unmarked text -- add "
                    "'# pre-NFKC' on that line if the text really is pre-normalisation, "
                    "otherwise pass False",
                )


@check("pf-threshold-uniformity")
def check_pf_threshold_uniformity():
    """Property 3 (RFC-046): the PF signal ratio lives in
    ``garble.PF_SIGNAL_RATIO``; a second 0.5 literal compared against PF
    counts would fork the threshold."""
    for path in src_files():
        key = path.relative_to(src_root()).as_posix()
        if key == "helpers/garble.py":
            continue
        node = tree_of(path)
        if node is None:
            continue
        lines = read(path).splitlines()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare):
                continue
            for comparator in [sub.left, *sub.comparators]:
                if isinstance(comparator, ast.Constant) and comparator.value in (0.50, 0.5):
                    ctx = lines[comparator.lineno - 1].lower()
                    if "pf" in ctx or "presentation" in ctx:
                        yield Violation(
                            rel(path),
                            comparator.lineno,
                            "pf-threshold-uniformity",
                            "independent PF threshold literal -- import PF_SIGNAL_RATIO "
                            "from helpers/garble.py instead",
                        )


# ---------------------------------------------------------------------------
# 8. Garble / recovery / gates encapsulation
# ---------------------------------------------------------------------------


@check("no-direct-garble-prongs")
def check_no_direct_garble_prongs():
    """D1 (RFC-041): ``_garble_prongs`` is private to ``helpers/garble.py``;
    every other caller goes through ``detect_garble``.  It must also stay out
    of the helpers barrel."""
    for path in src_files():
        key = path.relative_to(src_root()).as_posix()
        if key == "helpers/garble.py":
            continue
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and called_name(sub) in (
                "_garble_prongs",
                "garble_prongs",
            ):
                yield Violation(
                    rel(path),
                    sub.lineno,
                    "no-direct-garble-prongs",
                    f"direct {called_name(sub)}() call -- use detect_garble()",
                )
    exported = declared_all("helpers") or ()
    if "garble_prongs" in exported:
        yield Violation(
            "src/pageindex_mcp/helpers/__init__.py",
            all_lineno("helpers"),
            "no-direct-garble-prongs",
            "garble_prongs must not be exported from the helpers barrel",
        )


@check("no-direct-state-mutation-in-recovery")
def check_no_direct_state_mutation_in_recovery():
    """D3 (RFC-041): ``recovery.py`` routes route/ok changes through
    ``finalize_gate_and_route``."""
    path = src_root() / "client" / "recovery.py"
    source = read(path)
    for match in re.finditer(r"^\s+state\.(route|ok)\s*=\s*", source, re.MULTILINE):
        lineno = source[: match.start()].count("\n") + 1
        yield Violation(
            rel(path),
            lineno,
            "no-direct-state-mutation-in-recovery",
            f"direct state.{match.group(1)} assignment -- use finalize_gate_and_route()",
        )


@check("eligible-low-content-no-char-floor")
def check_eligible_low_content_no_char_floor():
    """RFC-043 D1/D2: ``_eligible_low_content`` gates on flags and defect
    membership only.  A ``total_chars`` floor re-opens the zero-content bug
    (0 >= 300 is False); an OR on ``image_dominant_ocr_escalation_enabled``
    turns that flag into a kill-switch for low-content recovery."""
    path = src_root() / "helpers" / "gates.py"
    node = tree_of(path)
    fn = find_def(node, "_eligible_low_content") if node else None
    if fn is None:
        yield Violation(
            rel(path),
            1,
            "eligible-low-content-no-char-floor",
            "_eligible_low_content not found",
        )
        return
    src = body_text(fn)
    if "total_chars" in src:
        yield Violation(
            rel(path),
            fn.lineno,
            "eligible-low-content-no-char-floor",
            "the char floor belongs in _recover_low_content_ocr, not the eligibility predicate",
        )
    if "image_dominant_ocr_escalation_enabled" in src:
        yield Violation(
            rel(path),
            fn.lineno,
            "eligible-low-content-no-char-floor",
            "the two eligibility predicates must not share a config flag (RFC-043 D2)",
        )


_ELIGIBLE_PREDICATES = (
    "_eligible_garble",
    "_eligible_low_content",
    "_eligible_image_dominant",
    "_eligible_rtl",
)


@check("eligible-predicate-symmetry")
def check_eligible_predicate_symmetry():
    """R2.4/Property 2 (RFC-044): every ``_eligible_*`` predicate uses
    ``_all_defects(state)`` and never reads ``state.first_defect``."""
    path = src_root() / "helpers" / "gates.py"
    node = tree_of(path)
    for name in _ELIGIBLE_PREDICATES:
        fn = find_def(node, name) if node else None
        if fn is None:
            yield Violation(rel(path), 1, "eligible-predicate-symmetry", f"{name} not found")
            continue
        src = body_text(fn)
        if "first_defect" in src:
            yield Violation(
                rel(path),
                fn.lineno,
                "eligible-predicate-symmetry",
                f"{name} reads state.first_defect -- use _all_defects(state)",
            )
        if "_all_defects(state)" not in src:
            yield Violation(
                rel(path),
                fn.lineno,
                "eligible-predicate-symmetry",
                f"{name} must use _all_defects(state) for defect membership",
            )


_RECOVER_RTL_METHODS = ("_recover_rtl_repair", "_recover_rtl_flat_compare")


@check("recover-rtl-uses-all-defects")
def check_recover_rtl_uses_all_defects():
    """R2.6 / Property 2 extension (RFC-044 Amendment 3): the RTL recovery
    methods test RTL_REVERSAL membership via ``_all_defects(state)``, not
    ``state.first_defect == TreeDefect.RTL_REVERSAL``.  ``_recover_vlm_fallback``
    is deliberately excluded -- its ``first_defect`` use gates Tesseract raster
    fallback on GARBLING/NODE_GARBLING, not RTL_REVERSAL."""
    path = src_root() / "client" / "recovery.py"
    node = tree_of(path)
    for name in _RECOVER_RTL_METHODS:
        fn = find_def(node, name) if node else None
        if fn is None:
            yield Violation(rel(path), 1, "recover-rtl-uses-all-defects", f"{name} not found")
            continue
        stripped = _copy_without_docstring(fn)
        for sub in ast.walk(stripped):
            if not isinstance(sub, ast.Compare):
                continue
            left_is_first_defect = (
                isinstance(sub.left, ast.Attribute) and sub.left.attr == "first_defect"
            )
            rtl = any(
                isinstance(c, ast.Attribute) and c.attr == "RTL_REVERSAL" for c in sub.comparators
            )
            if left_is_first_defect and rtl:
                yield Violation(
                    rel(path),
                    fn.lineno,
                    "recover-rtl-uses-all-defects",
                    f"{name} gates on state.first_defect == TreeDefect.RTL_REVERSAL",
                )
        if "TreeDefect.RTL_REVERSAL in _all_defects(state)" not in ast.unparse(stripped):
            yield Violation(
                rel(path),
                fn.lineno,
                "recover-rtl-uses-all-defects",
                f"{name} must use `TreeDefect.RTL_REVERSAL in _all_defects(state)`",
            )


@check("ocr-reentry-guard")
def check_ocr_reentry_guard():
    """Property 1 (RFC-044): every ``RecoveryMixin`` method that calls
    ``_execute_ocr_retry`` early-returns on ``state.full_page_already_applied``
    *before* that call, so a document that already received full-page OCR
    never triggers a redundant second pass.  The caller set is discovered, not
    hardcoded, so a new recovery method is covered automatically."""
    path = src_root() / "client" / "recovery.py"
    node = tree_of(path)
    cls = find_class(node, "RecoveryMixin") if node else None
    if cls is None:
        yield Violation(rel(path), 1, "ocr-reentry-guard", "RecoveryMixin not found")
        return

    callers: dict[str, ast.AST] = {}
    for stmt in cls.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(sub, ast.Attribute) and sub.attr == "_execute_ocr_retry"
            for sub in ast.walk(stmt)
        ):
            callers[stmt.name] = stmt

    expected = {"_recover_garble_ocr", "_recover_low_content_ocr", "_recover_image_dominant_ocr"}
    missing = expected - set(callers)
    if missing:
        yield Violation(
            rel(path),
            cls.lineno,
            "ocr-reentry-guard",
            f"discovery found {sorted(callers)} -- expected to include {sorted(missing)}; "
            "a discovery bug makes this guard vacuous",
        )

    for name, fn in sorted(callers.items()):
        guard_line = None
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.If):
                continue
            if not any(
                isinstance(t, ast.Attribute) and t.attr == "full_page_already_applied"
                for t in ast.walk(sub.test)
            ):
                continue
            if any(isinstance(s, ast.Return) for s in sub.body):
                guard_line = sub.lineno
                break
        retry_lines = [
            sub.lineno
            for sub in ast.walk(fn)
            if isinstance(sub, ast.Attribute) and sub.attr == "_execute_ocr_retry"
        ]
        retry_line = min(retry_lines) if retry_lines else None
        if guard_line is None:
            yield Violation(
                rel(path),
                fn.lineno,
                "ocr-reentry-guard",
                f"{name}: no early-return guard on state.full_page_already_applied",
            )
        elif retry_line is not None and guard_line > retry_line:
            yield Violation(
                rel(path),
                guard_line,
                "ocr-reentry-guard",
                f"{name}: re-entry guard sits after the _execute_ocr_retry call "
                f"(line {retry_line})",
            )


@check("decide-ocr-strategy-single-call-site")
def check_decide_ocr_strategy_single_call_site():
    """R3.3/Property 3 (RFC-044): exactly one live ``decide_ocr_strategy``
    call site in ``src/`` -- the one in ``converters/pictures.py``."""
    sites: list[tuple[str, int]] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if "test" in path.name:
            continue
        for i, line in enumerate(read(path).splitlines(), 1):
            if "decide_ocr_strategy(" in line and "def decide_ocr_strategy" not in line:
                sites.append((rel(path), i))
    expected_file = "src/pageindex_mcp/converters/pictures.py"
    if len(sites) != 1 or sites[0][0] != expected_file:
        yield Violation(
            sites[0][0] if sites else expected_file,
            sites[0][1] if sites else 1,
            "decide-ocr-strategy-single-call-site",
            f"expected exactly one call site in {expected_file}, found {sites}",
        )


@check("no-unified-ocr-plan-flag")
def check_no_unified_ocr_plan_flag():
    """R4/Property 4 (RFC-044): the unreachable ``UNIFIED_OCR_PLAN_ENABLED``
    flag and its dead ``document_type='image'`` branch stay removed."""
    for path in sorted((ROOT / "src").rglob("*.py")):
        for i, line in enumerate(read(path).splitlines(), 1):
            if "UNIFIED_OCR_PLAN_ENABLED" in line:
                yield Violation(
                    rel(path),
                    i,
                    "no-unified-ocr-plan-flag",
                    "UNIFIED_OCR_PLAN_ENABLED must not appear in src/",
                )


# ---------------------------------------------------------------------------
# 9. save_doc_meta single writer
# ---------------------------------------------------------------------------

_SAVE_DOC_META_ALLOWED_FILES = {"worker/registry_mirror.py", "client/indexer.py"}
_SAVE_DOC_META_ALLOWED_INDEXER_FUNCS = {"_persist_flat_result", "_persist_tree_result"}
_SAVE_DOC_META_DEFINING_FILE = "storage/verdict.py"


class _SaveDocMetaVisitor(ast.NodeVisitor):
    """Innermost enclosing function for every ``save_doc_meta`` reference.

    References, not just calls: ``asyncio.to_thread(save_doc_meta, ...)`` is
    the dominant pattern here because ``save_doc_meta`` is synchronous MinIO
    I/O, and a call-only visitor would see none of them.
    """

    def __init__(self) -> None:
        self._stack: list[str] = []
        self.refs: list[tuple[str | None, int]] = []

    def visit_FunctionDef(self, node):
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if node.id == "save_doc_meta":
            self.refs.append((self._stack[-1] if self._stack else None, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr == "save_doc_meta":
            self.refs.append((self._stack[-1] if self._stack else None, node.lineno))
        self.generic_visit(node)


@check("save-doc-meta-single-writer")
def check_save_doc_meta_single_writer():
    """D3 (RFC-042, Amendment 2026-09-01 v2): ``save_doc_meta`` is the sole
    authoritative verdict-sidecar writer.  Only ``registry_mirror.py``'s
    write-through path and the two child-subprocess persist paths in
    ``indexer.py`` -- which run without Postgres access -- may reach it.
    Everything else routes through ``_upsert_registry_row``."""
    for path in src_files():
        key = path.relative_to(src_root()).as_posix()
        if key == _SAVE_DOC_META_DEFINING_FILE:
            continue
        node = tree_of(path)
        if node is None:
            continue
        visitor = _SaveDocMetaVisitor()
        visitor.visit(node)
        if not visitor.refs:
            continue
        if key not in _SAVE_DOC_META_ALLOWED_FILES:
            for _enclosing, lineno in visitor.refs:
                yield Violation(
                    rel(path),
                    lineno,
                    "save-doc-meta-single-writer",
                    "save_doc_meta referenced outside the single-writer path -- "
                    "route through registry_mirror.py's _upsert_registry_row",
                )
        elif key == "client/indexer.py":
            for enclosing, lineno in visitor.refs:
                if enclosing not in _SAVE_DOC_META_ALLOWED_INDEXER_FUNCS:
                    yield Violation(
                        rel(path),
                        lineno,
                        "save-doc-meta-single-writer",
                        f"save_doc_meta referenced from {enclosing!r}; expected one of "
                        f"{sorted(_SAVE_DOC_META_ALLOWED_INDEXER_FUNCS)}",
                    )


# ---------------------------------------------------------------------------
# 10. Config access
# ---------------------------------------------------------------------------

_HOT_PATH_FILES = (
    "helpers/gates.py",
    "converters/pictures.py",
    "client/indexer.py",
    "helpers/tree_split.py",
    "helpers/garble.py",
    "helpers/verdict.py",
)

#: Startup-only files, exempt by design: they run once, before PipelineConfig
#: exists.  The list is guarded below so it cannot silently drift.
_STARTUP_ONLY_ALLOWLIST = (
    "tracing.py",
    "subprocess_mgr.py",
    "minio_client.py",
    "constants.py",
    "definitions.py",
)


@check("hot-path-config-access")
def check_hot_path_config_access():
    """D4 (RFC-042): hot-path files read configuration from the frozen
    ``PipelineConfig`` snapshot, never ``os.environ``/``os.getenv``.  AST, not
    grep, so a docstring that *mentions* ``os.environ`` (garble.py's does) is
    not reported."""
    for key in _HOT_PATH_FILES:
        path = src_root() / key
        node = tree_of(path)
        if node is None:
            yield Violation(key, 1, "hot-path-config-access", "hot-path file missing or unparsable")
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                if (
                    isinstance(sub.value, ast.Name)
                    and sub.value.id == "os"
                    and sub.attr in ("environ", "getenv")
                ):
                    yield Violation(
                        rel(path),
                        sub.lineno,
                        "hot-path-config-access",
                        f"direct os.{sub.attr} read -- use a PipelineConfig field",
                    )
            elif isinstance(sub, ast.ImportFrom) and sub.module == "os":
                for alias in sub.names:
                    if alias.name in ("environ", "getenv"):
                        yield Violation(
                            rel(path),
                            sub.lineno,
                            "hot-path-config-access",
                            f"from os import {alias.name} -- use a PipelineConfig field",
                        )

    for name in _STARTUP_ONLY_ALLOWLIST:
        if not any(p.name == name for p in src_files()):
            yield Violation(
                "src/pageindex_mcp",
                1,
                "hot-path-config-access",
                f"allowlisted startup-only file {name!r} no longer exists -- the "
                "exemption list has drifted from the tree",
            )


#: Pre-existing double-sourced reads, pinned rather than silently tolerated.
#: Both are module-level constants re-exported into ``indexer.py`` and
#: ``recovery.py`` and monkeypatched per-module by roughly eight tests.
#: Routing them through PipelineConfig is a real refactor that no RFC-046
#: task owns, so they are recorded here where they stay visible.
KNOWN_DOUBLE_SOURCED: frozenset[tuple[str, str]] = frozenset(
    {
        ("client/images.py", "TREE_PATH_PICTURE_SPLICE_ENABLED"),
        ("client/images.py", "IMAGE_STANDALONE_PIPELINE_ENABLED"),
    }
)


def _pipeline_config_owned_env_vars() -> set[str]:
    path = src_root() / "config.py"
    node = tree_of(path)
    if node is None:  # pragma: no cover - defensive
        return set()
    from_env = find_def(node, "from_env")
    if from_env is None:  # pragma: no cover - defensive
        return set()
    ctor = next(
        (
            n
            for n in ast.walk(from_env)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "cls"
        ),
        None,
    )
    if ctor is None:  # pragma: no cover - defensive
        return set()
    owned: set[str] = set()
    for kw in ctor.keywords:
        for sub in ast.walk(kw.value):
            if (
                isinstance(sub, ast.Constant)
                and isinstance(sub.value, str)
                and sub.value.isupper()
                and "_" in sub.value
            ):
                owned.add(sub.value)
    return owned


def _external_env_reads() -> dict[tuple[str, str], int]:
    owned = _pipeline_config_owned_env_vars()
    found: dict[tuple[str, str], int] = {}
    for path in src_files():
        key = path.relative_to(src_root()).as_posix()
        if key == "config.py":
            continue
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            if sub.func.attr not in ("getenv", "get"):
                continue
            target = sub.func.value
            reads_env = (isinstance(target, ast.Attribute) and target.attr == "environ") or (
                isinstance(target, ast.Name) and target.id in ("os", "environ")
            )
            if not reads_env or not sub.args:
                continue
            arg = sub.args[0]
            if isinstance(arg, ast.Constant) and arg.value in owned:
                found[(key, arg.value)] = sub.lineno
    return found


@check("no-config-double-sourcing")
def check_no_config_double_sourcing():
    """R9/D9 (RFC-046): closed-world -- the owned set is derived from
    ``from_env`` itself and all of ``src/`` is scanned, so a second read of a
    snapshotted variable fails wherever it lands.  A module-level read happens
    once at import, before ``reset_pipeline_config()`` can refresh anything;
    the snapshot and the constant then disagree for the life of the process."""
    reads = _external_env_reads()
    for (key, var), lineno in sorted(reads.items()):
        if (key, var) in KNOWN_DOUBLE_SOURCED:
            continue
        yield Violation(
            f"src/pageindex_mcp/{key}",
            lineno,
            "no-config-double-sourcing",
            f"{var} is already snapshotted by PipelineConfig -- read it from the config object",
        )
    stale = KNOWN_DOUBLE_SOURCED - set(reads)
    for key, var in sorted(stale):
        yield Violation(
            "scripts/gates/source_invariants.py",
            1,
            "no-config-double-sourcing",
            f"({key}, {var}) is no longer double-sourced -- delete it from "
            "KNOWN_DOUBLE_SOURCED rather than implying a defect that is fixed",
        )


# ---------------------------------------------------------------------------
# 11. OCR attribution closed world (RFC-046 R2.8)
# ---------------------------------------------------------------------------

#: Entry-point symbol -> module (relative to ``src/``) that defines it.
OCR_ENTRY_POINTS: dict[str, str] = {
    "_tesseract_ocr_image": "pageindex_mcp/converters/pictures.py",
    "tesseract_ocr_pdf_pages": "pageindex_mcp/converters/formats.py",
    "_attempt_tesseract_raster_recovery": "pageindex_mcp/client/images.py",
    "_landscape_rasterize_rotate_reextract": "pageindex_mcp/converters/pictures.py",
    # Docling-mediated: constructed, not defined by us.
    "TesseractCliOcrOptions": "pageindex_mcp/converters/docling_conv.py",
}

#: Modules permitted to call an OCR entry point.  Adding one here is the
#: deliberate act that must be paired with attributing its engine.
OCR_REACHING_MODULES: frozenset[str] = frozenset(
    {
        "pageindex_mcp/converters/pictures.py",
        "pageindex_mcp/converters/formats.py",
        "pageindex_mcp/converters/docling_conv.py",
        "pageindex_mcp/converters/pipeline.py",
        "pageindex_mcp/client/images.py",
        "pageindex_mcp/client/recovery.py",
    }
)


@check("ocr-attribution-closed-world")
def check_ocr_attribution_closed_world():
    """R2.8/Property 2 (RFC-046): OCR reachability is a closed world.

    Every prior enumeration of these sites found four and missed
    ``_landscape_rasterize_rotate_reextract``, which consults no decision
    function at all.  A new module reaching Tesseract -- through an entry
    point or by resolving the binary itself -- fails here until it is
    attributed, because an unattributed verdict cannot be compared across
    engines (HR5)."""
    for symbol, module in sorted(OCR_ENTRY_POINTS.items()):
        path = ROOT / "src" / module
        if not path.is_file() or symbol not in read(path):
            yield Violation(
                f"src/{module}",
                1,
                "ocr-attribution-closed-world",
                f"OCR entry point {symbol} moved or was renamed -- update "
                "OCR_ENTRY_POINTS and the attribution site table together",
            )

    src_dir = ROOT / "src"
    for path in sorted(src_dir.rglob("*.py")):
        key = path.relative_to(src_dir).as_posix()
        node = tree_of(path)
        if node is None:
            continue
        if key not in OCR_REACHING_MODULES:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and called_name(sub) in OCR_ENTRY_POINTS:
                    yield Violation(
                        rel(path),
                        sub.lineno,
                        "ocr-attribution-closed-world",
                        f"{called_name(sub)}() reached from a module that is not on the "
                        "attribution allowlist",
                    )
        if key in OCR_REACHING_MODULES or key == "pageindex_mcp/converters/ocr_langs.py":
            continue
        for i, line in enumerate(read(path).splitlines(), 1):
            if 'which("tesseract")' in line or "which('tesseract')" in line:
                yield Violation(
                    rel(path),
                    i,
                    "ocr-attribution-closed-world",
                    "resolves the tesseract binary without being on the attribution allowlist",
                )


@check("persist-records-attribution")
def check_persist_records_attribution():
    """R2.6/R2.8 (RFC-046): both persistence paths attribute.

    The tree and flat paths have repeatedly disagreed about what they record
    -- ``fired_prongs`` reached neither before RFC-046.  A stored 'garbling'
    verdict that cannot say which engine produced the text, or which prongs
    fired, cannot be explained without re-running the pipeline."""
    for method in ("_persist_tree_result", "_persist_flat_result"):
        path, fn = _indexer_method(method)
        if fn is None:
            yield Violation(rel(path), 1, "persist-records-attribution", f"{method} not found")
            continue
        src = node_text(path, fn)
        if "state.ocr_engine" not in src:
            yield Violation(
                rel(path),
                fn.lineno,
                "persist-records-attribution",
                f"{method} persists a verdict without recording state.ocr_engine",
            )
        if "garble_prongs" not in src:
            yield Violation(
                rel(path),
                fn.lineno,
                "persist-records-attribution",
                f"{method} must persist the fired prong set",
            )


# ---------------------------------------------------------------------------
# 12. Decision layer + logging
# ---------------------------------------------------------------------------


def decision_call_sites() -> list[dict]:
    """Every ``decision(...)`` call site in ``src/pageindex_mcp``."""
    sites: list[dict] = []
    for path in src_files():
        if path.name in ("decisions.py", "decision_points.py"):
            continue
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "decision"):
                continue
            kw = {k.arg: k.value for k in sub.keywords}
            event = kw.get("event")
            attrs = kw.get("attrs")
            keys = (
                [k.value for k in attrs.keys if isinstance(k, ast.Constant)]
                if isinstance(attrs, ast.Dict)
                else []
            )
            sites.append(
                {
                    "path": rel(path),
                    "line": sub.lineno,
                    "event": event.value
                    if isinstance(event, ast.Constant) and isinstance(event.value, str)
                    else None,
                    "attr_keys": keys,
                }
            )
    return sites


@check("decision-call-sites")
def check_decision_call_sites():
    """R12.6 (RFC-046 D12): a guard that silently scans nothing passes
    forever, and a computed event name defeats every static check here --
    including the registry cross-check that runs in pytest."""
    sites = decision_call_sites()
    if len(sites) < 100:
        yield Violation(
            "src/pageindex_mcp",
            1,
            "decision-call-sites",
            f"only {len(sites)} decision() call sites found -- the sweep has probably "
            "stopped seeing them, which makes every decision guard vacuous",
        )
    for site in sites:
        if site["event"] is None:
            yield Violation(
                site["path"],
                site["line"],
                "decision-call-sites",
                "decision(event=...) must be a literal string",
            )


@check("central-logging-configuration")
def check_central_logging_configuration():
    """R12.1 / task 12.9: ``obs.configure()`` is the only logging setup.

    AST, not a substring scan: ``log_config.py``'s docstring names
    ``logging.basicConfig`` while explaining why it replaced it (a text guard
    would false-positive), and ``from logging import basicConfig`` would slip
    past a text match entirely (a text guard would false-negative)."""
    files = list(src_files()) + sorted(ROOT.glob("*.py"))
    for path in files:
        node = tree_of(path)
        if node is None:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and called_name(sub) == "basicConfig":
                yield Violation(
                    rel(path),
                    sub.lineno,
                    "central-logging-configuration",
                    "logging.basicConfig must not be called -- obs.configure() owns logging setup",
                )


# ---------------------------------------------------------------------------
# 13. Facade surface  (RFC-045 Requirement 4)
# ---------------------------------------------------------------------------


@check("facade-frozen")
def check_facade_frozen():
    """Guard 1 + 3: the barrel surface may not drift, and may not lie.

    ``FROZEN_SURFACE`` is a change detector.  It has no idea what the names
    mean; it only refuses to let the surface drift silently, which forces each
    export change to be argued for in review instead of accumulating.  The
    resolve half keeps the frozen literal honest: an ``__all__`` entry naming
    a symbol the package never binds would otherwise go unnoticed until
    someone ran ``from pkg import *``."""
    for package in sorted(FROZEN_SURFACE):
        init = f"src/pageindex_mcp/{package}/__init__.py"
        lineno = all_lineno(package)
        actual = declared_all(package)
        if actual is None:
            yield Violation(
                init,
                1,
                "facade-frozen",
                f"pageindex_mcp.{package}.__all__ is missing or not a literal list",
            )
            continue
        expected = set(FROZEN_SURFACE[package])
        added = sorted(set(actual) - expected)
        removed = sorted(expected - set(actual))
        if added or removed:
            yield Violation(
                init,
                lineno,
                "facade-frozen",
                f"pageindex_mcp.{package}.__all__ drifted from the RFC-045 freeze: "
                f"added={added or '-'} removed={removed or '-'}. If the change is "
                "deliberate, update FROZEN_SURFACE in this file in the SAME commit; "
                "if you are removing a name, check UNEXERCISED_CONSUMERS first.",
            )
        dupes = sorted({n for n in actual if actual.count(n) > 1})
        if dupes:
            yield Violation(
                init,
                lineno,
                "facade-frozen",
                f"pageindex_mcp.{package}.__all__ lists {dupes} more than once",
            )
        bound = module_bindings(f"pageindex_mcp.{package}")
        missing = [n for n in FROZEN_SURFACE[package] if n not in bound]
        if missing:
            yield Violation(
                init,
                lineno,
                "facade-frozen",
                f"pageindex_mcp.{package}.__all__ names {missing}, which the package never "
                "binds -- `from pageindex_mcp." + package + " import *` would raise",
            )


@check("facade-removed-bindings-gone")
def check_facade_removed_bindings_gone():
    """RFC-045 Property 2: a removal deletes the ``__all__`` entry *and* the
    binding.  Stripping the name while leaving ``from .mod import name`` in
    place looks done and is not -- ``pkg.name`` still resolves, so the barrel
    has not shrunk and the name can drift back in unnoticed."""
    for package, names in sorted(REMOVED_SURFACE.items()):
        init = f"src/pageindex_mcp/{package}/__init__.py"
        exported = declared_all(package) or ()
        bound = module_bindings(f"pageindex_mcp.{package}")
        for name in names:
            if name in exported:
                yield Violation(
                    init,
                    all_lineno(package),
                    "facade-removed-bindings-gone",
                    f"{name} was removed by RFC-045 but is still in __all__",
                )
            if name in bound:
                yield Violation(
                    init,
                    1,
                    "facade-removed-bindings-gone",
                    f"{name} left __all__ but its binding survives, so the facade did not shrink",
                )
        overlap = sorted(set(names) & set(FROZEN_SURFACE.get(package, ())))
        if overlap:
            yield Violation(
                "scripts/gates/source_invariants.py",
                1,
                "facade-removed-bindings-gone",
                f"{package}: {overlap} appear in both FROZEN_SURFACE and REMOVED_SURFACE -- "
                "a name cannot be both retained and removed",
            )


@check("unexercised-consumer-contract")
def check_unexercised_consumers():
    """Guard 2: names whose only consumers the test suite cannot exercise.

    A reviewer who deletes an export and updates ``FROZEN_SURFACE`` to match
    gets a green suite, and ``services/docling-service`` then dies at runtime
    inside its own image.  Each pinned entry cites its consumer."""
    for dotted, name, consumer in UNEXERCISED_CONSUMERS:
        if name not in module_bindings(dotted):
            yield Violation(
                consumer.split(":")[0],
                1,
                "unexercised-consumer-contract",
                f"{dotted}.{name} no longer resolves, but {consumer} imports it through the "
                "facade. Nothing in the suite covers that consumer, so removing this name "
                "ships a runtime break.",
            )


_CONSUMER_DIRS = ("issue", "services", "scripts")
_CONSUMER_FILES = (
    "mcp_server.py",
    "preprocess_client.py",
    "promotion_sweep.py",
    "ingest_via_server.py",
    "stress_test.py",
)


def _is_submodule(dotted: str) -> bool:
    rel_path = dotted.replace(".", "/")
    src = ROOT / "src"
    return (src / f"{rel_path}.py").is_file() or (src / rel_path / "__init__.py").is_file()


def consumer_refs() -> list[tuple[str, str, str]]:
    """``(module, name, location)`` for every ``pageindex_mcp`` symbol reached
    by non-test code outside ``src/``."""
    paths: list[pathlib.Path] = []
    for d in _CONSUMER_DIRS:
        if (ROOT / d).is_dir():
            paths += sorted((ROOT / d).rglob("*.py"))
    paths += [ROOT / f for f in _CONSUMER_FILES if (ROOT / f).is_file()]

    refs: list[tuple[str, str, str]] = []
    for path in paths:
        node = tree_of(path)
        if node is None:
            continue
        loc = rel(path)
        aliases: dict[str, str] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and (sub.module or "").startswith("pageindex_mcp"):
                for a in sub.names:
                    dotted = f"{sub.module}.{a.name}"
                    if _is_submodule(dotted):
                        aliases[a.asname or a.name] = dotted
                    else:
                        refs.append((sub.module, a.name, f"{loc}:{sub.lineno}"))
            elif isinstance(sub, ast.Import):
                for a in sub.names:
                    if a.name.startswith("pageindex_mcp"):
                        aliases[a.asname or a.name.split(".")[0]] = a.name
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                module = aliases.get(sub.value.id)
                if module:
                    refs.append((module, sub.attr, f"{loc}:{sub.lineno}"))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for module, name, where in refs:
        if (module, name) not in seen:
            seen.add((module, name))
            unique.append((module, name, where))
    return unique


@check("consumer-references-resolve")
def check_consumer_references_resolve():
    """Guard 3: no consumer outside ``src/`` may name something that does not
    exist.  This is the check that would have caught the
    ``C._relevel_by_numbering`` break on the commit that introduced it (two
    years of latency).  It is NOT the rejected consumer counter: it asks only
    "does this reference resolve", never "is this export used", so it has no
    way to be quietly wrong about liveness."""
    refs = consumer_refs()
    if len(refs) < 40:
        yield Violation(
            "issue",
            1,
            "consumer-references-resolve",
            f"only {len(refs)} consumer references found -- the AST sweep has probably "
            "stopped seeing issue/ or services/, which would make this guard vacuous",
        )
    for module, name, where in refs:
        path = module_path(module)
        if path is None:
            continue
        if name not in module_bindings(module):
            loc_path, _, loc_line = where.partition(":")
            yield Violation(
                loc_path,
                int(loc_line or 1),
                "consumer-references-resolve",
                f"references {module}.{name}, which does not exist -- either the symbol "
                "moved (import it from its submodule) or it was removed and this consumer "
                "was never updated",
            )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checks() -> list[Violation]:
    violations: list[Violation] = []
    for _invariant_id, fn in CHECKS:
        violations.extend(fn() or [])
    return violations


def main(argv: list[str] | None = None) -> int:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=None,
        help="repository root to scan (defaults to the repo this script lives in)",
    )
    parser.add_argument("--list", action="store_true", help="list the invariant ids and exit")
    args = parser.parse_args(argv)

    if args.list:
        for invariant_id, _ in CHECKS:
            print(invariant_id)
        return 0

    if args.root:
        ROOT = pathlib.Path(args.root).resolve()
        for cached in (read, tree_of, src_files, module_bindings, declared_all):
            cached.cache_clear()

    violations = run_checks()
    for violation in violations:
        print(violation.render())

    if violations:
        by_id: dict[str, int] = {}
        for violation in violations:
            by_id[violation.invariant] = by_id.get(violation.invariant, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(by_id.items()))
        print(
            f"\nFAIL gate=source-invariants: {len(violations)} violation(s) "
            f"across {len(by_id)} invariant(s) [{summary}]",
            file=sys.stderr,
        )
        return 1

    print(f"PASS gate=source-invariants: {len(CHECKS)} invariants clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
