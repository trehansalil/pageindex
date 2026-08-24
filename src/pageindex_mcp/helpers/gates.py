"""Gate functions, gate registry (GATES), and feature-wiring validation."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from ..config import pipeline_config
from ..script import RtlDecision, ScriptContext, _infer_script
from .garble import (
    _EMPTY_NODE_FRACTION_THRESHOLD,
    _RFC029_DEEP_TREE_DEPTH_THRESHOLD,
    _RFC029_MIN_CHARS_PER_NODE,
    _RFC029_MIN_CHARS_PER_NODE_DEEP,
    _RFC029_MIN_SCANNED_DENSITY_FLOOR,
    _garble_check_nodes,
    _garble_config,
)
from .tree_validation import TreeSignals, _count_empty_body_nodes
from .types import (
    ExtractionState,
    FeatureWiring,
    GateSpec,
    TreeDefect,
    _ReasonPolicy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate functions
# ---------------------------------------------------------------------------


def _gate_garbling(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 1: bulk garbling."""
    return (sig.garbled, "")


def _gate_node_count_low(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 2: node_count < 3."""
    return (sig.node_count < 3, "")


def _gate_depth_low(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 3: depth < 2."""
    return (sig.depth < 2, "")


def _gate_node_garbling(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 4: per-node garble ratio (RFC-018 D3b).

    Zone-3: uses ScriptContext + GarbleConfig instead of re-inferring
    script from sig.flat_text.  The document-level script is resolved
    from expected_script (filename-derived) or inferred from flat_text
    as a fallback, then wrapped into a ScriptContext for threaded
    garble detection.
    """
    if sig.node_count <= 0:
        return (False, "")
    doc_script = expected_script if expected_script is not None else _infer_script(sig.flat_text)
    _ctx = ScriptContext(
        dominant_script=doc_script,
        had_presentation_forms=False,
        source="gate_node_garbling",
    )
    ratio = (
        _garble_check_nodes(
            structure,
            page_script=doc_script,
            expected_script=expected_script,
            script_context=_ctx,
            config=_garble_config,
        )
        / sig.node_count
    )
    fires = ratio > _garble_config.garble_node_ratio_threshold
    return (fires, "")


def _gate_reordered(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 5: content-ordering regression (RFC-015 D2)."""
    return (sig.is_reordered, "")


def _gate_rtl_reversal(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 6: reversed Arabic text (RFC-027 D3).

    Consumes the pre-computed rtl_decision from Zone 3's cached
    decide_rtl call — does NOT re-call decide_rtl.
    """
    fires = bool(rtl_decision and rtl_decision.reversed)
    return (fires, "")


def _gate_bidi_degraded(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 7: bidi coherence degradation (RFC-030 D5 / RFC-033 D2 Part B).

    Consumes the pre-computed rtl_decision — does NOT re-call decide_rtl.

    BIDI_COHERENCE_ENFORCE (default ``true``) gates this gate: when set to
    anything other than ``true`` the gate is disabled outright and
    BIDI_DEGRADED can never enter ``all_defects``.  Enforcement here is
    verdict-only and never persistence-gating: REASON_POLICY maps
    BIDI_DEGRADED to CAP_MARGINAL, and BIDI_DEGRADED is deliberately absent
    from HARD_FAIL_DEFECTS, so classify_verdict caps a would-be PASS at
    MARGINAL and never upgrades a worse verdict.

    Zone-3 deleted ``_check_bidi_coherence``; its sole signal was
    ``decide_rtl(...).reversed``, which is also the RTL_REVERSAL signal.
    Because the gate table is evaluated exhaustively, both gates now fire
    together on reversed text: RTL_REVERSAL wins as the primary defect
    (earlier in table order) while BIDI_DEGRADED is recorded in
    ``all_defects`` instead of being masked.  Additional bidi-degradation
    heuristics that detect degradation without full reversal belong here
    and must respect the same env var.
    """
    if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() != "true":
        return (False, "")
    fires = bool(rtl_decision and rtl_decision.reversed)
    return (fires, "")


def _gate_empty_node_contamination(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 8: zero-body contamination (RFC-029 D10)."""
    _total_non_root, _empty_leaf, _empty_non_leaf = _count_empty_body_nodes(structure)
    if _total_non_root <= 0:
        return (False, "")
    _empty_fraction = (_empty_leaf + _empty_non_leaf) / _total_non_root
    if _empty_fraction > _EMPTY_NODE_FRACTION_THRESHOLD:
        detail = (
            f"fraction={_empty_fraction:.2f}"
            f",empty_leaf={_empty_leaf}"
            f",empty_non_leaf={_empty_non_leaf}"
            f",total_non_root={_total_non_root}"
        )
        return (True, detail)
    return (False, "")


def _gate_low_content_density(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 9: content-density floor (RFC-029 D1, Task 3.1).

    Only fires when node count >= 200.

    Zone-6 Step B: script/depth-aware thresholds.  Deep trees (depth >= 4)
    and Arabic-script documents use a lower floor
    (``RFC029_MIN_CHARS_PER_NODE_DEEP``, default 50) to avoid false-rejecting
    well-structured legal hierarchies — the flat 150 chars/node threshold was
    already lowered once by RFC-030 D3 for exactly this class of regression.
    Shallow non-Arabic documents keep the existing 150 floor unchanged.
    """
    if sig.node_count < 200:
        return (False, "")

    is_deep = sig.depth >= _RFC029_DEEP_TREE_DEPTH_THRESHOLD
    is_arabic = expected_script == "Arab"
    if is_deep or is_arabic:
        threshold = _RFC029_MIN_CHARS_PER_NODE_DEEP
    else:
        threshold = _RFC029_MIN_CHARS_PER_NODE

    chars_per_node = len(sig.flat_text) / sig.node_count
    if chars_per_node < threshold:
        detail = (
            f"chars_per_node={chars_per_node:.1f}"
            f",threshold={threshold:.1f}"
            f",deep={is_deep},arabic={is_arabic}"
        )
        return (True, detail)
    return (False, "")


def _gate_suspect_density(
    sig: TreeSignals,
    structure: list,
    expected_script: str | None,
    page_count: int | None,
    rtl_decision: RtlDecision | None,
) -> tuple[bool, str]:
    """Gate 10: scanned-density floor (RFC-029 D2, Task 3.3).

    Only fires when page_count is provided and positive.
    """
    if page_count is None or page_count <= 0:
        return (False, "")
    chars_per_page = len(sig.flat_text) / page_count
    if chars_per_page < _RFC029_MIN_SCANNED_DENSITY_FLOOR:
        return (True, f"chars_per_page={chars_per_page:.1f}")
    return (False, "")


# Type alias for gate function signature.
_GateFn = Callable[
    [TreeSignals, list, str | None, int | None, RtlDecision | None],
    tuple[bool, str],
]

# ---------------------------------------------------------------------------
# Zone-1: Recovery eligibility predicates (single source of truth)
# ---------------------------------------------------------------------------


def _eligible_garble(state: ExtractionState) -> bool:
    """Garble-type OCR + VLM recovery eligibility (GARBLING or NODE_GARBLING).

    Flag gates for OCR escalation and VLM are checked inside the individual
    recovery methods (_recover_garble_ocr, _recover_vlm_fallback) to preserve
    their independence (VLM can fire even when OCR escalation is off).
    """
    return not state.ok and state.first_defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)


def _eligible_low_content(state: ExtractionState) -> bool:
    """Low-content / image-dominant recovery eligibility (NODE_COUNT_LOW).

    Combined OR-gate: at least one of ocr_escalation_garble or
    image_dominant_ocr_escalation_enabled must be active.  Individual
    recovery methods check their specific flag.
    """
    return (
        not state.ok
        and state.first_defect == TreeDefect.NODE_COUNT_LOW
        and (
            pipeline_config.ocr_escalation_garble
            or pipeline_config.image_dominant_ocr_escalation_enabled
        )
    )


def _eligible_image_dominant(state: ExtractionState) -> bool:
    """Image-dominant OCR recovery eligibility (DEPTH_LOW).

    DEPTH_LOW only has image-dominant recovery; the flag is checked here
    so the gate is skipped entirely when disabled.
    """
    return (
        not state.ok
        and pipeline_config.image_dominant_ocr_escalation_enabled
        and state.first_defect == TreeDefect.DEPTH_LOW
    )


def _eligible_rtl(state: ExtractionState) -> bool:
    """RTL repair eligibility (RTL_REVERSAL)."""
    return not state.ok and state.first_defect == TreeDefect.RTL_REVERSAL


# ---------------------------------------------------------------------------
# Zone-3: Unified gate registry — single source of truth
# ---------------------------------------------------------------------------

# Declarative gate list: one GateSpec per TreeDefect.  Table order (among
# active gates) defines primary-defect severity — first firing entry =
# primary defect.  ALL active gates are always evaluated — no early return.
#
# ARABIC_LOW_CONTENT_RATIO (dead gate, gate_fn=None) and OK (not a gate,
# gate_fn=None) are included solely for REASON_POLICY completeness so
# that ``set(REASON_POLICY) == set(TreeDefect)`` holds.
#
# ``hard_fail`` is orthogonal to ``policy``: GARBLING has RETRY_OCR
# (recovery policy) AND hard_fail=True (verdict-floor); SUSPECT_DENSITY
# has PERSIST_FAIL AND hard_fail=True.  This is intentional dual-axis
# design, not a bug to collapse.
#
# Zone-1: ``recovery_eligible`` and ``recovery_fns`` replace the former
# ``recovery_tag`` + client-side ``_recovery_dispatch`` dict.  Each gate
# with recovery declares its eligibility predicate and the tuple of
# recovery method names to invoke (resolved via getattr at call time).
# GARBLING handles both GARBLING and NODE_GARBLING defects via
# ``_eligible_garble``; NODE_GARBLING carries the same recovery_fns for
# the bidirectional exhaustiveness assertion.
GATES: list[GateSpec] = [
    GateSpec(
        TreeDefect.GARBLING,
        _ReasonPolicy.RETRY_OCR,
        hard_fail=True,
        gate_fn=_gate_garbling,
        severity=0,
        recovery_eligible=_eligible_garble,
        recovery_fns=("_recover_garble_ocr", "_recover_vlm_fallback"),
    ),
    GateSpec(
        TreeDefect.NODE_COUNT_LOW,
        _ReasonPolicy.RAISE,
        gate_fn=_gate_node_count_low,
        severity=1,
        recovery_eligible=_eligible_low_content,
        recovery_fns=("_recover_low_content_ocr", "_recover_image_dominant_ocr"),
    ),
    GateSpec(
        TreeDefect.DEPTH_LOW,
        _ReasonPolicy.RAISE,
        gate_fn=_gate_depth_low,
        severity=2,
        recovery_eligible=_eligible_image_dominant,
        recovery_fns=("_recover_image_dominant_ocr",),
    ),
    GateSpec(
        TreeDefect.NODE_GARBLING,
        _ReasonPolicy.RETRY_OCR,
        gate_fn=_gate_node_garbling,
        severity=3,
        recovery_eligible=_eligible_garble,
        recovery_fns=("_recover_garble_ocr", "_recover_vlm_fallback"),
    ),
    GateSpec(
        TreeDefect.REORDERED,
        _ReasonPolicy.RAISE,
        hard_fail=True,
        gate_fn=_gate_reordered,
        severity=4,
    ),
    GateSpec(
        TreeDefect.RTL_REVERSAL,
        _ReasonPolicy.RETRY_RTL,
        gate_fn=_gate_rtl_reversal,
        severity=5,
        recovery_eligible=_eligible_rtl,
        recovery_fns=("_recover_rtl_repair", "_recover_rtl_flat_compare"),
    ),
    GateSpec(
        TreeDefect.BIDI_DEGRADED,
        _ReasonPolicy.CAP_MARGINAL,
        gate_fn=_gate_bidi_degraded,
        severity=6,
    ),
    GateSpec(
        TreeDefect.EMPTY_NODE_CONTAMINATION,
        _ReasonPolicy.PERSIST_FAIL,
        hard_fail=True,
        gate_fn=_gate_empty_node_contamination,
        severity=7,
    ),
    GateSpec(
        TreeDefect.LOW_CONTENT_DENSITY,
        _ReasonPolicy.PERSIST_FAIL,
        hard_fail=True,
        gate_fn=_gate_low_content_density,
        severity=8,
    ),
    GateSpec(
        TreeDefect.SUSPECT_DENSITY,
        _ReasonPolicy.PERSIST_FAIL,
        hard_fail=True,
        gate_fn=_gate_suspect_density,
        severity=9,
    ),
    # Dead gate: strict subset of GARBLING; kept for persisted verdict_reason
    # compat and REASON_POLICY completeness.  severity=99 (default/dead).
    GateSpec(TreeDefect.ARABIC_LOW_CONTENT_RATIO, _ReasonPolicy.CAP_MARGINAL),
    # OK is not a gate — present for REASON_POLICY completeness only.
    # severity=99 (default/dead).
    GateSpec(TreeDefect.OK, _ReasonPolicy.OK),
]

# --- Derived backward-compat structures (from GATES) -----------------------

# GATE_TABLE: list of (gate_fn, defect) pairs for active gates only.
# Iteration order = severity order (first firing entry = primary defect).
GATE_TABLE: list[tuple[_GateFn, TreeDefect]] = [
    (g.gate_fn, g.defect)
    for g in GATES
    if g.gate_fn is not None  # type: ignore[misc]
]

# REASON_POLICY: maps every TreeDefect -> _ReasonPolicy.
REASON_POLICY = {g.defect: g.policy for g in GATES}

assert set(REASON_POLICY) == set(TreeDefect), (
    f"REASON_POLICY missing: {set(TreeDefect) - set(REASON_POLICY)}"
)

# Zone-1: bidirectional exhaustiveness assertion.
# Forward: every RETRY_OCR/RETRY_RTL gate must have non-empty recovery_fns
# and a non-None recovery_eligible so the GateSpec-driven recovery loop
# can dispatch to the right recovery method.
for _g in GATES:
    if _g.policy in (_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL):
        assert _g.recovery_fns and _g.recovery_eligible is not None, (
            f"GateSpec for {_g.defect.name} has {_g.policy.value} policy "
            f"but missing recovery_fns or recovery_eligible — wire them "
            f"to close the gate-to-recovery dispatch gap"
        )
# Reverse: every gate with non-empty recovery_fns must have a non-None
# recovery_eligible predicate (prevents orphaned recovery methods that
# never fire because the eligibility check is missing).
for _g in GATES:
    if _g.recovery_fns:
        assert _g.recovery_eligible is not None, (
            f"GateSpec for {_g.defect.name} has recovery_fns={_g.recovery_fns} "
            f"but no recovery_eligible predicate"
        )

# HARD_FAIL_DEFECTS: any of these in all_defects -> classify_verdict returns FAIL.
HARD_FAIL_DEFECTS = frozenset(g.defect for g in GATES if g.hard_fail)

# Severity rank per defect, derived from GateSpec.severity field (lower = more
# severe).  Used by classify_verdict to pick a deterministic reason when a
# hard-fail defect co-fires behind a less-severe primary defect.
# NOTE: severity values must mirror GATES list order for active gates so that
# validate_tree's list-order primary-defect selection stays consistent with
# compute_verdict's severity-based hard-fail tiebreak.
_GATE_PRIORITY: dict[TreeDefect, int] = {
    g.defect: g.severity for g in GATES if g.gate_fn is not None
}

# Import-time assertion: active-gate severities must be unique (no two active
# gates share the same severity value) to guarantee deterministic tiebreak.
_active_severities = [g.severity for g in GATES if g.gate_fn is not None]
assert len(_active_severities) == len(set(_active_severities)), (
    f"Active-gate severity values are not unique: {_active_severities}"
)

# ---------------------------------------------------------------------------
# Cross-module feature wiring registry
# ---------------------------------------------------------------------------

FEATURE_WIRINGS: list[FeatureWiring] = [
    FeatureWiring(
        name="pdf_inspector",
        producer="pageindex_mcp.converters.probe_conversion_route",
        consumers=(
            "pageindex_mcp.worker",
            "pageindex_mcp.client",
        ),
        config_flag="PDF_INSPECTOR_PRECLASSIFY",
        shadow_only=True,
    ),
    FeatureWiring(
        name="chunked_docling_timeout",
        producer="pageindex_mcp.converters.chunked_docling_timeout_s",
        consumers=("pageindex_mcp.worker",),
    ),
    FeatureWiring(
        name="picture_ocr_enrichment",
        producer="pageindex_mcp.helpers.compute_image_enrichment_ratio",
        consumers=("pageindex_mcp.client",),
    ),
    FeatureWiring(
        name="zdr_egress_gate",
        producer="pageindex_mcp.converters.zdr_egress_gate",
        consumers=("pageindex_mcp.client",),
    ),
    FeatureWiring(
        name="rtl_decision",
        producer="pageindex_mcp.script.decide_rtl",
        consumers=(
            "pageindex_mcp.helpers",
            "pageindex_mcp.client",
        ),
    ),
    FeatureWiring(
        name="gate_recovery_dispatch",
        producer="pageindex_mcp.helpers.GATES",
        consumers=("pageindex_mcp.client",),
    ),
]


def validate_feature_wirings() -> None:
    """Validate :data:`FEATURE_WIRINGS` producer/consumer contracts.

    For each :class:`FeatureWiring` entry:

    1. Resolves the producer dotted path via :func:`importlib.import_module`
       and confirms the target attribute exists.  Callable producers are
       verified as callable; non-callable producers (module-level data
       exports like ``GATE_TABLE``) are accepted as-is.
    2. For **non-shadow** entries, confirms each consumer module is loaded
       (present in ``sys.modules``) and that its source contains a reference
       to the producer function name.  Uses :func:`inspect.getsource` for
       the substring check so lazy (function-local) imports are caught too.
    3. For **shadow_only** entries, skips consumer assertions but logs a
       warning if consumers do not reference the producer — making shadow
       status visible, not silent.

    Raises :class:`AssertionError` for non-shadow wiring failures.

    Exported for explicit invocation by application entry points
    (server.py lifespan, worker.py startup) and tests.

    **Circular-import safety**: uses ``importlib.import_module`` and
    ``sys.modules`` introspection — never adds top-level imports from
    consumer modules (client, worker, converters) into helpers.py.
    """
    import importlib
    import inspect
    import sys

    _logger = logging.getLogger(__name__)

    for fw in FEATURE_WIRINGS:
        # --- Validate producer exists ---
        parts = fw.producer.rsplit(".", 1)
        if len(parts) != 2:
            raise AssertionError(
                f"FeatureWiring '{fw.name}': producer path '{fw.producer}' "
                f"must be 'module.attribute'"
            )
        mod_path, attr_name = parts

        try:
            mod = importlib.import_module(mod_path)
        except ImportError as exc:
            raise AssertionError(
                f"FeatureWiring '{fw.name}': producer module '{mod_path}' is not importable: {exc}"
            ) from exc

        producer_obj = getattr(mod, attr_name, None)
        if producer_obj is None:
            raise AssertionError(
                f"FeatureWiring '{fw.name}': producer '{fw.producer}' "
                f"not found in module '{mod_path}'"
            )
        if callable(producer_obj):
            pass
        else:
            _logger.debug(
                "FeatureWiring '%s': producer '%s' is a non-callable "
                "data export (type=%s) — accepted",
                fw.name,
                fw.producer,
                type(producer_obj).__name__,
            )

        # --- Validate consumer references ---
        for consumer_path in fw.consumers:
            consumer_mod = sys.modules.get(consumer_path)
            if consumer_mod is None:
                try:
                    consumer_mod = importlib.import_module(consumer_path)
                except ImportError as exc:
                    msg = (
                        f"FeatureWiring '{fw.name}': consumer module "
                        f"'{consumer_path}' is not importable: {exc}"
                    )
                    if fw.shadow_only:
                        _logger.warning("shadow wiring: %s", msg)
                        continue
                    raise AssertionError(msg) from exc

            try:
                source = inspect.getsource(consumer_mod)
            except (OSError, TypeError):
                source = ""

            if attr_name not in source and hasattr(consumer_mod, "__path__"):
                import pkgutil

                for _importer, submod_name, _ispkg in pkgutil.iter_modules(consumer_mod.__path__):
                    try:
                        submod = importlib.import_module(f"{consumer_path}.{submod_name}")
                        source = inspect.getsource(submod)
                        if attr_name in source:
                            break
                    except (ImportError, OSError, TypeError):
                        continue

            if attr_name not in source:
                msg = (
                    f"FeatureWiring '{fw.name}': consumer '{consumer_path}' "
                    f"does not reference producer function '{attr_name}' — "
                    f"the feature may be implemented but unwired"
                )
                if fw.shadow_only:
                    _logger.warning("shadow wiring: %s", msg)
                else:
                    raise AssertionError(msg)
