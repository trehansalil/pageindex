# ALLOW-NEW-TEST-FILE: RFC-045 D5 facade surface freeze + unexercised-consumer pin
"""Facade surface guard -- RFC-045 Requirement 4.

Two independent guards over the ``src/pageindex_mcp/*/__init__.py`` barrels.
They are not redundant; each covers the other's blind spot.

1. ``TestFacadeSurfaceIsFrozen`` -- every package's ``__all__`` must equal the
   frozen literal below. This is a *change detector*. It has no idea what the
   names mean; it only refuses to let the surface drift silently. Any
   deliberate export change updates ``FROZEN_SURFACE`` in the same commit,
   which is the point: it forces each surface change to be argued for in
   review instead of accumulating.

2. ``TestUnexercisedConsumerContract`` -- names that code *outside the test
   suite's import graph* reaches through a facade must stay resolvable. This
   is a *meaning check*, and it is the half a frozen list structurally cannot
   do: a reviewer who deletes an export and updates ``FROZEN_SURFACE`` to
   match gets a green suite, and ``services/docling-service`` then dies at
   runtime inside its own image. Each pinned entry cites its consumer.

``TestFacadeSurfaceIsFrozen.test_every_frozen_name_resolves`` keeps the frozen
literal honest -- an ``__all__`` entry naming a symbol the package never binds
would otherwise go unnoticed until someone ran ``from pkg import *``.

Deliberately NOT implemented: an "every export has >= 1 consumer" guard. That
needs a repo-wide consumer counter, and the RFC-045 measurement is the record
of how wrong such a counter gets -- two defects (relative-import off-by-one;
``issue/`` never globbed) inflated the candidate set 162 -> 110 before they
were caught. A guard that can be quietly wrong about liveness is worse than no
guard, because it is trusted. See ``agents/rfcs/045-package-facade-surface.md``
D5 and ``audit/FACADE_SURFACE_MANIFEST_2026-09-07.md``.

To regenerate after a deliberate change::

    python - <<'EOF'
    import ast, pathlib
    for init in sorted(pathlib.Path("src/pageindex_mcp").glob("*/__init__.py")):
        tree = ast.parse(init.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(
                getattr(t, "id", None) == "__all__" for t in n.targets
            ):
                print(init.parent.name, sorted(e.value for e in n.value.elts))
    EOF
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

# Frozen facade surface as of RFC-045 (2026-09-07), pre-shrink baseline.
# 412 entries across 9 packages:
#   client               13
#   converters          127
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
    "storage": (
        "SIDECAR_VERSION",
    ),
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
        "detect_ocr_langs",
        "docx_to_markdown",
        "ensure_tessdata",
        "html_to_markdown_with_images",
        "image_to_markdown",
        "libreoffice_to_pdf",
        "normalize_dashes",
        "numbering_depth",
        "pdf_markdown_converters",
        "pdf_to_markdown",
        "pdf_to_markdown_docling",
        "pptx_to_markdown",
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
        "_infer_presentation_forms",
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


class TestFacadeSurfaceIsFrozen:
    """Guard 1 + 3: the barrel surface may not drift, and may not lie."""

    @pytest.mark.parametrize("package", sorted(FROZEN_SURFACE))
    def test_all_matches_frozen_literal(self, package: str) -> None:
        module = importlib.import_module(f"pageindex_mcp.{package}")
        actual = set(module.__all__)
        expected = set(FROZEN_SURFACE[package])

        added = sorted(actual - expected)
        removed = sorted(expected - actual)

        assert not added and not removed, (
            f"pageindex_mcp.{package}.__all__ drifted from the RFC-045 freeze.\n"
            f"  added (not in FROZEN_SURFACE):   {added or '-'}\n"
            f"  removed (still in FROZEN_SURFACE): {removed or '-'}\n"
            "If the change is deliberate, update FROZEN_SURFACE in this file in "
            "the SAME commit and say why in the commit body. If you are removing "
            "a name, check UNEXERCISED_CONSUMERS below first -- this suite does "
            "not import services/ or issue/, so it cannot tell you when they break."
        )

    @pytest.mark.parametrize("package", sorted(FROZEN_SURFACE))
    def test_all_has_no_duplicates(self, package: str) -> None:
        module = importlib.import_module(f"pageindex_mcp.{package}")
        names = list(module.__all__)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"pageindex_mcp.{package}.__all__ lists {duplicates} more than once"

    @pytest.mark.parametrize("package", sorted(FROZEN_SURFACE))
    def test_every_frozen_name_resolves(self, package: str) -> None:
        module = importlib.import_module(f"pageindex_mcp.{package}")
        missing = [n for n in FROZEN_SURFACE[package] if not hasattr(module, n)]
        assert not missing, (
            f"pageindex_mcp.{package}.__all__ names {missing}, which the package "
            "never binds. `from pageindex_mcp."
            f"{package} import *` would raise AttributeError."
        )


class TestUnexercisedConsumerContract:
    """Guard 2: names whose only consumers this suite cannot exercise."""

    @pytest.mark.parametrize(
        "module_path,name,consumer",
        UNEXERCISED_CONSUMERS,
        ids=[f"{m.rsplit('.', 1)[-1]}.{n}" for m, n, _ in UNEXERCISED_CONSUMERS],
    )
    def test_pinned_name_resolves(self, module_path: str, name: str, consumer: str) -> None:
        module = importlib.import_module(module_path)
        assert hasattr(module, name), (
            f"{module_path}.{name} no longer resolves, but {consumer} imports it "
            "through the facade. Nothing else in this suite covers that consumer, "
            "so removing this name ships a runtime break. Re-export it, or update "
            "the consumer and this pin together."
        )


# ---------------------------------------------------------------------------
# Guard 3: every pageindex_mcp reference in non-test code outside src/ resolves
# ---------------------------------------------------------------------------
# This is the check that would have caught the ``C._relevel_by_numbering``
# break on the commit that introduced it (06b2bae, 2 years of latency). It is
# NOT the rejected consumer counter: it asks only "does this reference
# resolve", never "is this export used", so it has no way to be quietly wrong
# about liveness. It scales to consumers nobody has thought to pin by hand.

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_CONSUMER_DIRS = ("issue", "services", "scripts")
_CONSUMER_FILES = (
    "mcp_server.py",
    "preprocess_client.py",
    "promotion_sweep.py",
    "ingest_via_server.py",
    "stress_test.py",
)


def _is_submodule(dotted: str) -> bool:
    """True if ``dotted`` names a module/package on disk rather than a symbol."""
    rel = dotted.replace(".", "/")
    return (_SRC / f"{rel}.py").exists() or (_SRC / rel / "__init__.py").exists()


def _collect_consumer_refs() -> list[tuple[str, str, str]]:
    """(module, name, location) for every pageindex_mcp symbol these files use."""
    root = pathlib.Path(__file__).resolve().parents[1]
    paths: list[pathlib.Path] = []
    for d in _CONSUMER_DIRS:
        paths += sorted((root / d).rglob("*.py")) if (root / d).is_dir() else []
    paths += [root / f for f in _CONSUMER_FILES if (root / f).is_file()]

    refs: list[tuple[str, str, str]] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        loc = path.relative_to(root)
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pageindex_mcp"):
                for a in node.names:
                    dotted = f"{node.module}.{a.name}"
                    if _is_submodule(dotted):
                        aliases[a.asname or a.name] = dotted
                    else:
                        refs.append((node.module, a.name, f"{loc}:{node.lineno}"))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("pageindex_mcp"):
                        aliases[a.asname or a.name.split(".")[0]] = a.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                module = aliases.get(node.value.id)
                if module:
                    refs.append((module, node.attr, f"{loc}:{node.lineno}"))

    seen: set[tuple[str, str]] = set()
    unique = []
    for module, name, where in refs:
        if (module, name) not in seen:
            seen.add((module, name))
            unique.append((module, name, where))
    return unique


CONSUMER_REFS = _collect_consumer_refs()


class TestConsumerReferencesResolve:
    """Guard 3: no consumer outside src/ may name something that does not exist."""

    def test_sweep_found_consumers(self) -> None:
        assert len(CONSUMER_REFS) >= 40, (
            f"only {len(CONSUMER_REFS)} consumer references found -- the AST sweep "
            "has probably stopped seeing issue/ or services/, which would make "
            "TestConsumerReferencesResolve vacuously green"
        )

    @pytest.mark.parametrize(
        "module_path,name,location",
        CONSUMER_REFS,
        ids=[f"{m.rsplit('.', 1)[-1]}.{n}" for m, n, _ in CONSUMER_REFS],
    )
    def test_reference_resolves(self, module_path: str, name: str, location: str) -> None:
        module = importlib.import_module(module_path)
        assert hasattr(module, name), (
            f"{location} references {module_path}.{name}, which does not exist. "
            "Either the symbol moved (import it from its submodule) or it was "
            "removed and this consumer was never updated."
        )


class TestRemovedBindingsAreGone:
    """RFC-045 Property 2, second half: a removal deletes the entry *and* the
    binding.

    Stripping a name from ``__all__`` while leaving ``from .mod import name`` in
    place looks done and is not: ``pkg.name`` still resolves, so the barrel has
    not actually shrunk and the name can drift back into ``__all__`` unnoticed.
    Added by RFC-045 task 0.4.
    """

    def test_removed_names_are_not_package_attributes(self):
        import importlib

        stale: list[str] = []
        for pkg_name, names in REMOVED_SURFACE.items():
            pkg = importlib.import_module(f"pageindex_mcp.{pkg_name}")
            for name in names:
                if hasattr(pkg, name):
                    stale.append(f"pageindex_mcp.{pkg_name}.{name}")
        assert not stale, (
            "RFC-045 Property 2: these names left __all__ but their bindings "
            f"survive, so the facade did not actually shrink: {sorted(stale)}"
        )

    def test_removed_names_are_not_in_all(self):
        import importlib

        stale: list[str] = []
        for pkg_name, names in REMOVED_SURFACE.items():
            pkg = importlib.import_module(f"pageindex_mcp.{pkg_name}")
            for name in names:
                if name in getattr(pkg, "__all__", ()):
                    stale.append(f"pageindex_mcp.{pkg_name}.{name}")
        assert not stale, f"RFC-045 Property 2: still exported: {sorted(stale)}"

    def test_removed_and_frozen_sets_are_disjoint(self):
        """A name cannot be both retained and removed."""
        for pkg_name, names in REMOVED_SURFACE.items():
            overlap = set(names) & set(FROZEN_SURFACE.get(pkg_name, ()))
            assert not overlap, (
                f"{pkg_name}: {sorted(overlap)} appear in both FROZEN_SURFACE "
                "and REMOVED_SURFACE"
            )
