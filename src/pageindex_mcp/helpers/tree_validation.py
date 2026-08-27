"""Tree metric functions, TreeSignals, and validate_tree."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..config import pipeline_config
from ..script import (
    ARABIC_RANGES,
    PRESENTATION_RANGES,
    RtlDecision,
    ScriptContext,
    _infer_script,
    decide_rtl,
)
from .types import (
    TreeDefect,
    TreeGateResult,
    VerdictThresholds,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Basic tree metrics
# ---------------------------------------------------------------------------


def _tree_node_count(nodes: list) -> int:
    total = 0
    for n in nodes:
        total += 1
        total += _tree_node_count(n.get("nodes") or [])
    return total


def _tree_depth(nodes: list) -> int:
    if not nodes:
        return 0
    best = 1
    for n in nodes:
        children = n.get("nodes") or []
        if children:
            best = max(best, 1 + _tree_depth(children))
    return best


def _flatten_tree_text(nodes: list) -> str:
    """Concatenate all title+text from a tree structure into a single string.

    RFC-033 D1: parts are newline-separated so adjacent node boundaries cannot
    glue an Arabic title onto Latin text and fabricate a mixed-script pattern
    for `_has_sparse_mojibake`. Empty parts are dropped rather than joined —
    emitting a separator for an absent title would inflate the character counts
    that the volume floors in `classify_verdict` measure.
    """
    parts: list[str] = []

    def _walk(ns: list) -> None:
        for n in ns:
            for field in ("title", "text"):
                value = str(n.get(field, ""))
                if value:
                    parts.append(value)
            _walk(n.get("nodes") or [])

    _walk(nodes)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Leaf helpers
# ---------------------------------------------------------------------------


def _walk_leaves(structure: list):
    """Yield each leaf node dict (no ``nodes`` children) in document order."""
    for n in structure or []:
        if not isinstance(n, dict):
            continue
        children = n.get("nodes") or []
        if children:
            yield from _walk_leaves(children)
        else:
            yield n


def _count_empty_body_nodes(structure: list) -> tuple[int, int, int]:
    """RFC-029 D10: count non-root nodes with empty stripped body text.

    Returns (total_non_root, empty_leaf, empty_non_leaf).
    A node's «body text» is its ``text`` field (stripped); ``title``-only
    nodes are intentional structural nodes and are NOT counted as empty.
    Counts are over all non-root nodes (the entire tree minus the top-level
    list elements, which are document roots).
    """
    total = 0
    empty_leaf = 0
    empty_non_leaf = 0

    def _walk(nodes: list, is_root_level: bool = False) -> None:
        nonlocal total, empty_leaf, empty_non_leaf
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = node.get("nodes") or []
            if not is_root_level:
                total += 1
                body = node.get("text", "") or ""
                if not body.strip() and not str(node.get("title") or "").strip():
                    if children:
                        empty_non_leaf += 1
                    else:
                        empty_leaf += 1
            _walk(children, is_root_level=False)

    _walk(structure, is_root_level=True)
    return total, empty_leaf, empty_non_leaf


def _tree_max_leaf_ratio(structure: list) -> tuple[int, int, float]:
    max_leaf = 0
    total = 0
    for leaf in _walk_leaves(structure):
        chars = len(leaf.get("title", "")) + len(leaf.get("text", ""))
        total += chars
        max_leaf = max(max_leaf, chars)

    ratio = max_leaf / total if total > 0 else 0.0
    return max_leaf, total, ratio


def _tree_is_reordered(structure: list) -> bool:
    """RFC-015 D2: True if any leaf's start_index (fallback line_num) regresses
    below the running max seen so far — i.e. content emitted out of source order
    (54e92c0a: a span emitted after the document's final article)."""
    running_max: int | None = None
    for leaf in _walk_leaves(structure):
        idx = leaf.get("start_index", leaf.get("line_num"))
        if idx is None:
            continue
        if running_max is not None and idx < running_max:
            return True
        running_max = idx if running_max is None else max(running_max, idx)
    return False


# ---------------------------------------------------------------------------
# Noise ratios — canonical copies live in garble.py; imported for local use.
# Zone-4: deleted byte-identical duplicates (ocr_noise_ratio, hash_pipe_ratio,
# _garble_ratio) that lived here after the monolith decomposition (06b2bae).
# Redirected to the canonical garble.py copies to eliminate
# fix-one-miss-the-other drift (RFC-013 D7).
# ---------------------------------------------------------------------------
from .garble import _garble_ratio  # noqa: F401  (used by from_tree below)


# ---------------------------------------------------------------------------
# TreeSignals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeSignals:
    node_count: int
    depth: int
    max_leaf_ratio: float
    flat_text: str
    garbled: bool
    garble_ratio: float
    effectively_garbled: bool
    is_reordered: bool
    expected_min_depth: int
    primary_text: str = ""

    @classmethod
    def from_tree(
        cls,
        structure: list,
        expected_script: str | None | ScriptContext = None,
        garble_threshold: float = 0.05,
    ) -> TreeSignals:
        from .garble import _garble_config, detect_garble
        from ..script import BlobKind

        node_count = _tree_node_count(structure)
        depth = _tree_depth(structure)
        _, _, max_leaf_ratio = _tree_max_leaf_ratio(structure)
        flat_text = _flatten_tree_text(structure)

        if isinstance(expected_script, ScriptContext):
            _eff_script: str | None = expected_script.dominant_script
            _had_pf = expected_script.had_presentation_forms
        else:
            _eff_script = (
                expected_script if expected_script is not None else _infer_script(flat_text)
            )
            # Best-effort presentation-forms scan on flat_text.  When the
            # tree text is still pre-NFKC the scan detects Arabic
            # Presentation Forms; post-NFKC the ratio is 0 (same as the
            # prior False default).
            _pf_count = sum(
                1 for c in flat_text
                if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES)
            ) if flat_text else 0
            _ar_count = sum(
                1 for c in flat_text
                if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES)
            ) if flat_text else 0
            _had_pf = _ar_count > 0 and (_pf_count / _ar_count) > 0.50
            if not _had_pf:
                logger.debug(
                    "TreeSignals.from_tree received bare expected_script=%r; "
                    "had_presentation_forms=%s (scanned flat_text; upstream "
                    "should pass ScriptContext for accurate PF detection)",
                    expected_script,
                    _had_pf,
                )

        # Zone-4: unified detect_garble entry point replaces check_garble.
        _ctx = ScriptContext(
            dominant_script=_eff_script,
            had_presentation_forms=_had_pf,
            source="tree_signals",
        )
        garbled = bool(structure) and bool(detect_garble(
            flat_text,
            script_context=_ctx,
            config=_garble_config,
            blob_kind=BlobKind.TREE_TEXT,
        ))
        if garbled:
            gr = _garble_ratio(flat_text, expected_script=_eff_script, script_context=_ctx)
            effectively_garbled = gr >= garble_threshold
        else:
            gr = 0.0
            effectively_garbled = False
        is_reordered = _tree_is_reordered(structure)
        expected_min_depth = min(5, 2 + math.floor(math.log2(max(node_count, 1) / 50)))
        return cls(
            node_count=node_count,
            depth=depth,
            max_leaf_ratio=max_leaf_ratio,
            flat_text=flat_text,
            garbled=garbled,
            garble_ratio=gr,
            effectively_garbled=effectively_garbled,
            is_reordered=is_reordered,
            expected_min_depth=expected_min_depth,
            primary_text=flat_text,
        )


# ---------------------------------------------------------------------------
# validate_tree
# ---------------------------------------------------------------------------


def validate_tree(
    structure: list,
    expected_script: str | None | ScriptContext = None,
    page_count: int | None = None,
    *,
    rtl_decision: RtlDecision | None = None,
) -> TreeGateResult:
    """Gate a PageIndex tree before persistence (HR5 / WORKER-01-C2).

    Returns a ``TreeGateResult`` (iterable as ``(ok, reason_str)`` for
    backward-compat tuple unpacking).  The result carries a ``signals``
    field with the :class:`TreeSignals` computed during gating, and an
    ``all_defects`` field with every co-firing defect, so that
    ``classify_verdict`` can consume them without re-derivation.

    Evaluates all 10 gates exhaustively via :data:`GATE_TABLE`.  The
    primary defect (``defect`` field) is the first firing gate in table
    order (garbling highest severity, suspect_density lowest).
    ``all_defects`` is the frozenset of every firing gate's defect.

    Gate 11 (arabic_low_content_ratio) was removed: it is a strict subset
    of gate 1 (detect_garble already tests _is_garbled_blob on the
    flattened text) and was unreachable.

    Zone-6: accepts an optional pre-computed ``rtl_decision`` so callers
    that already ran ``decide_rtl`` during conversion can thread the same
    decision through without re-computation on potentially different text.
    Falls back to computing from ``sig.flat_text`` when not provided.
    """
    from .gates import GATE_TABLE

    th = VerdictThresholds.from_config(pipeline_config)

    # Zone-4: build/preserve ScriptContext for gate dispatch.
    if isinstance(expected_script, ScriptContext):
        _script_ctx = expected_script
    else:
        _script_ctx = ScriptContext.from_script_str(expected_script)

    sig = TreeSignals.from_tree(
        structure,
        expected_script=expected_script,
        garble_threshold=th.garble_threshold,
    )

    _rtl_decision = rtl_decision
    if _rtl_decision is None:
        _rtl_decision = decide_rtl(sig.flat_text) if sig.flat_text else None

    fired: list[tuple[TreeDefect, str]] = []
    for gate_fn, defect in GATE_TABLE:
        fires, detail = gate_fn(sig, structure, _script_ctx, page_count, _rtl_decision)
        if fires:
            fired.append((defect, detail))

    if fired:
        primary_defect, primary_detail = fired[0]
        # D4: garble-type defects must win as primary when co-firing with
        # non-garble defects, so OCR recovery dispatches correctly.
        _garble_defects = {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}
        if primary_defect not in _garble_defects:
            for d, detail in fired:
                if d in _garble_defects:
                    primary_defect, primary_detail = d, detail
                    break
        return TreeGateResult(
            ok=False,
            defect=primary_defect,
            detail=primary_detail,
            signals=sig,
            all_defects=frozenset(d for d, _ in fired),
        )
    # Advisory warnings for sub-threshold garble signals (no behavioral
    # change: ok=True verdict is preserved).
    _warnings: list[str] = []
    if sig.garble_ratio > 0.0:
        _warnings.append(
            f"sub_threshold_garble: ratio={sig.garble_ratio:.3f}"
        )
    # Near-firing checks for structural gates (cheap — uses pre-computed
    # signals; advisory only, no behavioral change).
    if sig.node_count <= 5:
        _warnings.append(
            f"near_gate_node_count: count={sig.node_count} (gate fires <3)"
        )
    if sig.depth == 2:
        _warnings.append(
            f"near_gate_depth: depth={sig.depth} (gate fires <2)"
        )
    if sig.max_leaf_ratio > th.pass_max_leaf_ratio * 0.8:
        _warnings.append(
            f"near_gate_leaf_concentration: ratio={sig.max_leaf_ratio:.3f} "
            f"(pass threshold={th.pass_max_leaf_ratio:.2f})"
        )
    return TreeGateResult(
        ok=True,
        defect=TreeDefect.OK,
        signals=sig,
        all_defects=frozenset(),
        warnings=tuple(_warnings),
    )
