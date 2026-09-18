"""Tree metric functions, TreeSignals, and validate_tree."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..config import pipeline_config
from ..obs import Phase, decision, phase
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


def _node_text_parts(n: dict) -> list[str]:
    """Extract all text-bearing content from a single tree node.

    Zone-5 fix: table blocks carry content in 'headers', 'rows', and
    'row_records' instead of 'text'.  This helper mirrors the extraction
    logic in flat.py:_flat_search_text (L187-236) so tree-level char
    counting, garble detection, and leaf-ratio scoring see the same
    content that flat-document tooling already sees.

    D2 (RFC-041): delegates primary text extraction to
    ``block_text(n, CHAR_COUNT)`` for the body portion so table-header
    handling is consistent across all consumers.  Title is still
    extracted separately because callers (e.g. garble.py) need it as
    a distinct part.

    Returns a list of non-empty string parts (title, body text, and
    table content).
    """
    from .flat import BlockTextPurpose, block_text

    parts: list[str] = []
    for field in ("title", "text"):
        value = str(n.get(field, ""))
        if value:
            parts.append(value)

    # Table block content: headers, rows, row_records carry text.
    # Use block_text for row_records (handles dict records) and
    # Zone-9 header-only fallback; extract raw rows individually
    # since block_text collapses them into one string.
    has_table_fields = (
        n.get("headers") is not None
        or n.get("rows") is not None
        or n.get("row_records") is not None
    )
    if has_table_fields:
        for header in n.get("headers") or []:
            if isinstance(header, str) and header:
                parts.append(header)
        for row in n.get("rows") or []:
            if isinstance(row, (list, tuple)):
                for cell in row:
                    cell_str = str(cell) if cell else ""
                    if cell_str:
                        parts.append(cell_str)
        for rec in n.get("row_records") or []:
            if isinstance(rec, str) and rec:
                parts.append(rec)
            elif isinstance(rec, dict):
                for v in rec.values():
                    v_str = str(v) if v else ""
                    if v_str:
                        parts.append(v_str)
    elif not n.get("text"):
        # Non-table, no text: delegate to block_text for any
        # remaining content (future-proofing).
        body = block_text(n, BlockTextPurpose.CHAR_COUNT)
        if body and body not in parts:
            parts.append(body)

    return parts


def _node_char_count(n: dict) -> int:
    """Total character count for a single tree node including table content.

    Zone-5 fix: used by _tree_max_leaf_ratio to correctly size table-only
    leaf nodes that carry zero chars in 'title'+'text' but substantive
    content in 'headers'/'rows'/'row_records'.
    """
    return sum(len(p) for p in _node_text_parts(n))


def _node_text_length(n: dict) -> int:
    """Stripped text length of a single tree node across all content sources.

    Measurement-tooling blind-spot fix: replaces bare
    ``node.get('text', '').strip()`` checks in emptiness gates that miss
    table blocks carrying content in 'headers'/'rows'/'row_records'.
    Delegates to :func:`_node_text_parts` so every content source is
    counted -- closing the shared blind spot between pipeline code and the
    measurement/audit tools that mirror it.
    """
    return sum(len(p.strip()) for p in _node_text_parts(n))


def _flatten_tree_text(nodes: list) -> str:
    """Concatenate all title+text from a tree structure into a single string.

    RFC-033 D1: parts are newline-separated so adjacent node boundaries cannot
    glue an Arabic title onto Latin text and fabricate a mixed-script pattern
    for `_has_sparse_mojibake`. Empty parts are dropped rather than joined —
    emitting a separator for an absent title would inflate the character counts
    that the volume floors in `classify_verdict` measure.

    Zone-5 fix: also extracts table block content from 'headers', 'rows',
    and 'row_records' via _node_text_parts, so table-heavy documents are
    no longer invisible to char-count scoring and garble detection.
    """
    parts: list[str] = []

    def _walk(ns: list) -> None:
        for n in ns:
            parts.extend(_node_text_parts(n))
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
    A node is «empty» when :func:`_node_text_length` returns 0 -- i.e. it
    has no stripped text in *any* content source (title, text, headers,
    rows, row_records).  Zone-7 fix: the prior ``node.get('text', '')``
    check missed table blocks whose content lives in headers/rows/
    row_records, falsely inflating the empty-node count for table-heavy
    documents.
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
                # Zone-7 fix: use _node_text_length which covers all
                # content sources (title, text, headers, rows, row_records)
                # — closing the measurement blind spot where table-only
                # nodes were falsely counted as empty.
                if not _node_text_length(node):
                    if children:
                        empty_non_leaf += 1
                    else:
                        empty_leaf += 1
            _walk(children, is_root_level=False)

    _walk(structure, is_root_level=True)
    return total, empty_leaf, empty_non_leaf


def _tree_max_leaf_ratio(structure: list) -> tuple[int, int, float]:
    # Zone-5 fix: use _node_char_count instead of title+text-only sizing
    # so table-only leaves are correctly measured.
    max_leaf = 0
    total = 0
    for leaf in _walk_leaves(structure):
        chars = _node_char_count(leaf)
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
from .garble import GarbleConfig, _garble_ratio  # noqa: F401  (used by from_tree below)


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
    # RFC-046 D2/R2.5: which garble prongs fired. Computed on every evaluation
    # and previously discarded by the surrounding bool(), leaving no stored
    # record of WHY a document was called garbled.
    garble_prongs: frozenset[str] = frozenset()

    @classmethod
    def from_tree(
        cls,
        structure: list,
        expected_script: str | None | ScriptContext = None,
        garble_threshold: float = 0.05,
        garble_config: GarbleConfig | None = None,
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
            _pf_count = (
                sum(
                    1
                    for c in flat_text
                    if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES)
                )
                if flat_text
                else 0
            )
            _ar_count = (
                sum(1 for c in flat_text if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES))
                if flat_text
                else 0
            )
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
        _effective_garble_config = garble_config if garble_config is not None else _garble_config
        _garble_report = (
            detect_garble(
                flat_text,
                script_context=_ctx,
                config=_effective_garble_config,
                blob_kind=BlobKind.TREE_TEXT,
            )
            if structure
            else None
        )
        garbled = bool(_garble_report)
        garble_prongs = _garble_report.fired_prongs if _garble_report is not None else frozenset()
        if garbled:
            gr = _garble_ratio(flat_text, expected_script=_eff_script, script_context=_ctx)
            effectively_garbled = gr >= garble_threshold
        else:
            gr = 0.0
            effectively_garbled = False
        is_reordered = _tree_is_reordered(structure)
        expected_min_depth = min(4, 2 + math.floor(math.log2(max(node_count, 1) / 100)))

        if not garbled:
            _verdict_choice = "not_garbled"
        elif effectively_garbled:
            _verdict_choice = "effectively_garbled"
        else:
            _verdict_choice = "sub_threshold_garbled"
        decision(
            event="tree_effective_garble_verdict",
            choice=_verdict_choice,
            reason=f"garble_ratio_vs_threshold_{garble_threshold}",
            attrs={
                "garbled": garbled,
                "garble_ratio": gr,
                "garble_threshold": garble_threshold,
                "garble_prongs": sorted(garble_prongs),
                "node_count": node_count,
                "depth": depth,
                "max_leaf_ratio": max_leaf_ratio,
                "is_reordered": is_reordered,
            },
            logger=logger,
        )

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
            garble_prongs=garble_prongs,
        )


# ---------------------------------------------------------------------------
# validate_tree
# ---------------------------------------------------------------------------


def _emit_gate_primary_defect_selection(
    computed_primary_defect: TreeDefect,
    final_primary_defect: TreeDefect,
    garble_co_fired: bool,
    fired: list[tuple[TreeDefect, str]],
) -> None:
    """R12.5: ``force_route``-shaped override inside ``validate_tree`` --
    the co-firing garble defect silently replaces ``fired[0]`` at the call
    site. Both labels survive here regardless of whether the override fired.
    """
    decision(
        event="gate_primary_defect_selection",
        choice="garble_override" if garble_co_fired else "first_fired_wins",
        reason=(
            "garble_defect_promoted_to_primary" if garble_co_fired else "first_fired_defect_kept"
        ),
        attrs={
            "computed_primary_defect": computed_primary_defect.value,
            "final_primary_defect": final_primary_defect.value,
            "garble_co_fired": garble_co_fired,
            "fired_defects": [d.value for d, _ in fired],
            "fired_count": len(fired),
        },
        logger=logger,
    )


def _emit_tree_gate_verdict(
    sig: TreeSignals,
    primary_defect: TreeDefect,
    fired: tuple | list,
    *,
    warning_labels,
) -> None:
    """The CLAUDE.md HR5 'never silently persist a low-quality tree' record."""
    decision(
        event="tree_gate_verdict",
        choice="gate_failed" if fired else "gate_passed",
        reason="gate_fired" if fired else "no_gate_fired",
        attrs={
            "primary_defect": primary_defect.value,
            "all_defects": sorted(d.value for d, _ in fired),
            "node_count": sig.node_count,
            "depth": sig.depth,
            "garble_ratio": sig.garble_ratio,
            "max_leaf_ratio": sig.max_leaf_ratio,
            "is_reordered": sig.is_reordered,
            "warning_labels": list(warning_labels),
        },
        logger=logger,
    )


def validate_tree(
    structure: list,
    expected_script: str | None | ScriptContext = None,
    page_count: int | None = None,
    *,
    rtl_decision: RtlDecision | None = None,
    garble_config: GarbleConfig | None = None,
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

    # No per-call attrs here (R12.9): node_count/depth/etc. are already
    # cheap-to-compute TreeSignals fields carried on tree_gate_verdict
    # below -- recomputing them again just to decorate the phase entry
    # would mean walking the tree twice.
    with phase(Phase.TREE_VALIDATE):
        sig = TreeSignals.from_tree(
            structure,
            expected_script=expected_script,
            garble_threshold=th.garble_threshold,
            garble_config=garble_config,
        )

        # Zone-4 + Zone-7 fix: build ScriptContext for gate dispatch AFTER
        # TreeSignals.from_tree computes flat_text.  When expected_script is a
        # bare string, the old from_script_str path hardcoded
        # had_presentation_forms=False, causing _gate_node_garbling (which
        # threads _script_ctx into _garble_check_nodes) to disagree with
        # sig.garbled (which from_tree computed with accurate PF detection).
        # Now: scan sig.flat_text for presentation forms so the gate dispatch
        # ScriptContext is consistent with from_tree's internal PF detection.
        if isinstance(expected_script, ScriptContext):
            _script_ctx = expected_script
        else:
            from .garble import _infer_presentation_forms

            _eff_script = (
                expected_script
                if expected_script is not None
                else _infer_script(sig.flat_text)
                if sig.flat_text
                else None
            )
            _script_ctx = ScriptContext(
                dominant_script=_eff_script,
                had_presentation_forms=_infer_presentation_forms(sig.flat_text),
                source="validate_tree",
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
            computed_primary_defect, primary_detail = fired[0]
            primary_defect = computed_primary_defect
            # D4: garble-type defects must win as primary when co-firing with
            # non-garble defects, so OCR recovery dispatches correctly.
            _garble_defects = {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}
            _garble_co_fired = computed_primary_defect not in _garble_defects and any(
                d in _garble_defects for d, _ in fired
            )
            if primary_defect not in _garble_defects:
                for d, detail in fired:
                    if d in _garble_defects:
                        primary_defect, primary_detail = d, detail
                        break
            _emit_gate_primary_defect_selection(
                computed_primary_defect, primary_defect, _garble_co_fired, fired
            )
            _emit_tree_gate_verdict(sig, primary_defect, fired, warning_labels=())
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
        _warning_labels: list[str] = []
        if sig.garble_ratio > 0.0:
            _warnings.append(f"sub_threshold_garble: ratio={sig.garble_ratio:.3f}")
            _warning_labels.append("sub_threshold_garble")
        # Near-firing checks for structural gates (cheap — uses pre-computed
        # signals; advisory only, no behavioral change).
        if sig.node_count <= 5:
            _warnings.append(f"near_gate_node_count: count={sig.node_count} (gate fires <3)")
            _warning_labels.append("near_gate_node_count")
        if sig.depth == 2:
            _warnings.append(f"near_gate_depth: depth={sig.depth} (gate fires <2)")
            _warning_labels.append("near_gate_depth")
        if sig.max_leaf_ratio > th.pass_max_leaf_ratio * 0.8:
            _warnings.append(
                f"near_gate_leaf_concentration: ratio={sig.max_leaf_ratio:.3f} "
                f"(pass threshold={th.pass_max_leaf_ratio:.2f})"
            )
            _warning_labels.append("near_gate_leaf_concentration")
        _emit_tree_gate_verdict(sig, TreeDefect.OK, (), warning_labels=_warning_labels)
        return TreeGateResult(
            ok=True,
            defect=TreeDefect.OK,
            signals=sig,
            all_defects=frozenset(),
            warnings=tuple(_warnings),
        )
