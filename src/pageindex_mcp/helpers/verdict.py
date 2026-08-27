"""Verdict computation: evaluate_gates → apply_promotions → compute_verdict."""

from __future__ import annotations

import logging
import re

from ..config import pipeline_config
from ..script import ScriptContext, decide_rtl
from .garble import (
    BULK_PROFILE,
    BlobKind,
    GarbleConfig,
    _garble_config,
    detect_garble,
    hash_pipe_ratio,
    ocr_noise_ratio,
)
from .gates import (
    _GATE_PRIORITY,
    GATE_TABLE,
    HARD_FAIL_DEFECTS,
)
from .tree_validation import TreeSignals, _tree_max_leaf_ratio, _tree_node_count
from .types import (
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    VerdictResult,
    VerdictThresholds,
)

_FLAT_CHART_TEXT_RE = re.compile(r"^>\s*\[Chart text\]:\s*(.+)$")


def compute_image_enrichment_ratio(image_blocks: list[dict]) -> float | None:
    """RFC-036 D4: excludes intentionally-skipped blocks from both the
    enriched numerator and the total denominator, so correctly-skipped
    picture regions never count as unenriched gaps toward
    classify_verdict's image_enrichment_promoted path.

    Uses ``SkipReason.counts_in_denominator`` policy when a typed
    SkipReason is available; falls back to the prior string-based
    exclusion for backward compatibility with blocks that carry raw
    ``skipped_reason`` strings."""
    from ..picture_plane import skip_reason_from_str

    scored_blocks: list[dict] = []
    for b in image_blocks:
        raw_reason = b.get("skipped_reason")
        if raw_reason:
            typed = skip_reason_from_str(raw_reason)
            if typed is not None and not typed.counts_in_denominator:
                continue
        scored_blocks.append(b)
    if not scored_blocks:
        return None
    enriched_count = sum(
        1
        for b in scored_blocks
        if b.get("ocr_text") or b.get("description") or b.get("figure_path")
    )
    return enriched_count / len(scored_blocks)


def _classify_image_verdict(image_enrichment_ratio: float | None) -> tuple[str, str]:
    """Verdict for image-standalone documents."""
    if image_enrichment_ratio is not None and image_enrichment_ratio >= 0.8:
        return "PASS", "image_enrichment_complete"
    if image_enrichment_ratio is not None and image_enrichment_ratio > 0:
        return "MARGINAL", f"image_enrichment_partial(ratio={image_enrichment_ratio:.2f})"
    return "FAIL", "no_image_enrichment"


def _dedupe_chart_text_lines(text: str) -> str:
    """RFC-027 D1: drop repeated '> [Chart text]:' lines before a promoted
    doc's char/garble calculations -- a single OCR read spliced into prose
    can otherwise be double-counted toward both the char floor and the
    garble check. Keeps the first occurrence of each distinct line. Pure.

    Note: table block content is included via _flatten_tree_text (Zone-5 fix).
    No change needed here -- this function operates on the flat_text string
    produced by _flatten_tree_text, which now automatically includes table
    content from headers/rows/row_records.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if _FLAT_CHART_TEXT_RE.match(line.strip()):
            if line in seen:
                continue
            seen.add(line)
        kept.append(line)
    return "".join(kept)


# Zone-3: _defect_from_reason_str moved to types.py next to finalize_gate_and_route.
# Re-exported here for backward compat with any callers importing from verdict.
from .types import _defect_from_reason_str as _defect_from_reason_str  # noqa: F811


def _clamp_pass(
    reason: str,
    *,
    defect: TreeDefect,
    sig: TreeSignals,
) -> tuple[str, str]:
    """Apply uniform caps to a PASS verdict (replaces ``_pass()`` closure).

    1. bidi_degraded -> MARGINAL (RFC-018 D2)
    2. depth-inadequacy -> MARGINAL when the tree is shallower than its
       node count warrants (RFC-036 D6).
    """
    if defect == TreeDefect.BIDI_DEGRADED:
        return "MARGINAL", "bidi_degraded"
    if sig.depth < sig.expected_min_depth and not sig.effectively_garbled:
        return (
            "MARGINAL",
            f"depth_inadequate:expected_min_depth={sig.expected_min_depth},actual_depth={sig.depth}",
        )
    return "PASS", reason


def evaluate_gates(
    structure: list,
    validate_result: TreeGateResult | None,
    expected_script: str | None | ScriptContext,
    th: VerdictThresholds,
) -> GateOutcome:
    """Zone-4 Phase 1: gate evaluation + hard-fail checks.

    Pure function that resolves ``validate_result`` into a typed
    :class:`GateOutcome`.  When a hard-fail or zero-content fast path
    fires, ``GateOutcome.hard_fail_verdict`` is non-None and the caller
    should return it directly (skipping Phase 2 promotions).

    Hard-fail tiebreak uses ``_GATE_PRIORITY`` / ``len(GATE_TABLE)``
    sentinel -- these are the same literal identifiers asserted by
    source-introspection tests in test_zone2/test_zone5.
    """
    if validate_result is not None and not isinstance(validate_result, TreeGateResult):
        raise TypeError(
            "compute_verdict(validate_result=...) expects a TreeGateResult or "
            f"None, got {type(validate_result).__name__!s}; the bare-string "
            "compat path was removed (Zone-1)."
        )
    if isinstance(validate_result, TreeGateResult):
        defect = validate_result.defect
        validate_reason: str | None = (
            str(validate_result) if validate_result.defect != TreeDefect.OK else None
        )
        sig = validate_result.signals
        _all_defects = validate_result.all_defects
    else:
        validate_reason = None
        defect = TreeDefect.OK
        sig = None
        _all_defects = frozenset[TreeDefect]()

    if isinstance(expected_script, ScriptContext):
        _bare_script: str | None = expected_script.dominant_script
    else:
        _bare_script = expected_script

    if sig is None:
        sig = TreeSignals.from_tree(
            structure, expected_script=expected_script, garble_threshold=th.garble_threshold
        )

    if sig.node_count == 0 or len(sig.flat_text.strip()) == 0:
        return GateOutcome(
            defect=defect,
            validate_reason=validate_reason,
            signals=sig,
            all_defects=_all_defects,
            hard_fail_verdict=VerdictResult(
                "FAIL", "zero_content", defect=defect, signals=sig, all_defects=_all_defects
            ),
        )

    if validate_result is None and sig.is_reordered:
        defect = TreeDefect.REORDERED
        _all_defects = frozenset({TreeDefect.REORDERED})

    if defect in HARD_FAIL_DEFECTS:
        return GateOutcome(
            defect=defect,
            validate_reason=validate_reason,
            signals=sig,
            all_defects=_all_defects,
            hard_fail_verdict=VerdictResult(
                "FAIL",
                validate_reason or defect.value,
                defect=defect,
                signals=sig,
                all_defects=_all_defects,
            ),
        )
    _masked = _all_defects & HARD_FAIL_DEFECTS
    if _masked:
        _worst = min(_masked, key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)))
        return GateOutcome(
            defect=defect,
            validate_reason=validate_reason,
            signals=sig,
            all_defects=_all_defects,
            hard_fail_verdict=VerdictResult(
                "FAIL",
                _worst.value,
                defect=defect,
                signals=sig,
                all_defects=_all_defects,
            ),
        )

    return GateOutcome(
        defect=defect,
        validate_reason=validate_reason,
        signals=sig,
        all_defects=_all_defects,
        hard_fail_verdict=None,
    )


def _try_image_enrichment(
    sig: TreeSignals,
    content_class: str,
    image_enrichment_ratio: float | None,
    th: VerdictThresholds,
    expected_script: str | None,
    script_context: ScriptContext | None,
) -> str | None:
    """Image-enrichment rescue path (RFC-022 B2, RFC-040 D1 guards).

    Returns the promotion reason string, or None if ineligible.
    D1 guards: node_count >= 3 and not effectively_garbled prevent
    structurally-empty or garbled docs from re-entering via image rescue.
    """
    if content_class not in ("flat_prose", "flat_mixed"):
        return None
    if image_enrichment_ratio is None or image_enrichment_ratio < 0.8:
        return None
    if sig.node_count < 3:
        return None
    if sig.effectively_garbled:
        return None
    _promoted_text = _dedupe_chart_text_lines(sig.primary_text)
    total_chars = len(_promoted_text)
    if total_chars < th.min_image_promoted_chars:
        return None
    _sc = (
        script_context
        if script_context is not None
        else ScriptContext(
            dominant_script=expected_script,
            had_presentation_forms=False,
            source="apply_promotions",
        )
    )
    if detect_garble(
        _promoted_text,
        script_context=_sc,
        config=_garble_config,
        blob_kind=BlobKind.TREE_TEXT,
    ):
        return None
    return "image_enrichment_promoted"


def _try_structural_pass(
    sig: TreeSignals,
    all_defects: frozenset[TreeDefect],
    th: VerdictThresholds,
) -> str | None:
    """Direct PASS when structural metrics are clean."""
    _structural_ok = {TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW}.isdisjoint(all_defects)
    _effective_max_leaf = th.pass_max_leaf_ratio
    if _structural_ok and sig.max_leaf_ratio < _effective_max_leaf and not sig.effectively_garbled:
        return ""
    return None


def _try_cat_a(
    sig: TreeSignals,
    content_class: str,
) -> str | None:
    """OCR category-A promotion."""
    if not content_class.startswith("ocr_"):
        return None
    if sig.max_leaf_ratio < 0.15 and ocr_noise_ratio(sig.flat_text) < 0.005:
        return "cat_a_promoted"
    return None


def _try_cat_b(
    sig: TreeSignals,
    content_class: str,
    th: VerdictThresholds,
) -> str | None:
    """Flat category-B promotion."""
    if not content_class.startswith("flat_"):
        return None
    _stripped_flat_text = sig.flat_text.strip()
    _text_blocks = [b for b in _stripped_flat_text.splitlines() if b.strip()]
    _placeholder_ratio = (
        sum(1 for b in _text_blocks if b.strip() == "<!-- image -->") / len(_text_blocks)
        if _text_blocks
        else 0.0
    )
    if (
        not sig.effectively_garbled
        and sig.max_leaf_ratio < th.cat_bc_promotion_threshold
        and sig.node_count >= 3
        and len(_stripped_flat_text) >= th.min_flat_promotion_chars
        and _placeholder_ratio <= 0.5
    ):
        return "cat_b_promoted"
    return None


def _try_cat_c(
    sig: TreeSignals,
    content_class: str,
    inspector_class: str | None,
    th: VerdictThresholds,
) -> str | None:
    """Generic category-C promotion."""
    if content_class.startswith("ocr_") or content_class.startswith("flat_"):
        return None
    _cat_c_threshold = th.cat_bc_promotion_threshold
    if not content_class and inspector_class == "text_based":
        _cat_c_threshold = th.cat_bc_promotion_threshold * 1.2
    if (
        not sig.effectively_garbled
        and hash_pipe_ratio(sig.flat_text) < 0.01
        and sig.max_leaf_ratio < _cat_c_threshold
    ):
        return "cat_c_promoted"
    return None


def _try_small_doc(
    sig: TreeSignals,
    content_class: str,
    th: VerdictThresholds,
) -> str | None:
    """Small document promotion."""
    if not th.small_doc_enabled:
        return None
    if not content_class.startswith("flat_"):
        return None
    _small_doc_leaf_ratio_bound = (
        th.small_doc_leaf_ratio_bound_high
        if sig.node_count <= 5
        else th.small_doc_leaf_ratio_bound_low
    )
    if (
        not sig.effectively_garbled
        and sig.node_count >= 1
        and sig.node_count <= 10
        and sig.max_leaf_ratio < _small_doc_leaf_ratio_bound
        and 100 <= len(sig.flat_text.strip()) < 15000
    ):
        return "small_doc_promoted"
    return None


# RFC-expected aliases — the zone fix specs name promotion paths by their
# domain semantics (_try_ocr_promotion, _try_flat_promotion, etc.) while the
# implementation uses shorter category labels (_try_cat_a, _try_cat_b, ...).
# These aliases make the RFC names callable in production and importable from
# the helpers package.
_try_ocr_promotion = _try_cat_a
_try_flat_promotion = _try_cat_b
_try_content_class_promotion = _try_cat_c
_try_small_doc_promotion = _try_small_doc


def apply_promotions(
    outcome: GateOutcome,
    content_class: str,
    image_enrichment_ratio: float | None,
    inspector_class: str | None,
    th: VerdictThresholds,
    expected_script: str | None,
    *,
    source_selection: bool = False,
    script_context: ScriptContext | None = None,
) -> VerdictResult:
    """Zone-4 Phase 2: promotion/cap logic (tried only when no HARD_FAIL fired).

    RFC-040 D1: structural hard-fail (max_leaf_ratio > threshold) is
    unconditional — evaluated BEFORE any promotion path.  Image-enrichment
    is a guarded EXCEPTION, not a bypass.

    RFC-040 D2: ordered if/elif pipeline replaces score-all-then-pick-best.
    Priority is expressed in source-code order; first match wins.
    """
    defect = outcome.defect
    sig = outcome.signals
    _all_defects = outcome.all_defects

    if content_class == "image_standalone":
        _iv, _ir = _classify_image_verdict(image_enrichment_ratio)
        return VerdictResult(_iv, _ir, defect=defect, signals=sig, all_defects=_all_defects)

    def _apply_clamp(reason: str) -> VerdictResult:
        if source_selection:
            return VerdictResult(
                "PASS", reason, defect=defect, signals=sig, all_defects=_all_defects
            )
        _v, _r = _clamp_pass(reason, defect=defect, sig=sig)
        return VerdictResult(_v, _r, defect=defect, signals=sig, all_defects=_all_defects)

    # D1: Unconditional structural hard-fail gate
    if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
        _ie = _try_image_enrichment(
            sig, content_class, image_enrichment_ratio, th, expected_script, script_context
        )
        if _ie is not None:
            return _apply_clamp(_ie)
        return VerdictResult(
            "FAIL",
            f"max_leaf_ratio={sig.max_leaf_ratio:.2f}",
            defect=defect,
            signals=sig,
            all_defects=_all_defects,
        )

    # D2: Ordered promotion pipeline — first match wins
    _ie = _try_image_enrichment(
        sig, content_class, image_enrichment_ratio, th, expected_script, script_context
    )
    if _ie is not None:
        return _apply_clamp(_ie)

    _sp = _try_structural_pass(sig, _all_defects, th)
    if _sp is not None:
        return _apply_clamp(_sp)

    _ca = _try_ocr_promotion(sig, content_class)
    if _ca is not None:
        return _apply_clamp(_ca)

    _cb = _try_flat_promotion(sig, content_class, th)
    if _cb is not None:
        return _apply_clamp(_cb)

    _cc = _try_content_class_promotion(sig, content_class, inspector_class, th)
    if _cc is not None:
        return _apply_clamp(_cc)

    _sd = _try_small_doc_promotion(sig, content_class, th)
    if _sd is not None:
        return _apply_clamp(_sd)

    # Fallback: no promotion path fired → MARGINAL
    if sig.effectively_garbled:
        reason = f"garbling(ratio={sig.garble_ratio:.2f})"
    elif sig.node_count < 3:
        reason = f"node_count={sig.node_count}"
    elif sig.depth < 2:
        reason = f"depth={sig.depth}"
    else:
        reason = f"leaf_concentration={sig.max_leaf_ratio:.2f}"
    return VerdictResult("MARGINAL", reason, defect=defect, signals=sig, all_defects=_all_defects)


def compute_verdict(
    structure: list,
    content_class: str,
    validate_result: TreeGateResult | None = None,
    image_enrichment_ratio: float | None = None,
    inspector_class: str | None = None,
    expected_script: str | None | ScriptContext = None,
    *,
    source_selection: bool = False,
) -> VerdictResult:
    """Zone-4 thin dispatcher: evaluate_gates -> apply_promotions.

    Signature and return type unchanged from the Zone-2 monolith.
    Returns a :class:`VerdictResult` (iterable as ``(verdict, reason)``
    for backward-compat tuple unpacking).

    Thresholds are sourced from ``VerdictThresholds.from_config(pipeline_config)``
    (Zone-5 contract).  The ``PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO``
    coupling assertion lives in ``config.py`` and is not duplicated here.

    Source-text contracts (asserted by test_zone2/test_zone5 introspection):
      - Hard-fail tiebreak delegates to evaluate_gates which uses
        ``_GATE_PRIORITY.get(d, len(GATE_TABLE))`` for masked co-firing
        defect resolution via ``_GATE_PRIORITY`` dict lookups.
    """
    th = VerdictThresholds.from_config(pipeline_config)
    if isinstance(expected_script, ScriptContext):
        _bare_script: str | None = expected_script.dominant_script
    else:
        _bare_script = expected_script
    outcome = evaluate_gates(structure, validate_result, expected_script, th)
    if outcome.hard_fail_verdict is not None:
        return outcome.hard_fail_verdict
    _sc = expected_script if isinstance(expected_script, ScriptContext) else None
    return apply_promotions(
        outcome,
        content_class,
        image_enrichment_ratio,
        inspector_class,
        th,
        _bare_script,
        source_selection=source_selection,
        script_context=_sc,
    )


def classify_verdict(
    structure: list,
    content_class: str,
    validate_result: TreeGateResult | None,
    image_enrichment_ratio: float | None = None,
    inspector_class: str | None = None,
    expected_script: str | None = None,
) -> tuple[str, str]:
    """Thin backward-compat wrapper around :func:`compute_verdict`.

    Returns a plain ``(verdict, reason)`` tuple so that existing call
    sites (tests, external scripts) continue working without changes.
    """
    _vr = compute_verdict(
        structure,
        content_class,
        validate_result,
        image_enrichment_ratio=image_enrichment_ratio,
        inspector_class=inspector_class,
        expected_script=expected_script,
    )
    return _vr.verdict, _vr.reason


def detect_regression(
    structure: list,
    prev_node_count: int | None,
    prev_max_leaf_ratio: float | None,
) -> bool:
    """Category E regression gate (RFC-014 D4, Property 6).

    Returns True when BOTH conditions hold vs. the last stored verdict:
      - node_count dropped >30%
      - max_leaf_ratio grew >2x
    """
    if prev_node_count is None or prev_max_leaf_ratio is None:
        return False
    if prev_node_count == 0:
        return False
    cur_count = _tree_node_count(structure)
    _, _, cur_ratio = _tree_max_leaf_ratio(structure)
    count_dropped = cur_count < prev_node_count * 0.7
    ratio_grew = prev_max_leaf_ratio > 0 and cur_ratio > prev_max_leaf_ratio * 2
    return count_dropped and ratio_grew
