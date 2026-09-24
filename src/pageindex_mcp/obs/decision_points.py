"""``DECISION_POINTS`` -- the decision-point registry (RFC-046 D12, task 12.5).

This module is **data, not behaviour**. It names every branch whose outcome
changes what happens to a document, the :class:`~.phases.Phase` that branch
belongs to, the closed set of ``choice`` values it may emit, and the closed
set of ``attrs`` keys it may carry.

Why a registry at all (R12.5): a decision record is only useful if a reader
can RECONSTRUCT THE BRANCH from it. That requires the allowed choices and
the allowed attrs keys to be declared somewhere a test can read, so that:

* **task 12.6** can AST-scan every ``decision(...)`` call site in ``src/``
  and assert its ``event`` is registered here, its ``choice`` literals are a
  subset of :attr:`DecisionPoint.choices`, and its ``attrs`` keys are a
  subset of :attr:`DecisionPoint.attrs` -- and that no attrs key anywhere
  looks like document content (:data:`FORBIDDEN_ATTR_SUBSTRINGS`);
* **task 12.8** can assert that every registered point with
  ``always_emits=True`` actually produced a record over a corpus run.

Nothing here reads the pipeline, imports the instrumented modules, or has
side effects -- importing it is free and safe from any test.

Content safety (R12.7)
----------------------
Every ``attrs`` key below is a count, a length, a ratio, a bounded
identifier (enum member name, converter name, exception CLASS name, language
code, gate/promotion label), a page number, or a boolean. No key names
document text: no ``md_content``, no node text or titles, no summaries, no
table cells, no OCR output, no LLM prompts/completions, no ``str(exc)``, no
absolute paths, no filenames. Correlation (``run_id``/``job_id``/
``doc_sha8``/``doc_id``) arrives from the contextvar filter and is therefore
deliberately NOT repeated as an attrs key on any point.

Cost (R12.9)
------------
:attr:`DecisionPoint.level` is ``INFO`` for every once-per-document decision
(R12.6 -- a level that hides the decision layer fails the requirement). It
is ``DEBUG`` only for the per-node / per-region / per-marker records R12.6
carves out as the exception, and those carry a :attr:`DecisionPoint.cap`:
the maximum number of records phase 3 may emit for that event per document.

Behaviour neutrality (R12.12)
-----------------------------
This registry describes branches that already exist. It reclassifies no
document and moves no verdict. If instrumenting one of these points would
require changing the branch, phase 3 must STOP and report it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .phases import Phase

__all__ = [
    "DECISION_EVENTS",
    "DECISION_POINTS",
    "DECISION_POINTS_BY_EVENT",
    "FORBIDDEN_ATTR_SUBSTRINGS",
    "INSTRUMENTED_MODULES",
    "SAFE_ATTR_EXCEPTIONS",
    "SAFE_ATTR_SUFFIXES",
    "DecisionPoint",
    "allowed_attrs",
    "allowed_choices",
    "content_attr_violations",
    "is_content_attr",
    "point_for",
]


@dataclass(frozen=True, slots=True)
class DecisionPoint:
    """One branch whose outcome changes what happens to a document."""

    #: snake_case event name. Unique across the registry; it is the ``event=``
    #: argument of the ``decision()`` call phase 3 adds.
    event: str
    #: Coarse pipeline phase the branch sits in.
    phase: Phase
    #: Dotted module path, e.g. ``pageindex_mcp.helpers.types``.
    module: str
    #: Enclosing function (or ``Class.method`` / ``outer.inner`` for a closure).
    function: str
    #: Closed set of ``choice`` values. A call site may emit no other value.
    choices: tuple[str, ...]
    #: Closed set of ``attrs`` keys. A call site may emit a subset, never a
    #: superset. Every key is content-free by construction (see module docs).
    attrs: tuple[str, ...] = ()
    #: True when the branch's computed outcome can be OVERRIDDEN downstream.
    #: R12.5: such a record MUST carry BOTH the computed and the forced value.
    overridden: bool = False
    #: Emission level name. ``INFO`` unless this is the per-node/per-region
    #: R12.6 exception.
    level: str = "INFO"
    #: Max records per document for a repeating point; ``None`` = fires at
    #: most once (or a small bounded number) per document.
    cap: int | None = None
    #: False when the branch is only reachable on a legacy/conditional path,
    #: so task 12.8 must not require a record for it on every corpus run.
    always_emits: bool = True
    #: Why this point matters / what phase 3 must be careful about.
    note: str = ""

    @property
    def levelno(self) -> int:
        return logging.getLevelName(self.level)  # type: ignore[return-value]


def _p(**kwargs) -> DecisionPoint:
    return DecisionPoint(**kwargs)


_H_TYPES = "pageindex_mcp.helpers.types"
_H_GATES = "pageindex_mcp.helpers.gates"
_H_GARBLE = "pageindex_mcp.helpers.garble"
_H_TREEVAL = "pageindex_mcp.helpers.tree_validation"
_H_VERDICT = "pageindex_mcp.helpers.verdict"
_H_ARBITRATE = "pageindex_mcp.helpers.arbitrate"
_C_INDEXER = "pageindex_mcp.client.indexer"
_C_RECOVERY = "pageindex_mcp.client.recovery"
_X_PICTURES = "pageindex_mcp.converters.pictures"
_X_PIPELINE = "pageindex_mcp.converters.pipeline"
_X_OCRLANGS = "pageindex_mcp.converters.ocr_langs"
_X_PRECLASSIFY = "pageindex_mcp.converters.preclassify"
_S_DOCUMENTS = "pageindex_mcp.storage.documents"
_W_SUBPROC = "pageindex_mcp.worker.subprocess_mgr"


# ---------------------------------------------------------------------------
# helpers/types.py -- route finalisation (THE override site named by R12.5)
# ---------------------------------------------------------------------------
_TYPES_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="route_selected",
        phase=Phase.ROUTE_SELECT,
        module=_H_TYPES,
        function="finalize_gate_and_route",
        choices=("tree", "flat", "reject", "persist_fail"),
        attrs=(
            "computed_route",
            "final_route",
            "forced",
            "first_defect",
            "flat_routing_enabled",
            "recovery_method",
            "recovery_succeeded",
        ),
        overridden=True,
        note=(
            "R12.5's headline case: decide_route()'s answer at types.py:457 is "
            "silently overwritten by force_route at 459-460. Capture the "
            "decide_route() return into a local BEFORE the force check so "
            "computed_route AND final_route both survive. Emit AFTER the "
            "`finally: _guard_bypass.active = False` (line 464), never inside "
            "the try (R12.8). Do NOT instrument decide_route() itself -- it "
            "also runs at import time from gates.py:532."
        ),
    ),
    _p(
        event="gate_ok_finalized",
        phase=Phase.ROUTE_SELECT,
        module=_H_TYPES,
        function="finalize_gate_and_route",
        choices=("ok", "not_ok"),
        attrs=("computed_ok", "final_ok", "forced", "first_defect"),
        overridden=True,
        note=(
            "state.ok is overwritten independently of state.route (force_ok "
            "without force_route happens at the RTL-comparison sites), so this "
            "stays a separate record. Same emit-after-finally constraint."
        ),
    ),
)


# ---------------------------------------------------------------------------
# helpers/gates.py -- individual gate firings + recovery eligibility predicates
# NOTE: TestHotPathConfigAccessGuard forbids NEW os.environ/getenv reads here.
# Every config attr below is already read by the existing code as a local.
# ---------------------------------------------------------------------------
_GATES_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="bidi_degraded_gate",
        phase=Phase.TREE_VALIDATE,
        module=_H_GATES,
        function="_gate_bidi_degraded",
        choices=("fires", "suppressed_by_config", "no_rtl_decision"),
        attrs=("bidi_coherence_enforce", "reversed_signal", "pres_forms_signal"),
        note="Reuse the pipeline_config.bidi_coherence_enforce read already at line 161.",
    ),
    _p(
        event="empty_node_contamination_gate",
        phase=Phase.TREE_VALIDATE,
        module=_H_GATES,
        function="_gate_empty_node_contamination",
        choices=("fires", "clear", "not_evaluated_zero_nonroot_nodes"),
        attrs=("empty_fraction", "empty_leaf", "empty_non_leaf", "total_non_root"),
    ),
    _p(
        event="low_content_density_gate",
        phase=Phase.TREE_VALIDATE,
        module=_H_GATES,
        function="_gate_low_content_density",
        choices=("fires", "clear", "not_evaluated_below_min_nodes"),
        attrs=(
            "node_count",
            "depth",
            "is_deep",
            "is_arabic",
            "chars_per_node",
            "threshold",
        ),
        note=(
            "Carry the RESOLVED threshold, not just is_deep/is_arabic -- the "
            "branch picks between two constants and only the resolved value "
            "reconstructs which literal was used."
        ),
    ),
    _p(
        event="suspect_density_gate",
        phase=Phase.TREE_VALIDATE,
        module=_H_GATES,
        function="_gate_suspect_density",
        choices=("fires", "clear", "not_evaluated_no_page_count"),
        attrs=(
            "page_count",
            "chars_per_page",
            "chars_per_page_corrected",
            "corrected_delta",
            "verdict_would_change",
            "floor_used",
            "floor_arabic",
            "is_arabic",
        ),
    ),
    _p(
        event="garble_recovery_eligible",
        phase=Phase.RECOVERY,
        module=_H_GATES,
        function="_eligible_garble",
        choices=("eligible", "not_eligible_gate_passed", "not_eligible_defect_absent"),
        attrs=("state_ok", "garble_defect_present"),
        note="Reads state.ok/defects only; never writes (R12.8).",
    ),
    _p(
        event="low_content_recovery_eligible",
        phase=Phase.RECOVERY,
        module=_H_GATES,
        function="_eligible_low_content",
        choices=(
            "eligible",
            "not_eligible_gate_passed",
            "not_eligible_defect_absent",
            "not_eligible_flag_disabled",
        ),
        attrs=("state_ok", "node_count_low_present", "ocr_escalation_low_content"),
    ),
    _p(
        event="image_dominant_recovery_eligible",
        phase=Phase.RECOVERY,
        module=_H_GATES,
        function="_eligible_image_dominant",
        choices=(
            "eligible",
            "not_eligible_gate_passed",
            "not_eligible_defect_absent",
            "not_eligible_flag_disabled",
        ),
        attrs=(
            "state_ok",
            "depth_low_present",
            "image_dominant_ocr_escalation_enabled",
        ),
    ),
    _p(
        event="rtl_recovery_eligible",
        phase=Phase.RECOVERY,
        module=_H_GATES,
        function="_eligible_rtl",
        choices=("eligible", "not_eligible_gate_passed", "not_eligible_defect_absent"),
        attrs=("state_ok", "rtl_reversal_present"),
    ),
)


# ---------------------------------------------------------------------------
# helpers/garble.py -- garble detection.
# detect_garble()/_garble_prongs() run PER NODE and PER BLOCK: they are the
# R12.6 per-node exception -> DEBUG + capped. The two aggregates are INFO.
# ---------------------------------------------------------------------------
_GARBLE_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="garble_prong_evaluation",
        phase=Phase.GARBLE_CHECK,
        module=_H_GARBLE,
        function="_garble_prongs",
        choices=("fired", "clean"),
        attrs=(
            "norm_blob_len",
            "expected_script",
            "had_presentation_forms",
            "fired_prong_count",
            "fired_prongs",
            "garble_latin_ratio",
            "garble_nonsense_ratio",
            "garble_digit_floor",
        ),
        level="DEBUG",
        cap=50,
        always_emits=False,
        note=(
            "Multi-label: fired_prongs carries every prong that fired, the "
            "choice is only fired/clean. Per-blob hot path -- R12.9: the "
            "isEnabledFor(DEBUG) guard MUST precede building the attrs dict, "
            "and the cap must be enforced by a counter, not by the logger."
        ),
    ),
    _p(
        event="garble_verdict",
        phase=Phase.GARBLE_CHECK,
        module=_H_GARBLE,
        function="detect_garble",
        choices=("garbled", "clean"),
        attrs=(
            "fired_prongs",
            "blob_kind",
            "blob_len",
            "dominant_script",
            "had_presentation_forms",
            "short_text_prior_applicable",
        ),
        level="DEBUG",
        cap=50,
        always_emits=False,
        note="Per-blob wrapper around the prongs. Same DEBUG+cap treatment.",
    ),
    _p(
        event="garble_whole_tree_fallback",
        phase=Phase.GARBLE_CHECK,
        module=_H_GARBLE,
        function="_garble_check_nodes",
        choices=("fallback_fired", "fallback_clean", "fallback_not_reached"),
        attrs=("per_node_garbled_count", "concat_text_len", "fired_prongs"),
        overridden=True,
        note=(
            "The per-node aggregate (0 garbled) is replaced by the fallback's "
            "answer (1) when it fires -- per_node_garbled_count=0 must be an "
            "explicit attr or the computed value is lost (R12.5). An existing "
            "logger.info at 775-778 already reports fired_prongs: MIGRATE it, "
            "do not duplicate it, and keep INFO."
        ),
    ),
    _p(
        event="garble_flat_block_verdict",
        phase=Phase.GARBLE_CHECK,
        module=_H_GARBLE,
        function="_garble_check_flat_blocks",
        choices=("garbled", "clean", "below_threshold"),
        attrs=(
            "checked_count",
            "garbled_count",
            "garble_ratio",
            "threshold",
            "fired_prongs",
        ),
    ),
)


# ---------------------------------------------------------------------------
# helpers/tree_validation.py -- the tree-quality gate (CLAUDE.md Hard Rule 5)
# ---------------------------------------------------------------------------
_TREEVAL_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="tree_effective_garble_verdict",
        phase=Phase.TREE_VALIDATE,
        module=_H_TREEVAL,
        function="TreeSignals.from_tree",
        choices=("effectively_garbled", "sub_threshold_garbled", "not_garbled"),
        attrs=(
            "garbled",
            "garble_ratio",
            "garble_threshold",
            "garble_prongs",
            "node_count",
            "depth",
            "max_leaf_ratio",
            "is_reordered",
        ),
    ),
    _p(
        event="gate_primary_defect_selection",
        phase=Phase.TREE_VALIDATE,
        module=_H_TREEVAL,
        function="validate_tree",
        choices=("first_fired_wins", "garble_override"),
        attrs=(
            "computed_primary_defect",
            "final_primary_defect",
            "garble_co_fired",
            "fired_defects",
            "fired_count",
        ),
        overridden=True,
        note=(
            "validate_tree:446-455 silently replaces fired[0] with a co-firing "
            "garble defect. Same trap as force_route -- BOTH labels required."
        ),
    ),
    _p(
        event="tree_gate_verdict",
        phase=Phase.TREE_VALIDATE,
        module=_H_TREEVAL,
        function="validate_tree",
        choices=("gate_passed", "gate_failed"),
        attrs=(
            "primary_defect",
            "all_defects",
            "node_count",
            "depth",
            "garble_ratio",
            "max_leaf_ratio",
            "is_reordered",
            "warning_labels",
        ),
        note=(
            "THE 'never silently persist a low-quality tree' decision. "
            "`warning_labels` is the near-gate warning NAMES only "
            "(near_gate_node_count, ...). The formatted f-string variants it "
            "was once contrasted against lived on TreeGateResult.warnings, "
            "which was deleted 2026-09-22 as unreachable; these labels are now "
            "the only surviving near-gate signal. Log "
            "primary_defect as the TreeDefect enum value, not a gate `detail` "
            "string, unless phase 3 has confirmed every gate detail string is "
            "content-free."
        ),
    ),
)


# ---------------------------------------------------------------------------
# helpers/verdict.py -- gate evaluation + promotion pipeline
# NOTE: hot-path config guard covers this file: no new env reads.
# ---------------------------------------------------------------------------
_VERDICT_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="zero_content_check",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="evaluate_gates",
        choices=("fail_zero_content", "continue"),
        attrs=("node_count", "flat_text_len"),
    ),
    _p(
        event="reordered_defect_inferred",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="evaluate_gates",
        choices=("reordered_inferred", "no_override"),
        attrs=("validate_result_present", "is_reordered"),
        always_emits=False,
        note=(
            "Only reachable on the legacy call path (validate_result is None). "
            "Registered for completeness; task 12.8 must not require a record."
        ),
    ),
    _p(
        event="hard_fail_resolution",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="evaluate_gates",
        choices=("primary_hard_fail", "masked_hard_fail", "no_hard_fail"),
        attrs=("defect", "all_defects_count", "masked_defect", "worst_defect"),
        note=(
            "masked_hard_fail = the first-fired defect is not a hard fail but a "
            "co-firing one is; _GATE_PRIORITY picks the worst. Short-circuits "
            "apply_promotions, so nothing downstream can override it."
        ),
    ),
    _p(
        event="image_standalone_verdict",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions",
        choices=("pass", "marginal", "fail"),
        attrs=("content_class", "image_enrichment_ratio"),
    ),
    _p(
        event="content_volume_floor",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions",
        choices=("fail_insufficient_content", "continue"),
        attrs=("stripped_len", "min_marginal_chars"),
    ),
    _p(
        event="structural_hard_fail_gate",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions",
        choices=("hard_fail", "image_enrichment_exception", "continue"),
        attrs=(
            "max_leaf_ratio",
            "hard_fail_max_leaf_ratio",
            "image_enrichment_available",
        ),
    ),
    _p(
        event="promotion_pipeline",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions",
        choices=(
            "image_enrichment",
            "structural_pass",
            "cat_a",
            "cat_b",
            "cat_c",
            "small_doc",
            "none",
        ),
        attrs=("matched_paths", "content_class"),
        note=(
            "VG-6 already records the ORDERED list of every matching path. The "
            "record MUST carry the full matched_paths list, not just the "
            "winner -- logging only the winner regresses existing auditability."
        ),
    ),
    _p(
        event="promotion_clamp",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions._apply_clamp",
        choices=("bypass_source_selection", "clamped_pass", "clamped_marginal"),
        attrs=("source_selection", "is_image_enrichment", "pre_clamp_reason_kind"),
        cap=2,
        note="Nested closure with two call sites (verdict.py:509 and 562).",
    ),
    _p(
        event="promotion_fallback_marginal",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="apply_promotions",
        choices=("garbling", "node_count_low", "depth_low", "leaf_concentration"),
        attrs=("garble_ratio", "node_count", "depth", "max_leaf_ratio"),
        always_emits=False,
        note="Reached only when promotion_pipeline matched nothing.",
    ),
    _p(
        event="regression_detected",
        phase=Phase.VERDICT,
        module=_H_VERDICT,
        function="detect_regression",
        choices=("regressed", "clean", "no_baseline"),
        attrs=(
            "cur_count",
            "prev_node_count",
            "cur_ratio",
            "prev_max_leaf_ratio",
            "count_dropped",
            "ratio_grew",
        ),
        always_emits=False,
        note="no_baseline is the early return; distinguish it from a real clean.",
    ),
)


# ---------------------------------------------------------------------------
# converters/ocr_langs.py -- language selection + tessdata availability
# ---------------------------------------------------------------------------
_OCRLANGS_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="ocr_lang_selection",
        phase=Phase.LANGUAGE_SELECT,
        module=_X_OCRLANGS,
        function="detect_ocr_langs",
        choices=(
            "empty_sample_default",
            "no_script_letters_default",
            "arabic_with_latin",
            "arabic_only",
            "bilingual_gazette",
            "german_diacritic_hint",
            "english_fallthrough",
        ),
        attrs=(
            "sample_len",
            "ar_count",
            "latin_count",
            "ar_ratio",
            "latin_ratio",
            "chosen_langs",
        ),
        note=(
            "R12.7: derive only lengths/counts/ratios from `sample`. Never log "
            "`sample`, a regex match, or any substring of it."
        ),
    ),
    _p(
        event="ocr_lang_availability_no_prefix",
        phase=Phase.OCR,
        module=_X_OCRLANGS,
        function="ensure_tessdata",
        choices=(
            "latin_trusted",
            "non_latin_cached_available",
            "non_latin_cached_raise",
            "non_latin_probed_found",
            "non_latin_probed_missing_raise",
        ),
        attrs=("lang", "cache_hit", "probe_found"),
        cap=8,
        note=(
            "The two *_raise choices must be emitted immediately BEFORE the "
            "raise -- after it is unreachable -- and must never mask "
            "TessdataUnavailableError (R12.8)."
        ),
    ),
    _p(
        event="ocr_lang_availability_with_prefix",
        phase=Phase.OCR,
        module=_X_OCRLANGS,
        function="ensure_tessdata",
        choices=(
            "prefix_path_exists",
            "prefix_download_success",
            "prefix_missing_non_latin_raise",
            "prefix_missing_latin_dropped",
        ),
        attrs=("lang", "allow_dl", "traineddata_present"),
        cap=8,
    ),
    _p(
        event="ocr_lang_final_fallback",
        phase=Phase.OCR,
        module=_X_OCRLANGS,
        function="ensure_tessdata",
        choices=("raise_non_latin_requested", "fallback_deu_eng"),
        attrs=("requested_langs", "had_non_latin"),
        always_emits=False,
        note="Only reached when the available list ended empty.",
    ),
    _p(
        event="tessdata_download_result",
        phase=Phase.OCR,
        module=_X_OCRLANGS,
        function="_try_download_tessdata",
        choices=("success", "failed"),
        attrs=("lang", "bytes_downloaded", "failure_exc_type"),
        always_emits=False,
        note="failure_exc_type is type(exc).__name__ ONLY -- never str(exc).",
    ),
)


# ---------------------------------------------------------------------------
# client/indexer.py -- conversion route, OCR forcing, recovery dispatch,
# persistence route. The central per-document flow.
# ---------------------------------------------------------------------------
_INDEXER_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="hash_cache_dedup_skip",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="index",
        choices=("skip_unchanged_reuse_doc_id", "reprocess"),
        attrs=("sha256_matched", "existing_doc_found"),
        note="When it skips, the entire pipeline is bypassed -- high-value record.",
    ),
    _p(
        event="stage_duration",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_emit_stage_timings",
        choices=("extraction", "tree_build", "recovery"),
        attrs=("duration_ms",),
        cap=3,
        always_emits=False,
        note="RFC-050 Task 1.5: one record per stage per non-deduped document, "
        "emitted when index() exits (success, reject or error). Stages are "
        "disjoint: tree_build is excluded from the extraction/recovery wall time.",
    ),
    _p(
        event="config_drift_detected",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_detect_config_drift",
        choices=("drift_detected", "no_drift"),
        attrs=("drift_present",),
        note="Boolean only. Never log the config dicts themselves.",
    ),
    _p(
        event="pdf_inspector_force_ocr",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("forced_by_inspector", "not_forced"),
        attrs=("pdf_type", "confidence", "inspector_confidence_threshold"),
        note="RFC-032. Migrate the existing INFO log at 477-483 rather than doubling it.",
    ),
    _p(
        event="d3a_agpl_probe_gate",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("probe_skipped_agpl_disabled", "probe_run"),
        attrs=("allow_agpl_fallback",),
    ),
    _p(
        event="d3a_pre_garble_probe",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=(
            "pre_garbled_garble_detected",
            "pre_garbled_sparse_text",
            "pre_garbled_from_preclassify",
            "clean_not_pre_garbled",
            "probe_error",
        ),
        attrs=(
            "page0_text_chars",
            "page_count",
            "d3a_sparse_page_char_floor",
            "error_type",
            "text_layer_chars",
            "alpha_ratio",
            "junk_ratio",
        ),
        note=(
            "The bare `except Exception: pass` at 547-548 currently erases a "
            "probe crash; `probe_error` records it. Pure addition -- do not "
            "change the except's control flow (R12.12)."
        ),
    ),
    _p(
        event="force_full_page_ocr_decision",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("forced_by_inspector", "forced_by_pre_garble", "not_forced"),
        attrs=("inspector_force_ocr", "pre_garbled", "pre_garble_force_ocr_enabled"),
        note=(
            "RFC-044 D5 authority inversion: fires BEFORE decide_ocr_strategy "
            "is consulted. Do not add a decide_ocr_strategy call here -- that "
            "would break the single-call-site guard."
        ),
    ),
    _p(
        event="pdf_route_remote_or_local",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("remote_docling", "local_converter_chain"),
        attrs=("docling_service_url_set", "staging_key_set"),
        note="Booleans only -- never the URL or the staging key.",
    ),
    _p(
        event="pdf_converter_dispatch_mode",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("remote_supports_ocr", "remote_plain", "local_force_full_page", "local_normal"),
        attrs=("converter_name", "supports_ocr", "force_full_page"),
        cap=4,
        note="Once per converter-chain entry attempted.",
    ),
    _p(
        event="converter_failure_policy",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=(
            "retry",
            "block_agpl",
            "gate_agpl_structural_blocked",
            "gate_agpl_structural_walk",
            "reject",
            "walk",
        ),
        attrs=(
            "converter_name",
            "exception_type",
            "is_transient",
            "transient_attempts",
            "converter_transient_retry_count",
            "next_is_agpl",
            "agpl_structural_fallback_enabled",
        ),
        cap=6,
        always_emits=False,
        note=(
            "Hard Rule 4 AGPL decision. Already at INFO as a plain logger.info "
            "(707-717) -- migrate. exception_type is the CLASS name only."
        ),
    ),
    _p(
        event="pdf_conversion_outcome",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("converter_succeeded", "all_converters_failed_legacy_fallback"),
        attrs=(
            "used_converter",
            "primary_converter",
            "fallback_from_primary",
            "fallback_converter_is_agpl",
        ),
    ),
    _p(
        event="pdf_picture_splice_or_strip",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("spliced_into_tree", "stripped_residual_markers", "no_action"),
        attrs=("pic_result_count", "splice_enabled"),
        note="Counts only -- never a PictureResult's ocr_text.",
    ),
    _p(
        event="pdf_remote_bidi_renorm_gate",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("renorm_attempted", "skipped_local_or_disabled"),
        attrs=("use_remote", "remote_md_renormalize"),
    ),
    _p(
        event="bidi_renorm_bilingual_guard",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_renormalize_bidi_guarded",
        choices=("skipped_bilingual_guard", "applied"),
        attrs=("latin_frac", "bidi_renorm_latin_guard", "bidi_norm_version"),
        always_emits=False,
        note="RFC-034 D17. Migrate the existing skip-only logger.info at 164.",
    ),
    _p(
        event="bidi_presentation_form_canonicalization",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_renormalize_bidi_guarded",
        choices=("nfkc_applied", "not_needed"),
        attrs=("had_presentation_forms", "pf_any"),
        always_emits=False,
        note="Boolean only -- never the scanned text (R12.7).",
    ),
    _p(
        event="docx_pptx_conversion_route",
        phase=Phase.CONVERT,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("libreoffice_page_index_success", "markdown_fallback"),
        attrs=("exception_type",),
        always_emits=False,
        note="Non-PDF path only. exception_type is the class name; never the message.",
    ),
    _p(
        event="standalone_image_tessdata_availability",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("tessdata_available", "degraded_to_deu_eng"),
        attrs=("detected_langs_count", "degraded_langs"),
        always_emits=False,
        note="Standalone-image path only.",
    ),
    _p(
        event="standalone_image_ocr_source_choice",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=("full_tesseract_retry", "reuse_image_to_markdown_text"),
        attrs=("md_content_chars", "min_standalone_image_md_chars"),
        always_emits=False,
        note="*_chars only, never the text.",
    ),
    _p(
        event="d4_corrective_retry",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=(
            "skip_langs_match",
            "skip_tessdata_unavailable",
            "skip_same_langs",
            "filename_derived",
            "corrective_retry",
        ),
        attrs=(
            "source_langs",
            "content_langs",
            "corrective_langs",
            "langs",
            "winner_index",
            "original_garbled",
            "corrective_garbled",
        ),
        always_emits=False,
        note="RFC-046 D4 task 6.2: bounded detect-correct-retry for image OCR.",
    ),
    _p(
        event="script_context_pf_carryover",
        phase=Phase.LANGUAGE_SELECT,
        module=_C_INDEXER,
        function="index",
        choices=("pf_signal_carried_over", "no_carryover_needed"),
        attrs=("had_presentation_forms_pre_nfkc", "had_presentation_forms_post_nfkc"),
        note=(
            "Sets expected_script for every downstream gate/recovery call; NFKC "
            "destroys the codepoints so the pre-value must be carried."
        ),
    ),
    _p(
        event="recovery_method_dispatch",
        phase=Phase.RECOVERY,
        module=_C_INDEXER,
        function="index",
        choices=(
            "recover_garble_ocr",
            "recover_vlm_fallback",
            "recover_low_content_ocr",
            "recover_image_dominant_ocr",
            "recover_rtl_repair",
            "recover_rtl_flat_compare",
            "recover_flat_prefer",
            "recover_landscape_reroute",
            "no_gate_eligible",
        ),
        attrs=("gate_defect", "already_fired_skip"),
        cap=8,
        note=(
            "THE recovery-rung selection. The per-gate WHY lives in the "
            "*_recovery_eligible points in gates.py; this records only which "
            "method actually ran."
        ),
    ),
    _p(
        event="flat_md_source_resolution",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_persist_flat_result",
        choices=(
            "from_state_md_content",
            "from_tmp_md_path",
            "from_file_direct_read",
            "unavailable",
        ),
        attrs=("md_chars",),
        always_emits=False,
        note="`unavailable` explains a reject with no flat markdown at all.",
    ),
    _p(
        event="flat_block_garble_gate",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_persist_flat_result",
        choices=("garbled_reject", "not_garbled_pass"),
        attrs=("fired_prongs", "fired_prongs_count"),
        always_emits=False,
        note=(
            "HAZARD: indexer.py:1045-1050 sets _guard_bypass.active directly. "
            "Emit strictly AFTER line 1050 (R12.8). Prong labels only, never "
            "block text."
        ),
    ),
    _p(
        event="flat_vlm_fallback_outcome",
        phase=Phase.RECOVERY,
        module=_C_INDEXER,
        function="_persist_flat_result",
        choices=(
            "not_attempted",
            "vlm_recovered",
            "vlm_still_garbled",
            "vlm_compliance_blocked",
            "vlm_error",
        ),
        attrs=("exception_type",),
        always_emits=False,
        note=(
            "Hard Rule 3 (ZDR/PII) decision point. Emit after the try/except "
            "completes; never log the VLM markdown or str(exc)."
        ),
    ),
    _p(
        event="post_enrichment_garble_check",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_persist_flat_result",
        choices=("blocks_stripped", "enriched_blocks_clean"),
        attrs=("checked_count", "stripped_count", "retained_count", "fired_prongs"),
        always_emits=False,
        note=(
            "D3 (RFC-047): per-block garble check on enriched"
            " image blocks; garbled blocks have ocr_text cleared."
        ),
    ),
    _p(
        event="verdict_downgrade_override_flat",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_persist_flat_result",
        choices=("force_verdict_override_enabled", "normal_cas"),
        attrs=("verdict_downgrade_enabled", "pipeline_version"),
        always_emits=False,
        note="Bypasses the max-priority-wins verdict CAS guard.",
    ),
    _p(
        event="verdict_downgrade_override_tree",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="_persist_tree_result",
        choices=("force_verdict_override_enabled", "normal_cas"),
        attrs=("verdict_downgrade_enabled", "pipeline_version"),
        always_emits=False,
        note="Tree-path mirror of the flat point above.",
    ),
    _p(
        event="flat_garble_unrecovered_reject",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="index",
        choices=("reject_flat_garble_unrecovered", "proceed_to_route_dispatch"),
        attrs=("flat_garble_unrecovered",),
        note="Independent reject trigger that pre-empts the (ok, route) match.",
    ),
    _p(
        event="persistence_route_dispatch",
        phase=Phase.PERSIST,
        module=_C_INDEXER,
        function="index",
        choices=(
            "tree_success",
            "flat_persist_success",
            "flat_persist_reject",
            "reject_low_quality",
            "persist_tree_with_fail_verdict",
            "unexpected_fallback_persist_tree",
        ),
        attrs=("ok", "route", "first_defect"),
        note=(
            "THE central persistence decision. Use state.first_defect.value (a "
            "TreeDefect member, always content-free) rather than state.reason, "
            "which is produced by TreeGateResult.__str__ out of this file's "
            "control."
        ),
    ),
    _p(
        event="surya_density_fallback",
        phase=Phase.RECOVERY,
        module=_C_INDEXER,
        function="_persist_flat_doc",
        choices=(
            "recovery_succeeded",
            "recovery_insufficient",
            "recovery_failed",
            "not_attempted",
        ),
        attrs=(
            "original_cpp",
            "surya_cpp",
            "arabic_floor",
            "surya_confidence",
            "surya_duration_s",
            "reason",
        ),
        always_emits=False,
        note="RFC-047 D8: Surya OCR re-extraction when suspect_density fires on Arabic doc.",
    ),
    _p(
        event="surya_image_fallback",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=(
            "gate_not_triggered",
            "not_attempted",
            "recovery_succeeded",
            "recovery_insufficient",
            "recovery_failed",
        ),
        attrs=(
            "tesseract_chars",
            "surya_chars",
            "tesseract_garbled",
            "surya_garbled",
            "winner",
            "surya_confidence",
            "surya_duration_s",
        ),
        always_emits=False,
        note="RFC-048: Surya OCR fallback for standalone images when Tesseract output is poor.",
    ),
    _p(
        event="post_validation_image_fallback",
        phase=Phase.OCR,
        module=_C_INDEXER,
        function="_convert_to_tree",
        choices=(
            "none",
            "vlm",
            "surya",
        ),
        attrs=(
            "trigger_defects",
            "vlm_chars",
            "vlm_garbled",
            "surya_chars",
            "surya_garbled",
            "surya_confidence",
            "winner",
        ),
        always_emits=False,
        note=(
            "RFC-048 Amendment: parallel VLM+Surya after validate_tree condemns a standalone image."
        ),
    ),
)


# ---------------------------------------------------------------------------
# client/recovery.py -- the recovery cascade and its four force_route sites
# ---------------------------------------------------------------------------
_RECOVERY_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="pre_rebuild_md_quality",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_execute_ocr_retry",
        choices=("md_clean", "md_garbled", "md_empty"),
        attrs=("md_char_count", "md_garbled", "fired_prongs"),
        always_emits=False,
        note=(
            "D7 (RFC-046 task 5.1): garble-checks recovered"
            " markdown before the expensive tree rebuild."
        ),
    ),
    _p(
        event="ocr_retry_lang_degrade",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_execute_ocr_retry",
        choices=("tessdata_available", "degraded_to_deu_eng"),
        attrs=("escalation_langs_count",),
        always_emits=False,
    ),
    _p(
        event="ocr_retry_dispatch_route",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_execute_ocr_retry",
        choices=("remote_docling", "local_docling", "image_tesseract"),
        attrs=("use_remote", "ext"),
        always_emits=False,
    ),
    _p(
        event="ocr_retry_keep_best",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_keep_best_wins",
        choices=(
            "zero_char_shortcut_retry_wins",
            "char_count_regression_revert",
            "clean_md_overrides_char_regression",
            "equal_count_tiebreak_retry_wins",
            "equal_count_tiebreak_revert",
            "density_improved_retry_wins",
            "post_not_garbled_retry_wins",
            "density_not_improved_revert",
        ),
        attrs=(
            "pre_total_chars",
            "post_retry_chars",
            "pre_garbled",
            "post_garbled",
            "pre_density",
            "post_density",
        ),
        overridden=True,
        always_emits=False,
        note=(
            "R12.9: reuse the locals the cascade already computed; do NOT add "
            "an isEnabledFor-gated recomputation of the density scans."
        ),
    ),
    _p(
        event="ocr_retry_keep_best_apply",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_execute_ocr_retry",
        choices=("retry_kept", "reverted_to_pre_retry"),
        attrs=("use_keep_best", "reason_label", "post_retry_chars", "pre_total_chars"),
        overridden=True,
        always_emits=False,
        note=(
            "The call-site half of the override: pre_retry.apply(state) "
            "discards an OCR re-extraction that finalize_gate_and_route had "
            "already finalised. R12.5 -- record BOTH the computed post-OCR "
            "outcome and the fact it was discarded."
        ),
    ),
    _p(
        event="ocr_retry_escalation_outcome",
        phase=Phase.OCR,
        module=_C_RECOVERY,
        function="_execute_ocr_retry",
        choices=("recovered", "still_failed", "error"),
        attrs=("metric_fail_label", "reason_label", "exception_type"),
        always_emits=False,
    ),
    _p(
        event="low_content_ocr_eligibility",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_low_content_ocr",
        choices=("escalate_below_floor", "skip_sufficient_content"),
        attrs=("total_chars", "low_content_ocr_char_floor"),
        always_emits=False,
    ),
    _p(
        event="image_dominant_ocr_eligibility",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_image_dominant_ocr",
        choices=("escalate_image_dominant", "skip_insufficient_image_ratio"),
        attrs=("image_lines", "non_empty_lines_count", "image_line_ratio"),
        always_emits=False,
    ),
    _p(
        event="rtl_repair_double_correction_guard",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_rtl_repair",
        choices=("skip_already_bidi_renormalized", "proceed_per_node_repair"),
        attrs=("bidi_renorm_applied",),
        always_emits=False,
    ),
    _p(
        event="rtl_repair_convergence",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_rtl_repair",
        choices=("converged", "did_not_converge"),
        attrs=("bidi_norm_version",),
        always_emits=False,
        note="Never log the repaired node text; state.ok is a bool.",
    ),
    _p(
        event="rtl_flat_compare_override",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_rtl_flat_compare",
        choices=("override_to_flat_tree_still_reversed", "no_override_kept_tree"),
        attrs=("computed_route", "final_route", "flat_reversed", "tree_reversed"),
        overridden=True,
        always_emits=False,
        note=(
            "force_route site 1 of 4 in recovery.py. Capture state.route into a "
            "local BEFORE calling finalize_gate_and_route -- afterwards the "
            "computed value is gone (R12.5)."
        ),
    ),
    _p(
        event="vlm_fallback_eligibility",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_vlm_fallback",
        choices=("skip_full_page_ocr_already_resolved_garble", "proceed_to_vlm"),
        attrs=("full_page_already_applied", "first_defect"),
        always_emits=False,
        note="RFC-045 guard: stops VLM overwriting good OCR content.",
    ),
    _p(
        event="vlm_fallback_outcome",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_vlm_fallback",
        choices=(
            "vlm_recovered",
            "vlm_still_garbled",
            "vlm_compliance_blocked",
            "vlm_error",
        ),
        attrs=("exception_type",),
        always_emits=False,
        note="Hard Rule 3. Never log VLM markdown or str(exc).",
    ),
    _p(
        event="vlm_tesseract_raster_attempt_gate",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_vlm_fallback",
        choices=("attempt_raster_recovery", "skip"),
        attrs=(
            "vlm_tesseract_fallback_enabled",
            "d7_garble_recovery_enabled",
            "first_defect",
        ),
        always_emits=False,
    ),
    _p(
        event="vlm_tesseract_raster_override",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_vlm_fallback",
        choices=("override_to_flat_raster_recovered", "no_recovery_no_override"),
        attrs=("computed_route", "final_route", "recovered_md_chars"),
        overridden=True,
        always_emits=False,
        note="force_route site 2 of 4. Same both-values requirement.",
    ),
    _p(
        event="flat_prefer_script_multiplier_selection",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_flat_prefer",
        choices=("arabic_multiplier", "default_multiplier"),
        attrs=("expected_script", "multiplier"),
        always_emits=False,
    ),
    _p(
        event="flat_prefer_density_override",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_flat_prefer",
        choices=("override_to_flat_density_win", "no_override_kept_tree"),
        attrs=(
            "computed_route",
            "final_route",
            "flat_char_count",
            "tree_char_count",
            "multiplier",
        ),
        overridden=True,
        always_emits=False,
        note="force_route + force_ok=False site 3 of 4.",
    ),
    _p(
        event="landscape_reroute_override",
        phase=Phase.RECOVERY,
        module=_C_RECOVERY,
        function="_recover_landscape_reroute",
        choices=("override_to_flat_landscape_fallback", "no_override", "skip_tree_already_passed"),
        attrs=("computed_route", "final_route", "pic_results_count"),
        overridden=True,
        always_emits=False,
        note="force_route + force_ok=False site 4 of 4.",
    ),
)


# ---------------------------------------------------------------------------
# converters/pictures.py -- picture/OCR plane.
# NOTE: hot-path config guard covers this file: read pipeline_config.* only.
# NOTE: decide_ocr_strategy's SINGLE permitted call site in src/ lives here.
# ---------------------------------------------------------------------------
_ARBITRATE_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="arbitrate_candidates",
        phase=Phase.OCR,
        module=_H_ARBITRATE,
        function="arbitrate",
        choices=("pre_retry", "post_retry", "corrective_retry"),
        attrs=("candidate_count", "winner_index", "winner_chars", "winner_garbled"),
        always_emits=False,
        note="D7 (RFC-046 task 5.3): unified N-candidate script-aware arbitration.",
    ),
)

_PICTURES_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="vlm_egress_gate",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="zdr_egress_gate",
        choices=("allowed", "blocked"),
        attrs=("purpose", "api_base_host", "pii_corpus"),
        note=(
            "Hard Rule 3 gate. Reduce api_base to its HOST (or a boolean "
            "on_allowlist) -- never the full URL, never a key."
        ),
    ),
    _p(
        event="doc_text_layer_fallback",
        phase=Phase.CONVERT,
        module=_X_PICTURES,
        function="_document_level_text_fallback",
        choices=(
            "not_needed",
            "pdfium_read_failed",
            "empty_text",
            "garbled_skip",
            "fired",
        ),
        attrs=(
            "total_chars",
            "heading_count",
            "chars_per_heading",
            "min_chars_threshold",
            "min_chars_per_heading_threshold",
            "fired_prongs",
        ),
        overridden=True,
        note=(
            "The garble veto can revert a 'would fire' outcome to 'md "
            "unchanged' -- R12.5 needs the trigger's intended outcome AND the "
            "veto. Never log full_text or md."
        ),
    ),
    _p(
        event="pdf_rotation_normalization",
        phase=Phase.CONVERT,
        module=_X_PICTURES,
        function="_normalize_pdf_page_rotation",
        choices=(
            "disabled_by_flag",
            "agpl_fallback_disabled",
            "no_change_needed",
            "corrected",
            "failed",
        ),
        attrs=("pages_corrected_count", "allow_agpl_fallback", "error_type"),
        note="RFC-026 D2. Never log the temp PDF's absolute path -- basename only.",
    ),
    _p(
        event="landscape_probe_outcome",
        phase=Phase.CONVERT,
        module=_X_PICTURES,
        function="_tag_landscape_pages_for_fallback",
        choices=("agpl_fallback_disabled", "probe_failed", "completed"),
        attrs=("landscape_page_count", "total_page_count", "error_type"),
    ),
    _p(
        event="landscape_page_flagged_for_reextract",
        phase=Phase.CONVERT,
        module=_X_PICTURES,
        function="_landscape_pages_below_threshold",
        choices=(
            "flagged",
            "not_landscape",
            "no_picture_region",
            "above_threshold",
            "char_count_probe_failed",
            "no_landscape_pages_at_all",
        ),
        attrs=(
            "page_no",
            "char_count",
            "landscape_char_threshold",
            "is_landscape",
            "has_picture_region",
        ),
        level="DEBUG",
        cap=40,
        always_emits=False,
        note=(
            "Per page -- DEBUG + capped (R12.6 per-item exception). The "
            "aggregate lives in landscape_phase2_trigger at INFO."
        ),
    ),
    _p(
        event="landscape_reextract_bail",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_landscape_rasterize_rotate_reextract",
        choices=("continue", "bail_cap_reached", "bail_deadline_reached"),
        attrs=("pages_recovered_so_far", "max_landscape_pages", "deadline_seconds"),
        always_emits=False,
        note="RFC-036 D0a: remaining flagged pages silently fall through when this fires.",
    ),
    _p(
        event="landscape_reextract_engine",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_landscape_rasterize_rotate_reextract",
        choices=(
            "docling_recovered",
            "tesseract_fallback_recovered",
            "rasterize_failed",
            "reextract_failed",
            "empty_dropped",
        ),
        attrs=("page_no", "has_pictures", "ocr_langs", "md_chars"),
        cap=20,
        always_emits=False,
        note="RFC-046 D2, 'OCR site 5 of 5' -- implicated in the Doc 17 failure.",
    ),
    _p(
        event="picture_recovery_agpl_gate",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_text",
        choices=("allowed", "blocked_agpl_disabled"),
        attrs=("region_count",),
        note="When blocked, no picture region is cropped or OCR'd at all.",
    ),
    _p(
        event="picture_region_disposition",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_text",
        choices=(
            "skip_page_coverage",
            "skip_coverage_cap",
            "skip_clip_exported",
            "skip_clip_text",
            "skip_decorative",
            "capture_clip_text",
            "crop_and_ocr",
            "crop_error",
            "region_exception",
        ),
        attrs=(
            "region_index",
            "page",
            "coverage",
            "rect_width",
            "rect_height",
            "clip_text_len",
            "clip_contained",
            "fullpage_ocr_region_count",
            "retains_crop",
            "skip_reason",
            "error_type",
        ),
        level="DEBUG",
        cap=60,
        always_emits=False,
        note=(
            "Per-region route -- the highest-value picture decision, but "
            "high-cardinality on a figure-heavy document, so DEBUG + capped per "
            "R12.6's per-item exception. `retains_crop` (D5a RFC-029) is folded "
            "in here as an attr rather than a separate event."
        ),
    ),
    _p(
        event="picture_coverage_exemption",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_text",
        choices=("exempt", "not_exempt"),
        attrs=("page", "coverage_pct", "fullpage_ocr_region_count_after"),
        level="DEBUG",
        cap=20,
        always_emits=False,
        note=(
            "An early exemption increments the counter that caps a LATER region "
            "in the same document -- record the post-increment value."
        ),
    ),
    _p(
        event="picture_ocr_phase2_skipped",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_text",
        choices=("ran", "skipped_no_crops"),
        attrs=("region_count", "clip_capture_count", "retained_skip_count"),
        always_emits=False,
        note="Explains zero Tesseract invocations on a document that had regions.",
    ),
    _p(
        event="picture_ocr_min_chars_outcome",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_text",
        choices=("content", "decorative_ocr_min_chars"),
        attrs=(
            "region_index",
            "ocr_text_len",
            "vlm_describe_images_enabled",
            "png_kept",
        ),
        level="DEBUG",
        cap=60,
        always_emits=False,
        note="*_len only, never the OCR text.",
    ),
    _p(
        event="per_picture_ocr_strategy",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_results",
        choices=("none", "per_picture", "full_page"),
        attrs=(
            "ocr_escalation_enabled",
            "has_image_markers",
            "force_full_page_ocr_applied",
            "document_type",
        ),
        always_emits=False,
        note=(
            "Read the OcrDecision the EXISTING decide_ocr_strategy call "
            "(pictures.py:1075) already returned. Adding a second call site "
            "breaks tests/test_architecture_guards.py:1089-1114. `full_page` is "
            "listed because OcrMode has it, even though this site cannot "
            "currently reach it -- log the mode as returned."
        ),
    ),
    _p(
        event="picture_ocr_lang_tessdata_degrade",
        phase=Phase.LANGUAGE_SELECT,
        module=_X_PICTURES,
        function="_recover_picture_results",
        choices=("detected_langs", "degraded_default"),
        attrs=("lang_sources", "resolved_langs"),
        always_emits=False,
        note="Language CODES are content-free identifiers, safe verbatim.",
    ),
    _p(
        event="per_picture_recovery_aborted",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_recover_picture_results",
        choices=("completed", "aborted_exception"),
        attrs=("region_count", "error_type"),
        always_emits=False,
        note=(
            "The document silently continues with zero figures today. The "
            "adjacent WARNING logs str(exc); the new record must use "
            "type(exc).__name__ and must not touch that existing log (R12.12)."
        ),
    ),
    _p(
        event="vlm_description_outcome",
        phase=Phase.OCR,
        module=_X_PICTURES,
        function="_add_vlm_descriptions._describe_one",
        choices=("success", "failed_after_retry"),
        attrs=("attempt_count", "error_type"),
        level="DEBUG",
        cap=60,
        always_emits=False,
        note=(
            "Runs inside a ThreadPoolExecutor -- wrap the worker with "
            "obs.propagate() or the record loses its correlation fields. Never "
            "log the description text."
        ),
    ),
    _p(
        event="figure_marker_disposition",
        phase=Phase.CONVERT,
        module=_X_PICTURES,
        function="splice_figure_markers._repl",
        choices=(
            "excess_stripped",
            "excess_neutral",
            "skipped_stripped",
            "skipped_neutral",
            "figure_with_desc_and_ocr",
            "figure_with_desc_only",
            "figure_with_ocr_only",
            "figure_marker_only",
        ),
        attrs=(
            "picture_index",
            "has_ocr",
            "has_desc",
            "has_png",
            "strip_skipped_image_markers",
        ),
        level="DEBUG",
        cap=60,
        always_emits=False,
        note=(
            "Fires once per `<!-- image -->` marker from a re.sub callback -- "
            "DEBUG + capped deliberately, not by inertia. Never log ocr text, "
            "description content, or titles."
        ),
    ),
)


# ---------------------------------------------------------------------------
# converters/preclassify.py -- text-layer language detection (RFC-046 D4)
# ---------------------------------------------------------------------------
_PRECLASSIFY_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="preclassify_text_layer",
        phase=Phase.LANGUAGE_SELECT,
        module=_X_PRECLASSIFY,
        function="detect_lang_from_text_layer",
        choices=("detected", "garbled"),
        attrs=(
            "detected_langs",
            "text_layer_chars",
            "arabic_ratio",
            "has_german_markers",
            "alpha_ratio",
            "junk_ratio",
        ),
        note="RFC-046 D4. Text-layer language detection using pypdfium2, before any OCR.",
    ),
    _p(
        event="preclassify_lang_merge",
        phase=Phase.LANGUAGE_SELECT,
        module=_X_PRECLASSIFY,
        function="merge_lang_sources",
        choices=("filename_only", "reclassified", "confirmed"),
        attrs=(
            "fname_derived_langs",
            "text_layer_langs",
            "merged_langs",
            "reclassified",
        ),
        note="RFC-046 D4. Merges filename + text-layer language detection.",
    ),
    _p(
        event="preclassify_document",
        phase=Phase.LANGUAGE_SELECT,
        module=_X_PRECLASSIFY,
        function="preclassify_document",
        choices=(
            "non_pdf",
            "pdf_text_layer",
            "pdf_filename",
            "pdf_garbled_text_layer",
            "pdf_error",
            "pdf_no_pypdfium2",
            "pdf_unavailable",
        ),
        attrs=(
            "file_type",
            "pdf_type",
            "pdf_confidence",
            "page_count",
            "detected_langs",
            "ocr_langs",
            "lang_source",
            "garbled_text_layer",
        ),
        note="RFC-046 D4. Unified pre-classification: doc type + lang + garble.",
    ),
)


# ---------------------------------------------------------------------------
# converters/pipeline.py -- converter chain construction + markdown source
# ---------------------------------------------------------------------------
_PIPELINE_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="converter_chain_composition",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_markdown_converters",
        choices=(
            "no_converters_available_raise",
            "docling_primary",
            "pymupdf4llm_primary_docling_secondary",
            "pymupdf4llm_only_docling_unavailable",
            "pymupdf4llm_only_docling_missing_requested",
        ),
        attrs=("configured_primary", "have_docling", "allow_agpl_fallback"),
        note=(
            "Hard Rule 4 adjacency: builds the ordered chain indexer.py walks. "
            "The walk/fallback POLICY lives in indexer.py "
            "(converter_failure_policy), not here."
        ),
    ),
    _p(
        event="docling_chunk_route",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("direct", "chunked"),
        attrs=("page_count", "effective_max_pages", "page_count_guard_failed"),
        note=(
            "RFC-027 D7. page_count_guard_failed must be present or a reader "
            "misreads a failed probe as a genuinely short document."
        ),
    ),
    _p(
        event="hierarchical_addon_status",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("applied", "not_installed", "postprocess_failed"),
        attrs=("patch_infer_applied", "error_type"),
        note="Decides whether headings come from the add-on or raw Docling heuristics.",
    ),
    _p(
        event="heading_repromotion_status",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("applied", "no_op", "failed"),
        attrs=("n_promoted", "error_type"),
    ),
    _p(
        event="docling_empty_output",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("has_content", "empty_raise"),
        attrs=("post_md_len",),
        note="*_len only. The raise is what triggers indexer.py's converter walk.",
    ),
    _p(
        event="markdown_source_selection",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("post_add_on", "raw_over_prune_rescue", "raw_verdict_better"),
        attrs=(
            "post_headings",
            "raw_headings",
            "post_has_depth",
            "raw_has_depth",
            "post_verdict",
            "raw_verdict",
            "post_max_heading_level",
        ),
        overridden=True,
        note=(
            "R12.12's highest-risk site. Do NOT reorder the if/elif, do NOT "
            "call classify_verdict again, and REUSE the "
            "_max_heading_level(post_candidate.md) value already computed for "
            "the warning at line 532 (R12.9)."
        ),
    ),
    _p(
        event="landscape_phase2_trigger",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="pdf_to_markdown_docling",
        choices=("triggered", "not_triggered"),
        attrs=("pages_below_threshold_count",),
        note="Top-level gate for the expensive rasterize path in pictures.py.",
    ),
    _p(
        event="extraction_stage_outcome",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="_run_stages",
        choices=("success", "failed"),
        attrs=("stage_name", "char_delta", "heading_delta", "error_type"),
        cap=8,
        note=(
            "Bounded stage count per document, so INFO is fine. error_type is "
            "type(exc).__name__; the existing WARNING at line 245 logging "
            "str(exc) stays untouched (R12.12)."
        ),
    ),
    _p(
        event="picture_marker_strip_on_empty_recovery",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="_fallback_and_recover_pictures",
        choices=("stripped", "kept"),
        attrs=("pic_results_count",),
    ),
    _p(
        event="landscape_fallback_marker_added",
        phase=Phase.CONVERT,
        module=_X_PIPELINE,
        function="_fallback_and_recover_pictures",
        choices=("marker_added", "not_added"),
        attrs=("page_no", "has_pictures"),
        level="DEBUG",
        cap=20,
        always_emits=False,
    ),
)


# ---------------------------------------------------------------------------
# worker/subprocess_mgr.py -- per-file ingest lock (parent side)
# ---------------------------------------------------------------------------
_WORKER_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="ingest_dedup_lock",
        phase=Phase.CONVERT,
        module=_W_SUBPROC,
        function="_run_converter_subprocess",
        choices=(
            "acquired",
            "acquired_after_wait",
            "wait_timeout_proceed_unlocked",
            "redis_unavailable_proceed_unlocked",
        ),
        attrs=("waited_ms",),
        note="RFC-050 D7 (HR2): per-file Redis lock held by the worker PARENT "
        "around the converter child, so the child's hash-cache dedup check and "
        "hash_cache_set run under it and a killed child cannot strand it. A "
        "waiter's child re-runs the dedup check, so a concurrent duplicate "
        "dedup-skips instead of minting an orphan doc_id. The two *_unlocked "
        "choices are fail-open degradations.",
    ),
)


# ---------------------------------------------------------------------------
# storage/documents.py -- quarantine persistence
# ---------------------------------------------------------------------------
_STORAGE_POINTS: tuple[DecisionPoint, ...] = (
    _p(
        event="quarantine_write",
        phase=Phase.PERSIST,
        module=_S_DOCUMENTS,
        function="save_quarantine",
        choices=("quarantine_saved", "quarantine_write_error"),
        attrs=("sha256", "payload_size", "meta_filenames_count"),
    ),
)


#: The registry. Ordered by pipeline module for readability; order is not
#: semantically meaningful.
DECISION_POINTS: tuple[DecisionPoint, ...] = (
    _TYPES_POINTS
    + _GATES_POINTS
    + _GARBLE_POINTS
    + _TREEVAL_POINTS
    + _VERDICT_POINTS
    + _OCRLANGS_POINTS
    + _INDEXER_POINTS
    + _RECOVERY_POINTS
    + _ARBITRATE_POINTS
    + _PICTURES_POINTS
    + _PRECLASSIFY_POINTS
    + _PIPELINE_POINTS
    + _STORAGE_POINTS
    + _WORKER_POINTS
)


def _build_index() -> dict[str, DecisionPoint]:
    index: dict[str, DecisionPoint] = {}
    for point in DECISION_POINTS:
        if point.event in index:  # pragma: no cover - registry integrity
            raise ValueError(f"duplicate decision event: {point.event}")
        index[point.event] = point
    return index


#: ``event`` -> :class:`DecisionPoint`. The lookup tasks 12.6 and 12.8 use.
DECISION_POINTS_BY_EVENT: dict[str, DecisionPoint] = _build_index()

#: Every registered event name.
DECISION_EVENTS: frozenset[str] = frozenset(DECISION_POINTS_BY_EVENT)

#: Dotted module paths that phase 3 is expected to instrument. Task 12.6's
#: AST scan walks exactly these files.
INSTRUMENTED_MODULES: tuple[str, ...] = tuple(
    dict.fromkeys(point.module for point in DECISION_POINTS)
)

#: R12.7 default-deny tripwire. No ``attrs`` key -- here or at any call site
#: -- may contain one of these substrings. Lengths, counts and hashes of the
#: same things are fine, which is why the list names the CONTENT nouns and
#: task 12.6 must exempt a key ending in ``_len``/``_chars``/``_count``/
#: ``_ratio``/``_sha8``.
FORBIDDEN_ATTR_SUBSTRINGS: tuple[str, ...] = (
    "md_content",
    "markdown",
    "content_text",
    "text_sample",
    "raw_text",
    "full_text",
    "flat_text",
    "primary_text",
    "promoted_text",
    "node_text",
    "ocr_text",
    "clip_text",
    "title",
    "heading_text",
    "summary",
    "snippet",
    "excerpt",
    "prompt",
    "completion",
    "description",
    "cell",
    "filename",
    "file_path",
    "path",
    "url",
    "api_key",
    "token",
    "exc_message",
    "error_message",
    "traceback",
    "sample",
)


#: Suffixes that make a key safe even though its stem names content: a
#: LENGTH, COUNT, RATIO or HASH of document text is not document text. R12.7
#: explicitly nominates ``*_len`` / ``*_chars`` / ratios / ``*_sha8``.
SAFE_ATTR_SUFFIXES: tuple[str, ...] = (
    "_len",
    "_chars",
    "_count",
    "_counts",
    "_ratio",
    "_pct",
    "_frac",
    "_fraction",
    "_sha8",
    "_threshold",
    "_index",
    "_level",
    "_no",
    "_seq",
    "_delta",
)

#: Keys whose stem trips a forbidden substring but which carry no content:
#: a list of promotion-path LABELS, and a boolean saying whether a service
#: URL is configured (never the URL itself).
SAFE_ATTR_EXCEPTIONS: frozenset[str] = frozenset({"matched_paths", "docling_service_url_set"})


def is_content_attr(key: str) -> bool:
    """True when ``key`` names document content and is therefore forbidden.

    The R12.7 default-deny test in task 12.6 calls this on every attrs key it
    finds at a ``decision()`` call site, not just on the registry.
    """
    if key in SAFE_ATTR_EXCEPTIONS:
        return False
    if key.endswith(SAFE_ATTR_SUFFIXES):
        return False
    lowered = key.lower()
    return any(bad in lowered for bad in FORBIDDEN_ATTR_SUBSTRINGS)


def content_attr_violations() -> tuple[tuple[str, str], ...]:
    """Every ``(event, attr)`` pair in the registry that names content.

    Must be empty. Exposed as a function rather than an import-time assert so
    that importing this module can never abort a document (R12.8 posture).
    """
    return tuple(
        (point.event, key)
        for point in DECISION_POINTS
        for key in point.attrs
        if is_content_attr(key)
    )


def point_for(event: str) -> DecisionPoint:
    """Look up a registered point, raising a clear error if it is unknown."""
    try:
        return DECISION_POINTS_BY_EVENT[event]
    except KeyError:
        raise KeyError(f"unregistered decision event: {event!r}") from None


def allowed_choices(event: str) -> frozenset[str]:
    return frozenset(point_for(event).choices)


def allowed_attrs(event: str) -> frozenset[str]:
    return frozenset(point_for(event).attrs)
