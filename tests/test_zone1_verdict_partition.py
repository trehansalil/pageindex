# ALLOW-NEW-TEST-FILE: zone "Verdict-Gate Threshold / Promotion / Override Cascade"
"""Golden partition table for the verdict-gate promotion cascade.

Zone: *Verdict-Gate Threshold / Promotion / Override Cascade*.

The six promotion paths in :func:`apply_promotions` overlap heavily — the
same feature vector routinely satisfies three or four of them at once, and
which one "wins" is decided purely by source-code order.  Before VG-6 that
ordering was invisible: the pipeline short-circuited at the first match, so
a reader (and a test) could only observe the winner, never the shadowed
paths.  ``VerdictResult.promotion_paths_matched`` now records the full
ordered match set.

This module pins that partition down as a characterization table:

* :data:`GOLDEN_TABLE` — ~34 feature vectors, each mapped to its exact
  ``(promotion_paths_matched, verdict, reason)``.  Any change to a
  threshold, a guard, or the path ordering moves at least one row.
* Two overlap clusters are asserted explicitly (see
  :class:`TestOverlapClusters`): ``structural_pass`` shadowing the other
  paths, and ``image_enrichment`` / ``cat_b`` / ``small_doc`` competing on
  ``flat_*`` documents.
* VG-1 regression: a garbled ``ocr_*`` document must not promote via cat_a.
* VG-6 contracts: behavior-identity against a short-circuit reference
  implementation, and tuple-unpacking compatibility of ``VerdictResult``.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.config import pipeline_config
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.types import GateOutcome, TreeDefect, VerdictThresholds
from pageindex_mcp.helpers.verdict import (
    _classify_image_verdict,
    _clamp_pass,
    _try_cat_a,
    _try_content_class_promotion,
    _try_flat_promotion,
    _try_image_enrichment,
    _try_ocr_promotion,
    _try_small_doc_promotion,
    _try_structural_pass,
    apply_promotions,
    compute_verdict,
)

# ---------------------------------------------------------------------------
# Fixture text corpora
# ---------------------------------------------------------------------------

PROSE = (
    "Die Versicherung leistet Entschaedigung fuer Schaeden an dem versicherten "
    "Gegenstand, soweit die vereinbarten Bedingungen dies vorsehen. "
)
TEXT_LONG = PROSE * 20  # ~2700 chars — clears every char floor
TEXT_SHORT = PROSE * 2  # ~270 chars — clears small_doc_min_chars, fails
#                          min_flat_promotion_chars / min_image_promoted_chars
TEXT_TINY = "Kurzer Text."  # 12 chars — below min_marginal_chars
TEXT_NOISY = (PROSE + "�" * 6) * 20  # ocr_noise_ratio above cat_a bound
TEXT_HASHY = (PROSE + "#" * 30) * 20  # hash_pipe_ratio above the cat_c bound

_OK = frozenset[TreeDefect]()
_NCL = frozenset({TreeDefect.NODE_COUNT_LOW})
_DL = frozenset({TreeDefect.DEPTH_LOW})


def _th() -> VerdictThresholds:
    """Production thresholds, as compute_verdict itself resolves them."""
    return VerdictThresholds.from_config(pipeline_config)


def _sig(**kw) -> TreeSignals:
    d = dict(
        node_count=10,
        depth=3,
        max_leaf_ratio=0.10,
        flat_text=TEXT_LONG,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
        primary_text=None,
    )
    d.update(kw)
    if d["primary_text"] is None:
        d["primary_text"] = d["flat_text"]
    return TreeSignals(**d)  # type: ignore[arg-type]


def _outcome(sig: TreeSignals, all_defects: frozenset[TreeDefect]) -> GateOutcome:
    return GateOutcome(
        defect=TreeDefect.OK,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects,
        hard_fail_verdict=None,
    )


def _run(case: "Case"):
    return apply_promotions(
        _outcome(case.sig, case.all_defects),
        case.content_class,
        case.image_enrichment_ratio,
        case.inspector_class,
        _th(),
        None,
    )


class Case:
    """One row of the golden partition table."""

    __slots__ = (
        "name",
        "sig",
        "content_class",
        "image_enrichment_ratio",
        "inspector_class",
        "all_defects",
        "paths",
        "verdict",
        "reason",
    )

    def __init__(
        self,
        name: str,
        sig: TreeSignals,
        content_class: str,
        image_enrichment_ratio: float | None,
        inspector_class: str | None,
        all_defects: frozenset[TreeDefect],
        paths: tuple[str, ...],
        verdict: str,
        reason: str,
    ) -> None:
        self.name = name
        self.sig = sig
        self.content_class = content_class
        self.image_enrichment_ratio = image_enrichment_ratio
        self.inspector_class = inspector_class
        self.all_defects = all_defects
        self.paths = paths
        self.verdict = verdict
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - pytest id only
        return self.name


# ---------------------------------------------------------------------------
# GOLDEN PARTITION TABLE
#
# Columns: feature vector -> (promotion_paths_matched, verdict, reason).
# ``paths`` is the FULL ordered match set; ``paths[0]`` is the winner whose
# reason became ``reason``.  Rows were captured from the post-fix
# implementation and are characterization assertions: a diff here means the
# promotion partition moved and needs a deliberate re-baseline.
# ---------------------------------------------------------------------------

GOLDEN_TABLE: list[Case] = [
    # --- ocr_* documents ---------------------------------------------------
    Case(
        "ocr_clean_low_ratio",
        _sig(max_leaf_ratio=0.10),
        "ocr_scanned", None, None, _OK,
        ("structural_pass", "cat_a"), "PASS", "structural_pass",
    ),
    Case(
        "ocr_cat_a_only",
        _sig(max_leaf_ratio=0.10),
        "ocr_scanned", None, None, _DL,
        ("cat_a",), "PASS", "cat_a_promoted",
    ),
    Case(
        # VG-1: garble guard on cat_a. structural_pass is also garble-guarded,
        # so nothing promotes and the doc lands on the garbling fallback.
        "ocr_garbled_VG1",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned", None, None, _OK,
        (), "MARGINAL", "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_garbled_VG1_structblocked",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned", None, None, _DL,
        (), "MARGINAL", "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_garbled_nodecount_low",
        _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4),
        "ocr_scanned", None, None, _NCL,
        (), "MARGINAL", "garbling(ratio=0.40)",
    ),
    Case(
        "ocr_ratio_above_cat_a",
        _sig(max_leaf_ratio=0.20),
        "ocr_scanned", None, None, _OK,
        ("structural_pass",), "PASS", "structural_pass",
    ),
    Case(
        "ocr_ratio_above_cat_a_structblocked",
        _sig(max_leaf_ratio=0.20),
        "ocr_scanned", None, None, _DL,
        (), "MARGINAL", "leaf_concentration=0.20",
    ),
    Case(
        # VG-2: ocr_noise_ratio above cat_a_max_ocr_noise blocks cat_a.
        "ocr_noise_high_structblocked",
        _sig(max_leaf_ratio=0.10, flat_text=TEXT_NOISY),
        "ocr_scanned", None, None, _DL,
        (), "MARGINAL", "leaf_concentration=0.10",
    ),
    # --- flat_* documents: the image_enrichment / cat_b / small_doc cluster --
    Case(
        "flat_clean_all_paths",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose", 0.9, None, _OK,
        ("image_enrichment", "structural_pass", "cat_b", "small_doc"),
        "PASS", "image_enrichment_promoted",
    ),
    Case(
        "flat_clean_no_image",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose", None, None, _OK,
        ("structural_pass", "cat_b", "small_doc"), "PASS", "structural_pass",
    ),
    Case(
        "flat_structblocked_image_cat_b_small",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose", 0.9, None, _NCL,
        ("image_enrichment", "cat_b", "small_doc"),
        "PASS", "image_enrichment_promoted",
    ),
    Case(
        "flat_structblocked_no_image",
        _sig(max_leaf_ratio=0.10, node_count=4),
        "flat_prose", None, None, _NCL,
        ("cat_b", "small_doc"), "PASS", "cat_b_promoted",
    ),
    Case(
        "flat_ratio_0_18_structblocked",
        _sig(max_leaf_ratio=0.18, node_count=4),
        "flat_prose", None, None, _DL,
        ("small_doc",), "PASS", "small_doc_promoted",
    ),
    Case(
        "flat_ratio_0_25_structblocked",
        _sig(max_leaf_ratio=0.25, node_count=4),
        "flat_prose", None, None, _DL,
        ("small_doc",), "PASS", "small_doc_promoted",
    ),
    Case(
        "flat_ratio_0_25_image",
        _sig(max_leaf_ratio=0.25, node_count=4),
        "flat_mixed", 0.95, None, _DL,
        ("image_enrichment", "small_doc"), "PASS", "image_enrichment_promoted",
    ),
    Case(
        "flat_shorttext_structblocked",
        _sig(max_leaf_ratio=0.10, node_count=4, flat_text=TEXT_SHORT),
        "flat_prose", None, None, _DL,
        ("small_doc",), "PASS", "small_doc_promoted",
    ),
    Case(
        # node_count > 10 takes the doc out of the small_doc window, and the
        # short text keeps it out of cat_b: nothing promotes.
        "flat_bignode_shorttext_structblocked",
        _sig(max_leaf_ratio=0.10, node_count=20, flat_text=TEXT_SHORT),
        "flat_prose", None, None, _DL,
        (), "MARGINAL", "leaf_concentration=0.10",
    ),
    Case(
        "small_doc_only",
        _sig(max_leaf_ratio=0.19, node_count=4, flat_text=TEXT_SHORT),
        "flat_prose", None, None, _DL,
        ("small_doc",), "PASS", "small_doc_promoted",
    ),
    Case(
        "small_doc_nodecount_8",
        _sig(max_leaf_ratio=0.15, node_count=8, flat_text=TEXT_SHORT),
        "flat_prose", None, None, _DL,
        ("small_doc",), "PASS", "small_doc_promoted",
    ),
    Case(
        "small_doc_nodecount_12_blocked",
        _sig(max_leaf_ratio=0.15, node_count=12, flat_text=TEXT_SHORT),
        "flat_prose", None, None, _DL,
        (), "MARGINAL", "leaf_concentration=0.15",
    ),
    Case(
        # Zone-8 content-volume floor fires before any promotion path, even
        # with a fully-enriched image ratio.
        "flat_tinytext_fail_floor",
        _sig(max_leaf_ratio=0.10, node_count=4, flat_text=TEXT_TINY),
        "flat_prose", 0.9, None, _OK,
        (), "FAIL", "insufficient_content(chars=12)",
    ),
    Case(
        "flat_hardfail_ratio_no_image",
        _sig(max_leaf_ratio=0.90, node_count=4),
        "flat_prose", None, None, _OK,
        (), "FAIL", "max_leaf_ratio=0.90",
    ),
    Case(
        # D1 hard-fail exception: image-enrichment is the ONLY path that may
        # rescue a doc above hard_fail_max_leaf_ratio.
        "flat_hardfail_ratio_image",
        _sig(max_leaf_ratio=0.90, node_count=4),
        "flat_prose", 0.9, None, _OK,
        ("image_enrichment",), "PASS", "image_enrichment_promoted",
    ),
    Case(
        # RFC-040 D1 garble guard on image-enrichment.
        "flat_garbled_with_images",
        _sig(max_leaf_ratio=0.10, node_count=4, effectively_garbled=True, garble_ratio=0.5),
        "flat_prose", 0.9, None, _OK,
        (), "MARGINAL", "garbling(ratio=0.50)",
    ),
    Case(
        "marginal_nothing",
        _sig(max_leaf_ratio=0.55, node_count=4),
        "flat_prose", None, None, _DL,
        (), "MARGINAL", "leaf_concentration=0.55",
    ),
    Case(
        # _clamp_pass caps a structural PASS whose depth is inadequate; the
        # match set still records every path that fired.
        "depth_inadequate_clamp",
        _sig(max_leaf_ratio=0.10, depth=1, expected_min_depth=3),
        "flat_prose", None, None, _OK,
        ("structural_pass", "cat_b", "small_doc"),
        "MARGINAL", "depth_inadequate:expected_min_depth=3,actual_depth=1",
    ),
    # --- generic (neither ocr_* nor flat_*): cat_c ------------------------
    Case(
        "generic_cat_c",
        _sig(max_leaf_ratio=0.10),
        "", None, None, _NCL,
        ("cat_c",), "PASS", "cat_c_promoted",
    ),
    Case(
        "generic_cat_c_textbased",
        _sig(max_leaf_ratio=0.19),
        "", None, "text_based", _NCL,
        ("cat_c",), "PASS", "cat_c_promoted",
    ),
    Case(
        "generic_cat_c_structpass",
        _sig(max_leaf_ratio=0.10),
        "", None, None, _OK,
        ("structural_pass", "cat_c"), "PASS", "structural_pass",
    ),
    Case(
        "generic_above_cat_c",
        _sig(max_leaf_ratio=0.25),
        "", None, None, _NCL,
        (), "MARGINAL", "leaf_concentration=0.25",
    ),
    Case(
        "generic_hashpipe_blocked",
        _sig(max_leaf_ratio=0.10, flat_text=TEXT_HASHY),
        "", None, None, _NCL,
        (), "MARGINAL", "leaf_concentration=0.10",
    ),
    # --- image_standalone short-circuit (never records promotion paths) -----
    Case(
        "image_standalone_high",
        _sig(),
        "image_standalone", 0.9, None, _OK,
        (), "PASS", "image_enrichment_complete",
    ),
    Case(
        "image_standalone_partial",
        _sig(),
        "image_standalone", 0.4, None, _OK,
        (), "MARGINAL", "image_enrichment_partial(ratio=0.40)",
    ),
    Case(
        "image_standalone_none",
        _sig(),
        "image_standalone", 0.0, None, _OK,
        (), "FAIL", "no_image_enrichment",
    ),
]


# ===========================================================================
# 1. Golden partition table (characterization)
# ===========================================================================


class TestGoldenPartitionTable:
    def test_table_is_broad_enough(self):
        """The table must stay a partition survey, not a handful of spots."""
        assert len(GOLDEN_TABLE) >= 30
        assert len({c.name for c in GOLDEN_TABLE}) == len(GOLDEN_TABLE)

    @pytest.mark.parametrize("case", GOLDEN_TABLE, ids=lambda c: c.name)
    def test_exact_paths_winner_and_verdict(self, case: Case):
        result = _run(case)
        assert tuple(result.promotion_paths_matched) == case.paths, (
            f"{case.name}: promotion_paths_matched drifted"
        )
        assert result.verdict == case.verdict, f"{case.name}: verdict drifted"
        assert result.reason == case.reason, f"{case.name}: reason drifted"

    @pytest.mark.parametrize("case", GOLDEN_TABLE, ids=lambda c: c.name)
    def test_winner_is_first_match(self, case: Case):
        """``promotion_paths_matched[0]`` is always the winner: a non-empty
        match set implies a promotion reason, an empty one implies a
        FAIL/MARGINAL fallback or the image_standalone short-circuit."""
        result = _run(case)
        if result.promotion_paths_matched:
            winner = result.promotion_paths_matched[0]
            expected_reason = {
                "image_enrichment": "image_enrichment_promoted",
                "structural_pass": "structural_pass",
                "cat_a": "cat_a_promoted",
                "cat_b": "cat_b_promoted",
                "cat_c": "cat_c_promoted",
                "small_doc": "small_doc_promoted",
            }[winner]
            # The reason is the winner's, unless _clamp_pass overrode it.
            assert result.reason == expected_reason or result.verdict == "MARGINAL"
        elif case.content_class == "image_standalone":
            # The image_standalone short-circuit returns before the promotion
            # pipeline, so it never records a match set even when it PASSes.
            assert result.reason.startswith(("image_enrichment", "no_image_enrichment"))
        else:
            assert result.verdict in ("FAIL", "MARGINAL")

    def test_every_promotion_path_appears_somewhere(self):
        """Exhaustiveness: all six paths are exercised by the table."""
        seen: set[str] = set()
        for case in GOLDEN_TABLE:
            seen.update(_run(case).promotion_paths_matched)
        assert seen == {
            "image_enrichment",
            "structural_pass",
            "cat_a",
            "cat_b",
            "cat_c",
            "small_doc",
        }

    def test_paths_are_recorded_in_pipeline_order(self):
        """The recorded set is always a subsequence of the canonical order."""
        canonical = [
            "image_enrichment",
            "structural_pass",
            "cat_a",
            "cat_b",
            "cat_c",
            "small_doc",
        ]
        for case in GOLDEN_TABLE:
            paths = list(_run(case).promotion_paths_matched)
            idx = [canonical.index(p) for p in paths]
            assert idx == sorted(idx), f"{case.name}: {paths} out of pipeline order"


# ===========================================================================
# 2. Verified overlap clusters
# ===========================================================================


class TestOverlapClusters:
    """Both clusters the zone audit verified as real, asserted directly."""

    def _paths(self, name: str) -> tuple[str, ...]:
        case = next(c for c in GOLDEN_TABLE if c.name == name)
        return tuple(_run(case).promotion_paths_matched)

    @pytest.mark.parametrize(
        "row,shadowed",
        [
            ("ocr_clean_low_ratio", "cat_a"),
            ("flat_clean_no_image", "cat_b"),
            ("flat_clean_no_image", "small_doc"),
            ("generic_cat_c_structpass", "cat_c"),
        ],
    )
    def test_structural_pass_shadows(self, row: str, shadowed: str):
        """Cluster 1: structural_pass wins over every path below it."""
        paths = self._paths(row)
        assert paths[0] == "structural_pass"
        assert shadowed in paths[1:]

    def test_structural_pass_is_itself_shadowed_by_image_enrichment(self):
        """Cluster 1, the one direction that inverts: image_enrichment is
        evaluated first, so structural_pass is the shadowed path here."""
        paths = self._paths("flat_clean_all_paths")
        assert paths[0] == "image_enrichment"
        assert "structural_pass" in paths[1:]

    def test_image_enrichment_cat_b_small_doc_all_compete_on_flat(self):
        """Cluster 2: on a flat_* doc all three fire; image_enrichment wins."""
        paths = self._paths("flat_structblocked_image_cat_b_small")
        assert paths == ("image_enrichment", "cat_b", "small_doc")

    def test_cat_b_shadows_small_doc_when_image_absent(self):
        paths = self._paths("flat_structblocked_no_image")
        assert paths == ("cat_b", "small_doc")

    def test_small_doc_alone_when_cat_b_char_floor_unmet(self):
        paths = self._paths("flat_shorttext_structblocked")
        assert paths == ("small_doc",)


# ===========================================================================
# 3. VG-1 regression: garbled ocr_* must not promote via cat_a
# ===========================================================================


class TestVG1GarbleGuardOnCatA:
    """Regression: before VG-1, ``_try_cat_a`` was the only promotion path
    without an ``effectively_garbled`` guard, so a garbled ``ocr_*`` doc with
    a low leaf ratio and low OCR noise promoted straight to PASS — a direct
    CLAUDE.md HR#5 violation."""

    def _garbled_sig(self) -> TreeSignals:
        return _sig(max_leaf_ratio=0.10, effectively_garbled=True, garble_ratio=0.4)

    def test_try_cat_a_returns_none_when_garbled(self):
        from pageindex_mcp.helpers.garble import ocr_noise_ratio

        sig = self._garbled_sig()
        th = _th()
        # Preconditions: this vector satisfies every OTHER cat_a condition.
        assert sig.max_leaf_ratio < th.cat_a_max_leaf_ratio
        assert ocr_noise_ratio(sig.flat_text) < th.cat_a_max_ocr_noise
        assert _try_cat_a(sig, "ocr_scanned", th) is None

    def test_ungarbled_twin_does_promote(self):
        """Control: the identical vector with effectively_garbled=False
        promotes — proving the garble flag alone is what blocks it."""
        clean = _sig(max_leaf_ratio=0.10, effectively_garbled=False)
        assert _try_cat_a(clean, "ocr_scanned", _th()) == "cat_a_promoted"

    def test_apply_promotions_records_no_cat_a_match(self):
        result = apply_promotions(
            _outcome(self._garbled_sig(), _DL),
            "ocr_scanned", None, None, _th(), None,
        )
        assert "cat_a" not in result.promotion_paths_matched
        assert result.verdict != "PASS"

    def test_alias_shares_the_guard(self):
        """``_try_ocr_promotion`` is the RFC-facing alias of ``_try_cat_a``."""
        assert _try_ocr_promotion is _try_cat_a
        assert _try_ocr_promotion(self._garbled_sig(), "ocr_scanned", _th()) is None

    @pytest.mark.parametrize("ratio", [0.0, 0.05, 0.10, 0.14])
    def test_no_leaf_ratio_below_the_bound_rescues_a_garbled_doc(self, ratio: float):
        sig = _sig(max_leaf_ratio=ratio, effectively_garbled=True, garble_ratio=0.4)
        assert _try_cat_a(sig, "ocr_scanned", _th()) is None


# ===========================================================================
# 4. VG-6 behavior identity vs. the pre-change short-circuit pipeline
# ===========================================================================


def _reference_apply_promotions(outcome: GateOutcome, content_class, ier, insp, th):
    """Faithful model of the PRE-VG-6 ordered if/elif pipeline.

    Same guards, same order, but short-circuits at the first match and has no
    ``promotion_paths_matched``.  Its ``(verdict, reason)`` must equal the
    evaluate-all implementation's for every row of the golden table — that is
    exactly the claim VG-6 makes.
    """
    defect = outcome.defect
    sig = outcome.signals
    all_defects = outcome.all_defects

    if content_class == "image_standalone":
        return _classify_image_verdict(ier)

    stripped_len = len(sig.flat_text.strip())
    if stripped_len < th.min_marginal_chars:
        return "FAIL", f"insufficient_content(chars={stripped_len})"

    if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
        ie = _try_image_enrichment(sig, content_class, ier, th, None, None)
        if ie is not None:
            return _clamp_pass(ie, defect=defect, sig=sig)
        return "FAIL", f"max_leaf_ratio={sig.max_leaf_ratio:.2f}"

    reason = _try_image_enrichment(sig, content_class, ier, th, None, None)
    if reason is None:
        reason = _try_structural_pass(sig, all_defects, th)
    if reason is None:
        reason = _try_ocr_promotion(sig, content_class, th)
    if reason is None:
        reason = _try_flat_promotion(sig, content_class, th)
    if reason is None:
        reason = _try_content_class_promotion(sig, content_class, insp, th)
    if reason is None:
        reason = _try_small_doc_promotion(sig, content_class, th)
    if reason is not None:
        return _clamp_pass(reason, defect=defect, sig=sig)

    if sig.effectively_garbled:
        return "MARGINAL", f"garbling(ratio={sig.garble_ratio:.2f})"
    if sig.node_count < 3:
        return "MARGINAL", f"node_count={sig.node_count}"
    if sig.depth < 2:
        return "MARGINAL", f"depth={sig.depth}"
    return "MARGINAL", f"leaf_concentration={sig.max_leaf_ratio:.2f}"


class TestVG6BehaviorIdentity:
    @pytest.mark.parametrize("case", GOLDEN_TABLE, ids=lambda c: c.name)
    def test_verdict_and_reason_unchanged_by_evaluate_all(self, case: Case):
        ref_verdict, ref_reason = _reference_apply_promotions(
            _outcome(case.sig, case.all_defects),
            case.content_class,
            case.image_enrichment_ratio,
            case.inspector_class,
            _th(),
        )
        result = _run(case)
        assert (result.verdict, result.reason) == (ref_verdict, ref_reason), (
            f"{case.name}: VG-6 changed observable behavior, not just telemetry"
        )

    def test_only_new_observable_is_promotion_paths_matched(self):
        """The reference model exposes no match set; the new field is the
        entire delta, and it never contradicts the winner."""
        for case in GOLDEN_TABLE:
            result = _run(case)
            if result.promotion_paths_matched:
                assert result.verdict in ("PASS", "MARGINAL")


# ===========================================================================
# 5. VG-6 unpacking compatibility of the widened VerdictResult
# ===========================================================================


class TestVerdictResultUnpackingCompat:
    """``promotion_paths_matched`` must stay out of ``__iter__`` so that every
    existing ``verdict, reason = compute_verdict(...)`` call site keeps
    working."""

    _TREE = [
        {
            "title": "Kapitel 1",
            "text": PROSE * 6,
            "nodes": [
                {"title": "1.1", "text": PROSE * 6},
                {"title": "1.2", "text": PROSE * 6},
            ],
        },
        {
            "title": "Kapitel 2",
            "text": PROSE * 6,
            "nodes": [{"title": "2.1", "text": PROSE * 6}],
        },
    ]

    def test_two_tuple_unpacking_still_works(self):
        verdict, reason = compute_verdict(self._TREE, "flat_prose")
        assert isinstance(verdict, str)
        assert isinstance(reason, str)

    def test_unpacking_more_than_two_raises(self):
        """__iter__ yields exactly two items — no silent third element."""
        with pytest.raises(ValueError):
            _a, _b, _c = compute_verdict(self._TREE, "flat_prose")  # noqa: F841

    def test_list_of_result_is_two_items(self):
        assert len(list(compute_verdict(self._TREE, "flat_prose"))) == 2

    def test_field_reachable_by_attribute_not_by_iteration(self):
        result = compute_verdict(self._TREE, "flat_prose")
        assert isinstance(result.promotion_paths_matched, tuple)
        assert result.promotion_paths_matched not in list(result)

    def test_default_is_empty_tuple(self):
        from pageindex_mcp.helpers.types import VerdictResult

        assert VerdictResult("PASS", "x").promotion_paths_matched == ()

    def test_unpacking_holds_across_the_whole_golden_table(self):
        for case in GOLDEN_TABLE:
            verdict, reason = _run(case)
            result = _run(case)
            assert (verdict, reason) == (result.verdict, result.reason)
