"""Core types and dataclasses for the helpers package."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from ..config import pipeline_config

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..script import RtlDecision, ScriptContext
    from .tree_validation import TreeSignals


class TreeDefect(StrEnum):
    OK = ""
    GARBLING = "garbling"
    NODE_GARBLING = "node_garbling"
    NODE_COUNT_LOW = "node_count<3"
    DEPTH_LOW = "depth<2"
    REORDERED = "reordered"
    RTL_REVERSAL = "rtl_reversal"
    BIDI_DEGRADED = "bidi_degraded"
    EMPTY_NODE_CONTAMINATION = "empty_node_contamination"
    LOW_CONTENT_DENSITY = "low_content_density"
    SUSPECT_DENSITY = "suspect_density"
    # deprecated: dead gate (strict subset of GARBLING); kept for persisted verdict_reason compat
    ARABIC_LOW_CONTENT_RATIO = "arabic_low_content_ratio"


# RFC-037 D6: single source of truth for verdict priority ordering
# (PASS > MARGINAL > FAIL > ERROR). Replaces _LEDGER_VERDICT_PRIORITY
# (storage/verdict.py) and _LEDGER_PRIORITY (helpers/verdict.py).
VERDICT_PRIORITY: dict[str, int] = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}

# Import-time assertion: values must be unique and form a strict total order.
assert len(set(VERDICT_PRIORITY.values())) == len(VERDICT_PRIORITY), (
    f"VERDICT_PRIORITY values must be unique: {VERDICT_PRIORITY}"
)
assert sorted(VERDICT_PRIORITY.values(), reverse=True) == list(VERDICT_PRIORITY.values()), (
    f"VERDICT_PRIORITY values must be in descending order: {VERDICT_PRIORITY}"
)



@dataclass(frozen=True)
class TreeGateResult:
    ok: bool
    defect: TreeDefect
    detail: str = ""
    signals: TreeSignals | None = None
    all_defects: frozenset[TreeDefect] = frozenset()
    warnings: tuple[str, ...] = ()

    def __str__(self) -> str:
        if self.detail:
            return f"{self.defect.value}({self.detail})"
        return self.defect.value

    def __iter__(self) -> Iterator[bool | str]:
        """Yield (ok, reason_str) for backward-compat tuple unpacking.

        ``signals``, ``all_defects``, and ``warnings`` are intentionally
        excluded from iteration so that ``ok, reason = validate_tree(...)``
        keeps working at all call sites.
        """
        yield self.ok
        yield str(self)


@dataclass(frozen=True)
class VerdictResult:
    """Zone-2: consolidated verdict returned by :func:`compute_verdict`.

    Fields mirror :class:`TreeGateResult`'s shape for consistency.
    ``__iter__`` yields ``(verdict, reason)`` so existing call sites that
    do ``verdict, reason = compute_verdict(...)`` keep working without
    changes.
    """

    verdict: str
    reason: str
    defect: TreeDefect = TreeDefect.OK
    signals: TreeSignals | None = None
    all_defects: frozenset[TreeDefect] = frozenset()
    # VG-6 telemetry: every promotion path that matched, in evaluation order.
    # ``promotion_paths_matched[0]`` is the winner whose reason became
    # ``reason``; the remainder are paths that *would* also have promoted the
    # document and are recorded so the ordering can be audited instead of
    # inferred.  Empty when no promotion path fired (FAIL / MARGINAL / the
    # image_standalone short-circuit).
    promotion_paths_matched: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[str]:
        """Yield ``(verdict, reason)`` for backward-compat tuple unpacking.

        ``defect``, ``signals``, ``all_defects``, and
        ``promotion_paths_matched`` are intentionally excluded from
        iteration so that ``verdict, reason = compute_verdict(...)`` keeps
        working at all existing call sites.
        """
        yield self.verdict
        yield self.reason


class _Unset:
    """Sentinel: field was not provided in :class:`RecoveryOutcome`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


_UNSET = _Unset()


@dataclass(frozen=True)
class RecoveryOutcome:
    """Zone-3: frozen pre-retry snapshot for OCR-escalation revert.

    All fields default to ``_UNSET`` (not provided).  ``apply(state)``
    writes only provided fields back to *state*.  For ``rtl_decision``,
    ``_UNSET`` means *no change* and ``None`` means *clear to None*.

    Replaces the positional-tuple ``ExtractionSnapshot.restore()``
    pattern with explicit field-by-field apply.
    """

    result: dict | _Unset = _UNSET  # type: ignore[assignment]
    ok: bool | _Unset = _UNSET  # type: ignore[assignment]
    reason: str | _Unset = _UNSET  # type: ignore[assignment]
    gate_result: TreeGateResult | None | _Unset = _UNSET  # type: ignore[assignment]
    md_content: str | None | _Unset = _UNSET  # type: ignore[assignment]
    pic_results: list | _Unset = _UNSET  # type: ignore[assignment]
    used_converter: str | None | _Unset = _UNSET  # type: ignore[assignment]
    total_chars: int | _Unset = _UNSET  # type: ignore[assignment]
    route: Route | _Unset = _UNSET  # type: ignore[assignment]
    rtl_decision: RtlDecision | None | _Unset = _UNSET  # type: ignore[assignment]
    tmp_md_path: str | None | _Unset = _UNSET  # type: ignore[assignment]
    bidi_renorm_applied: bool | _Unset = _UNSET  # type: ignore[assignment]

    def apply(self, state: ExtractionState) -> None:
        """Write provided (non-``_UNSET``) fields back to *state*."""
        if not isinstance(self.result, _Unset):
            state.result = self.result
        if not isinstance(self.ok, _Unset):
            state.ok = self.ok
        if not isinstance(self.reason, _Unset):
            state.reason = self.reason
        if not isinstance(self.gate_result, _Unset):
            state.gate_result = self.gate_result
        if not isinstance(self.md_content, _Unset):
            state.md_content = self.md_content
        if not isinstance(self.pic_results, _Unset):
            state.pic_results = self.pic_results
        if not isinstance(self.used_converter, _Unset):
            state.used_converter = self.used_converter
        if not isinstance(self.total_chars, _Unset):
            state.total_chars = self.total_chars
        if not isinstance(self.route, _Unset):
            state.route = self.route
        if not isinstance(self.rtl_decision, _Unset):
            state.rtl_decision = self.rtl_decision
        if not isinstance(self.tmp_md_path, _Unset):
            state.tmp_md_path = self.tmp_md_path
        if not isinstance(self.bidi_renorm_applied, _Unset):
            state.bidi_renorm_applied = self.bidi_renorm_applied


ExtractionSnapshot = RecoveryOutcome


@dataclass
class ExtractionState:
    """Zone-2: mutable extraction state threaded through the recovery pipeline.

    Consolidates the ~20 mutable locals from ``index()`` into a single object
    so each recovery method receives and mutates a coherent state bundle.

    ``rtl_decision`` (Zone-6): the authoritative ``RtlDecision`` computed
    once during conversion (``_pre_inference_normalize`` for local,
    ``_renormalize_bidi_guarded`` for remote) and threaded into
    ``validate_tree`` so it does not recompute on different text.
    """

    result: dict
    ok: bool
    reason: str
    gate_result: TreeGateResult | None
    first_defect: TreeDefect
    route: Route
    md_content: str | None
    tmp_md_path: str | None
    pic_results: list
    used_converter: str | None
    total_chars: int
    extraction_stages_captured: list
    pre_garbled: bool = False
    pdf_page_count: int | None = None
    use_remote: bool = False
    tmp_lo_dir: str | None = None
    flat_garble_unrecovered: bool = False
    bidi_renorm_applied: bool = False
    rtl_decision: RtlDecision | None = None
    landscape_pages: list | None = None
    full_page_already_applied: bool = False
    supports_ocr: bool = False


class _ReasonPolicy(StrEnum):
    RAISE = "raise"
    RETRY_OCR = "retry_ocr"
    RETRY_RTL = "retry_rtl"
    PERSIST_FAIL = "persist_fail"
    CAP_MARGINAL = "cap_marginal"
    OK = "ok"


# Type alias for gate function signature.
_GateFn = Callable[
    ["TreeSignals", list, "ScriptContext", "int | None", "RtlDecision | None"],
    tuple[bool, str],
]


@dataclass(frozen=True)
class GateSpec:
    """Unified per-defect gate metadata (single source of truth).

    Consolidates GATE_TABLE, REASON_POLICY, HARD_FAIL_DEFECTS,
    ``_GATE_PRIORITY``, and recovery dispatch into one declarative list
    (:data:`GATES`).  Legacy dicts are derived from GATES at import time
    for backward-compat consumers.

    ``gate_fn`` is ``None`` for deprecated / dead gates (e.g.
    ARABIC_LOW_CONTENT_RATIO) and for TreeDefect.OK (which is not a gate).

    ``recovery_eligible`` (Zone-1) is a predicate that checks whether
    recovery should be attempted for this gate given the current
    :class:`ExtractionState`.  ``recovery_fns`` is a tuple of method-name
    strings resolved via ``getattr(client, fn_name)`` at call time.
    Together they replace the former ``recovery_tag`` + client-side
    ``_recovery_dispatch`` dict, making GateSpec the single source of
    truth for both gate evaluation AND recovery routing.

    ``severity`` declares the gate's priority rank (lower = more severe).
    Active gates (``gate_fn is not None``) must have unique severity values;
    dead/placeholder gates use the default 99.  ``_GATE_PRIORITY`` is derived
    from this field rather than from GATE_TABLE list position.
    """

    defect: TreeDefect
    policy: _ReasonPolicy
    hard_fail: bool = False
    gate_fn: _GateFn | None = None
    severity: int = 99
    recovery_eligible: Callable[[ExtractionState], bool] | None = None
    recovery_fns: tuple[str, ...] = ()
    recovery_waived: bool = False


@dataclass(frozen=True)
class FeatureWiring:
    """Declarative cross-module feature contract.

    Each entry declares a feature that spans module boundaries: a *producer*
    function (dotted import path) and one or more *consumer* modules that
    must import or reference the producer.  An optional *config_flag* names
    the environment variable that gates the feature.

    ``shadow_only``
        When ``True`` the producer exists and runs but consumers are not
        required to act on its output (the feature is in observability-only
        / shadow mode).  Validation still confirms the producer is
        importable and callable but skips the consumer-reference assertion,
        logging a warning instead.  This prevents permanent-shadow states
        from being silently forgotten: the shadow flag in the registry is a
        conscious declaration, not an accidental gap.

    Validated at startup by :func:`validate_feature_wirings` so that
    "implemented but never wired" becomes a crash rather than a silent gap.
    """

    name: str
    producer: str
    consumers: tuple[str, ...]
    config_flag: str | None = None
    shadow_only: bool = False


class Route(StrEnum):
    TREE = "tree"
    FLAT = "flat"
    REJECT = "reject"
    PERSIST_FAIL = "persist_fail"


def decide_route(defect: TreeDefect, flat_routing_enabled: bool = True) -> Route:
    """Determine the extraction route from a :class:`TreeDefect`.

    Performs an exhaustive :data:`REASON_POLICY` lookup:

    * OK / CAP_MARGINAL -> TREE (tree is usable)
    * RETRY_OCR -> TREE (retry handled upstream)
    * RETRY_RTL -> FLAT when ``flat_routing_enabled`` (RFC-036 D3: flat
      is the fallback after RTL repair exhausted), else REJECT
    * RAISE -> FLAT when ``flat_routing_enabled`` and defect is
      NODE_COUNT_LOW or DEPTH_LOW, else REJECT
    * PERSIST_FAIL -> PERSIST_FAIL

    This is the **sole** routing decider -- no competing definitions.
    """
    from .gates import REASON_POLICY

    policy = REASON_POLICY[defect]
    if policy == _ReasonPolicy.OK:
        return Route.TREE
    if policy == _ReasonPolicy.RETRY_OCR:
        return Route.TREE
    if policy == _ReasonPolicy.RETRY_RTL:
        return Route.FLAT if flat_routing_enabled else Route.REJECT
    if policy == _ReasonPolicy.CAP_MARGINAL:
        return Route.TREE
    if policy == _ReasonPolicy.PERSIST_FAIL:
        return Route.PERSIST_FAIL
    if policy == _ReasonPolicy.RAISE:
        if flat_routing_enabled and defect in (
            TreeDefect.NODE_COUNT_LOW,
            TreeDefect.DEPTH_LOW,
        ):
            return Route.FLAT
        return Route.REJECT
    raise AssertionError(f"unhandled policy {policy!r} for defect {defect!r}")


def _defect_from_reason_str(reason: str | None) -> TreeDefect:
    """Parse a validate_reason string back into a :class:`TreeDefect`.

    Handles both exact matches (``"garbling"``) and prefix matches with
    parenthesised detail (``"empty_node_contamination(fraction=0.35,...)"``)
    by matching against each ``TreeDefect.value``.
    """
    if not reason:
        return TreeDefect.OK
    for td in TreeDefect:
        if td.value and (reason == td.value or reason.startswith(td.value + "(")):
            return td
    return TreeDefect.OK


def finalize_gate_and_route(
    state: ExtractionState,
    vt_raw: TreeGateResult | tuple[bool, str],
    flat_routing_enabled: bool = True,
) -> None:
    """Single writer of gate_result/ok/reason/first_defect/route on *state*.

    Zone-3: eliminates the stale-routing window by atomically deriving
    all five fields from a ``validate_tree`` result.  Every call site
    that previously set a subset of these fields (``_convert_to_tree``,
    ``_reconvert_and_revalidate``, ``_recover_rtl_repair``) must call
    this instead.

    Accepts both :class:`TreeGateResult` (preferred) and a legacy
    ``(ok, reason)`` tuple for backward compatibility.
    """
    if isinstance(vt_raw, TreeGateResult):
        state.gate_result = vt_raw
        state.ok = vt_raw.ok
        state.reason = str(vt_raw)
    else:
        state.gate_result = None
        state.ok = vt_raw[0]
        state.reason = vt_raw[1]

    state.first_defect = (
        state.gate_result.defect
        if state.gate_result is not None
        else _defect_from_reason_str(state.reason)
    )
    state.route = decide_route(state.first_defect, flat_routing_enabled)


@dataclass(frozen=True)
class GateOutcome:
    """Zone-4: typed intermediate connecting evaluate_gates() -> apply_promotions().

    Captures everything Phase 1 (gate evaluation + hard-fail checks) decides,
    so Phase 2 (promotions/caps) can consume it without re-derivation.
    """

    defect: TreeDefect
    validate_reason: str | None
    signals: TreeSignals
    all_defects: frozenset[TreeDefect]
    hard_fail_verdict: VerdictResult | None
    """Non-None when a hard-fail or zero-content fast path fired in Phase 1.
    When set, apply_promotions() is skipped and the caller returns this directly."""


@dataclass(frozen=True)
class VerdictThresholds:
    hard_fail_max_leaf_ratio: float
    pass_max_leaf_ratio: float
    garble_threshold: float
    cat_bc_promotion_threshold: float
    min_image_promoted_chars: int
    min_flat_promotion_chars: int
    small_doc_enabled: bool
    small_doc_leaf_ratio_bound_low: float
    small_doc_leaf_ratio_bound_high: float
    # Zone-8 content-volume floor: minimum stripped-text chars for MARGINAL.
    # Documents below this floor FAIL regardless of promotion eligibility,
    # enforcing CLAUDE.md HR#5 (never silently persist a low-quality tree).
    min_marginal_chars: int = 50
    # VG-2: OCR category-A promotion bounds, previously hardcoded inside
    # ``_try_cat_a``.  Defaults reproduce the old literals exactly.
    cat_a_max_leaf_ratio: float = 0.15
    cat_a_max_ocr_noise: float = 0.005
    # VG-3: small-doc stripped-text window, previously hardcoded inside
    # ``_try_small_doc``.  Defaults reproduce the old literals exactly.
    small_doc_min_chars: int = 100
    small_doc_max_chars: int = 15000

    @classmethod
    def from_env(cls) -> VerdictThresholds:
        """Legacy constructor -- delegates to from_config(pipeline_config)."""
        return cls.from_config(pipeline_config)

    @classmethod
    def from_config(cls, cfg: PipelineConfig) -> VerdictThresholds:
        """Build a typed threshold subset from a frozen PipelineConfig."""
        from ..config import CATEGORY_BC_PROMOTION_THRESHOLD

        return cls(
            hard_fail_max_leaf_ratio=cfg.hard_fail_max_leaf_ratio,
            pass_max_leaf_ratio=cfg.pass_max_leaf_ratio,
            garble_threshold=cfg.garble_window_ratio_threshold,
            cat_bc_promotion_threshold=CATEGORY_BC_PROMOTION_THRESHOLD,
            min_image_promoted_chars=cfg.min_image_promoted_chars,
            min_flat_promotion_chars=cfg.min_flat_promotion_chars,
            small_doc_enabled=cfg.small_doc_promotion_enabled,
            small_doc_leaf_ratio_bound_low=cfg.small_doc_leaf_ratio_bound_low,
            small_doc_leaf_ratio_bound_high=cfg.small_doc_leaf_ratio_bound_high,
            min_marginal_chars=cfg.min_marginal_chars,
            cat_a_max_leaf_ratio=cfg.cat_a_max_leaf_ratio,
            cat_a_max_ocr_noise=cfg.cat_a_max_ocr_noise,
            small_doc_min_chars=cfg.small_doc_min_chars,
            small_doc_max_chars=cfg.small_doc_max_chars,
        )


def _get_verdict_thresholds() -> VerdictThresholds:
    """Return VerdictThresholds derived from the current pipeline_config."""
    return VerdictThresholds.from_config(pipeline_config)


def reset_verdict_thresholds() -> None:
    """Legacy shim -- delegates to reset_pipeline_config().

    Kept so tests importing this name do not break at collection time.
    A separate test-update phase will migrate callers.
    """
    from ..config import reset_pipeline_config

    reset_pipeline_config()


class LowQualityTreeError(Exception):
    """Raised when validate_tree rejects a tree (HR5 / WORKER-01-C2).

    Carries .reason ('node_count<3' | 'depth<2' | 'garbling') so the worker can
    surface status=error reason=low_quality_tree without persisting anything."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"low_quality_tree: {reason}")
