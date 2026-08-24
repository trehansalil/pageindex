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
    garble check. Keeps the first occurrence of each distinct line. Pure."""
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


def apply_promotions(
    outcome: GateOutcome,
    content_class: str,
    image_enrichment_ratio: float | None,
    inspector_class: str | None,
    th: VerdictThresholds,
    expected_script: str | None,
    *,
    source_selection: bool = False,
) -> VerdictResult:
    """Zone-4 Phase 2: promotion/cap logic (tried only when no HARD_FAIL fired).

    The image-enrichment rescue is intentionally positioned before the
    max_leaf_ratio structural hard-fail: flat image-enriched documents
    render as a single leaf (max_leaf_ratio=1.0), so the structural
    metric is not meaningful for them.  This ordering is locked by
    RFC-022 B2.

    Thresholds are sourced from *th* (:class:`VerdictThresholds` built
    via ``VerdictThresholds.from_config(pipeline_config)``).  The
    ``PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO`` coupling assertion is
    owned by ``PipelineConfig`` in ``config.py`` and is NOT duplicated
    here.
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

    _structural_ok = {TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW}.isdisjoint(_all_defects)

    if (
        content_class in ("flat_prose", "flat_mixed")
        and image_enrichment_ratio is not None
        and image_enrichment_ratio >= 0.8
    ):
        _promoted_text = _dedupe_chart_text_lines(sig.primary_text)
        total_chars = len(_promoted_text)
        if total_chars < th.min_image_promoted_chars:
            return VerdictResult(
                "MARGINAL",
                "image_enrichment_promoted_below_char_floor",
                defect=defect,
                signals=sig,
                all_defects=_all_defects,
            )
        _sc = ScriptContext(dominant_script=expected_script, had_presentation_forms=False, source="apply_promotions")
        if not detect_garble(_promoted_text, script_context=_sc, config=_garble_config, blob_kind=BlobKind.TREE_TEXT):
            return _apply_clamp("image_enrichment_promoted")

    if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
        return VerdictResult(
            "FAIL",
            f"max_leaf_ratio={sig.max_leaf_ratio:.2f}",
            defect=defect,
            signals=sig,
            all_defects=_all_defects,
        )

    _effective_max_leaf = th.pass_max_leaf_ratio
    if _structural_ok and sig.max_leaf_ratio < _effective_max_leaf and not sig.effectively_garbled:
        return _apply_clamp("")

    if content_class.startswith("ocr_"):
        if sig.max_leaf_ratio < 0.15 and ocr_noise_ratio(sig.flat_text) < 0.005:
            return _apply_clamp("cat_a_promoted")
    elif content_class.startswith("flat_"):
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
            return _apply_clamp("cat_b_promoted")
    else:
        _cat_c_threshold = th.cat_bc_promotion_threshold
        if not content_class and inspector_class == "text_based":
            _cat_c_threshold = th.cat_bc_promotion_threshold * 1.2
        if (
            not sig.effectively_garbled
            and hash_pipe_ratio(sig.flat_text) < 0.01
            and sig.max_leaf_ratio < _cat_c_threshold
        ):
            return _apply_clamp("cat_c_promoted")

    _small_doc_leaf_ratio_bound = (
        th.small_doc_leaf_ratio_bound_high
        if sig.node_count <= 5
        else th.small_doc_leaf_ratio_bound_low
    )
    if (
        th.small_doc_enabled
        and not sig.effectively_garbled
        and content_class.startswith("flat_")
        and sig.node_count >= 1
        and sig.node_count <= 10
        and sig.max_leaf_ratio < _small_doc_leaf_ratio_bound
        and 100 <= len(sig.flat_text.strip()) < 15000
    ):
        return _apply_clamp("small_doc_promoted")

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
    return apply_promotions(
        outcome,
        content_class,
        image_enrichment_ratio,
        inspector_class,
        th,
        _bare_script,
        source_selection=source_selection,
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


# ---------------------------------------------------------------------------
# Zone-4: verdict-ledger hysteresis (shared helper)
# ---------------------------------------------------------------------------

_LEDGER_PRIORITY: dict[str, int] = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}

_hysteresis_logger = logging.getLogger(__name__)


def apply_verdict_hysteresis(
    verdict: str,
    verdict_reason: str,
    sha256: str,
    filename: str,
    path_label: str,
    read_ledger_fn: object,
) -> tuple[str, str]:
    """Apply verdict-ledger hysteresis anchoring (sync helper).

    If the ledger records a higher-priority verdict for this content
    (keyed by *sha256*), override the just-computed verdict to prevent
    oscillation across re-ingestions.  Non-blocking, graceful degradation:
    any exception during ledger read is logged and the computed verdict
    is returned unchanged.

    *read_ledger_fn* is the ``read_verdict_ledger`` callable (injected
    to avoid a circular import from storage into helpers).

    *path_label* is ``"flat"`` or ``"tree"`` and appears only in log
    messages for differentiation.

    Returns ``(verdict, verdict_reason)`` -- possibly overridden.
    """
    try:
        _prior_verdict = read_ledger_fn(sha256)  # type: ignore[operator]
        if _prior_verdict is not None and _LEDGER_PRIORITY.get(
            _prior_verdict, -1
        ) > _LEDGER_PRIORITY.get(verdict, -1):
            _hysteresis_logger.info(
                "Zone-4 hysteresis anchor: overriding %s verdict %s -> %s "
                "for %s (sha256=%s, anchored_by_ledger)",
                path_label,
                verdict,
                _prior_verdict,
                filename,
                sha256[:12],
            )
            verdict_reason = f"anchored_by_ledger(was={verdict}:{verdict_reason})"
            verdict = _prior_verdict
    except Exception:
        _hysteresis_logger.warning(
            "Zone-4 hysteresis: ledger read failed for %s, continuing "
            "with computed verdict (graceful degradation)",
            filename,
            exc_info=True,
        )
    return verdict, verdict_reason
