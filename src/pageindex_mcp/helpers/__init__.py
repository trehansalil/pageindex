"""helpers package — re-exports every public symbol for backward compatibility.

``from pageindex_mcp.helpers import X`` continues to work after the split.
"""
# ruff: noqa: F401, E402

from __future__ import annotations

# ── script.py re-exports (kept for backward compat) ─────────────────────────
from ..script import (
    _JOINING_TYPE as _JOINING_TYPE,
)
from ..script import (
    AR_RUN_RE,
    ARABIC_RANGES,
    PRESENTATION_RANGES,
    BlobKind,
    RtlDecision,
    ScriptContext,
    _word_has_reversed_morphology,
    decide_rtl,
    normalize_dashes,
    normalize_for_garble,
)
from ..script import (
    _infer_script as _infer_script,
)
from ..script import (
    _script_from_filename as _script_from_filename,
)
from ..script import (
    arabic_readability_score as _arabic_readability_score,
)
from ..script import (
    is_arabic_char as _is_arabic_char,
)

# ── types ────────────────────────────────────────────────────────────────────
from .types import (
    _UNSET,
    ExtractionState,
    GateOutcome,
    GateSpec,
    LowQualityTreeError,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    VerdictResult,
    VerdictThresholds,
    _GateFn,
    _get_verdict_thresholds,
    _ReasonPolicy,
    _Unset,
    decide_route,
    reset_verdict_thresholds,
)

ExtractionSnapshot = RecoveryOutcome

# ── tree_validation ──────────────────────────────────────────────────────────
# ── flat ─────────────────────────────────────────────────────────────────────
from .flat import (
    _flat_block_primary_text,
    _flat_block_text,
    _flat_search_text,
    flat_doc_view,
    route_and_extract_flat,
)

# ── garble ───────────────────────────────────────────────────────────────────
from .garble import (
    _COMMON_WORDS,
    _EMPTY_NODE_FRACTION_THRESHOLD,
    _GARBLE_FLAT_MARKDOWN_NORMALIZE,
    _GARBLE_NODE_RATIO_THRESHOLD,
    _GARBLE_NODE_RATIO_THRESHOLD_RAW,
    _GARBLE_SHORT_TEXT_DEFAULT,
    _LATIN_TOKEN_RE,
    _MIXED_SCRIPT_RE,
    _RFC029_DEEP_TREE_DEPTH_THRESHOLD,
    _RFC029_FLAT_PREFER_MULTIPLIER,
    _RFC029_MIN_CHARS_PER_NODE,
    _RFC029_MIN_CHARS_PER_NODE_DEEP,
    _RFC029_MIN_SCANNED_DENSITY_FLOOR,
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleConfig,
    GarbleProfile,
    GarbleReport,
    _garble_check_nodes,
    _garble_config,
    _garble_ratio,
    _is_morphologically_nonsense,
    _latin_token_ratio,
    _rebuild_garble_config_compat,
    check_garble,
    detect_garble,
    garble_prongs,
    hash_pipe_ratio,
    infer_script,
    ocr_noise_ratio,
)

# ── gates ────────────────────────────────────────────────────────────────────
from .gates import (
    _FLAT_APPLICABLE_DEFECTS,
    _GATE_PRIORITY,
    FEATURE_WIRINGS,
    FLAT_GATE_SUBSET,
    GATE_TABLE,
    GATES,
    HARD_FAIL_DEFECTS,
    REASON_POLICY,
    FeatureWiring,
    _gate_bidi_degraded,
    _gate_depth_low,
    _gate_empty_node_contamination,
    _gate_garbling,
    _gate_low_content_density,
    _gate_node_count_low,
    _gate_node_garbling,
    _gate_reordered,
    _gate_rtl_reversal,
    _gate_suspect_density,
    validate_feature_wirings,
)

# ── rag ──────────────────────────────────────────────────────────────────────
from .rag import (
    _build_node_map,
    _check_registry_complete_cached,
    _extract_json_object,
    _extract_page_hits,
    _llm,
    _parse_page_spec,
    _prefilter_docs,
    _rag,
    _rag_inner,
    _search_one_doc,
    _strip_text,
)

# ── table_stitch ─────────────────────────────────────────────────────────────
from .table_stitch import (
    _is_continuation_table,
    _looks_like_toc_page,
    _merge_continuation_table,
    _strip_toc_heading_nodes,
    _strip_toc_heading_nodes_guarded,
    flag_empty_cells,
    stitch_continuation_tables,
)

# ── tables ───────────────────────────────────────────────────────────────────
from .tables import (
    _flat_is_pipe_row,
    _flat_is_separator_row,
    _flat_parse_table,
    _flat_split_pipe_row,
    _flat_verbalize_rows,
    _forward_fill_leading_column,
)

# ── tree_split ───────────────────────────────────────────────────────────────
from .tree_split import (
    _OVERSIZED_ORDINAL_RE,
    _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD,
    _RFC029_TABLE_SEGMENT_MIN_ROWS,
    _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE,
    _RFC036_SINGLETON_RATIO_LANDSCAPE,
    _RFC036_SINGLETON_ROW_RATIO_THRESHOLD,
    _apply_split,
    _blank_line_fallback_enabled,
    _fold_with_index_map,
    _has_heading_markers,
    _longest_increasing_run,
    _looks_like_frontmatter_toc,
    _ordinal_value,
    _roman_to_int,
    _segment_table_nodes,
    _split_on_atx_headings,
    _split_on_blank_line_paragraphs,
    _split_on_generic_numbered_lines,
    _split_on_paragraph_markers,
    _synthesize_preamble_node,
    prepare_tree,
    split_oversized_leaf_nodes,
    table_is_rtl,
)
from .tree_validation import (
    TreeSignals,
    _count_empty_body_nodes,
    _flatten_tree_text,
    _tree_depth,
    _tree_is_reordered,
    _tree_max_leaf_ratio,
    _tree_node_count,
    _walk_leaves,
    validate_tree,
)

# ── verdict ──────────────────────────────────────────────────────────────────
from .verdict import (
    _clamp_pass,
    _classify_image_verdict,
    _dedupe_chart_text_lines,
    _defect_from_reason_str,
    apply_promotions,
    classify_verdict,
    compute_image_enrichment_ratio,
    compute_verdict,
    detect_regression,
    evaluate_gates,
)

__all__ = [
    "BULK_PROFILE",
    "FEATURE_WIRINGS",
    "FLAT_GATE_SUBSET",
    "FLAT_MARKDOWN_PROFILE",
    # gates
    "GATES",
    "GATE_TABLE",
    "HARD_FAIL_DEFECTS",
    "REASON_POLICY",
    "_FLAT_APPLICABLE_DEFECTS",
    "_GATE_PRIORITY",
    # script re-exports
    "_JOINING_TYPE",
    "_UNSET",
    "BlobKind",
    "ExtractionSnapshot",
    "ExtractionState",
    "FeatureWiring",
    # garble
    "GarbleConfig",
    "GarbleProfile",
    "GarbleReport",
    "GateOutcome",
    "GateSpec",
    "LowQualityTreeError",
    "RecoveryOutcome",
    "Route",
    "ScriptContext",
    # types
    "TreeDefect",
    "TreeGateResult",
    # tree_validation
    "TreeSignals",
    "VerdictResult",
    "VerdictThresholds",
    "_GateFn",
    "_ReasonPolicy",
    "_Unset",
    "_count_empty_body_nodes",
    "_extract_page_hits",
    "_flat_block_text",
    "_flat_is_pipe_row",
    "_flat_is_separator_row",
    # tables
    "_flat_parse_table",
    "_flat_search_text",
    "_flat_split_pipe_row",
    "_flat_verbalize_rows",
    "_flatten_tree_text",
    "_forward_fill_leading_column",
    "_garble_check_nodes",
    "_garble_config",
    "_garble_ratio",
    "_get_verdict_thresholds",
    "_infer_script",
    "_is_morphologically_nonsense",
    "_llm",
    "_looks_like_toc_page",
    # rag
    "_rag",
    "_rag_inner",
    "_script_from_filename",
    "_segment_table_nodes",
    "_strip_text",
    "_strip_toc_heading_nodes_guarded",
    "_tree_depth",
    "_tree_is_reordered",
    "_tree_max_leaf_ratio",
    "_tree_node_count",
    "_walk_leaves",
    "apply_promotions",
    "check_garble",
    "classify_verdict",
    "compute_image_enrichment_ratio",
    "compute_verdict",
    "decide_route",
    "decide_rtl",
    "detect_garble",
    "detect_regression",
    # verdict
    "evaluate_gates",
    "flag_empty_cells",
    "flat_doc_view",
    "garble_prongs",
    "hash_pipe_ratio",
    "infer_script",
    "normalize_dashes",
    "normalize_for_garble",
    "ocr_noise_ratio",
    # tree_split
    "prepare_tree",
    "reset_verdict_thresholds",
    # flat
    "route_and_extract_flat",
    "split_oversized_leaf_nodes",
    # table_stitch
    "stitch_continuation_tables",
    "table_is_rtl",
    "validate_feature_wirings",
    "validate_tree",
]
