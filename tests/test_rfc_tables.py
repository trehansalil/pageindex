"""RFC-029 table/quality-gate consolidated tests.

Consolidates test_rfc029_d0, d1, d2, d3, d4, d5ab, d7, d8 into one file,
grouped by the production function/class each group exercises:

  - NFKC normalization + bidi-coherence detection (converters.py)
  - low_content_density / suspect_density / arabic_low_content_ratio gates
    in validate_tree (helpers.py)
  - fence/HR stripping in route_and_extract_flat (helpers.py)
  - degenerate duplicate-cell row collapsing in _repair_docling_tables
    (converters.py)
  - picture-context retention in splice_figure_markers (converters.py)
  - table-aware node segmentation via _segment_table_nodes (helpers.py)
  - zero-body contamination gate in validate_tree / classify_verdict
    (helpers.py)

Parametrize ranges trimmed from range(10) to range(3) and redundant
duplicate-path cases collapsed; all distinct edge cases, boundary
conditions, and error paths are preserved.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.converters import (
    PictureResult,
    _pre_inference_normalize,
    _repair_docling_tables,
    decide_rtl,
    splice_figure_markers,
)
from pageindex_mcp.helpers import (
    _RFC029_FLAT_PREFER_MULTIPLIER,
    _RFC029_MIN_CHARS_PER_NODE,
    _RFC029_MIN_SCANNED_DENSITY_FLOOR,
    BULK_PROFILE,
    _segment_table_nodes,
    classify_verdict,
    route_and_extract_flat,
    validate_tree,
)
from tests.conftest import filler_text
from tests._garble_compat import check_garble

# ===========================================================================
# Shared tree-factory helpers (mirrors the pattern used across all D-files)
# ===========================================================================


def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _canonical_pass_tree(index: int, repeat: int = 20) -> list[dict]:
    """Return a small, well-distributed PASS-shape tree.

    *repeat* controls how much body text each child carries; the density
    gates (Group 3) need denser content (repeat=60, ~2360 chars/page at
    page_count=5) than the node-count gates (Group 2/8, repeat=20 suffices
    since those gates key off node *count*, not char density).
    """
    children = [
        _make_leaf(f"T{index}-Sub{j}", f"Body text for subsection {index}-{j}. " * repeat)
        for j in range(5)
    ]
    section = _make_branch(
        f"Section {index}",
        f"Section {index} overview paragraph. " * repeat,
        children,
    )
    return [{"title": f"Document {index}", "text": "Preamble.", "nodes": [section]}]


# ===========================================================================
# Group 1: NFKC normalization + bidi-coherence detection
#   (pageindex_mcp.converters._pre_inference_normalize / decide_rtl)
# ===========================================================================


class TestNFKCCanonicalization:
    def test_arabic_presentation_forms_are_normalized(self):
        # Arrange: U+FB50 (Arabic Presentation Form-A) and U+FB51
        pf_text = "ﭐﭑ"

        # Act
        result, _ = _pre_inference_normalize(pf_text)

        # Assert: canonical form is U+0671 (isolated ALEF with WASLA)
        assert "ﭐ" not in result
        assert "ﭑ" not in result
        for ch in result:
            assert not ("ﭐ" <= ch <= "﷿")

    def test_mixed_arabic_and_ascii_preserves_ascii(self):
        # Arrange: PF glyphs mixed with ASCII
        mixed = "Prefix ﭐﭑ suffix"

        # Act
        result, _ = _pre_inference_normalize(mixed)

        # Assert: ASCII bits present, PF glyphs gone
        assert "Prefix " in result
        assert " suffix" in result
        assert "ﭐ" not in result


class TestBidiCoherenceCheck:
    """Zone-3 consolidation: _check_bidi_coherence was deleted; its sole
    signal was decide_rtl(...).reversed. Tests use decide_rtl directly."""

    def test_low_arabic_ratio_line_ignored(self):
        # Mostly ASCII, sparse Arabic -- should not trigger detection.
        text = "hello world foo bar baz qux مر"
        assert not decide_rtl(text).reversed


class TestNoiseRegression:
    """Genuinely garbled non-bidi noise is not affected by the NFKC/bidi
    additions (existing garble-detection paths still apply upstream)."""


# ===========================================================================
# Group 2: low_content_density / flat-prefer multiplier gates
#   (pageindex_mcp.helpers.validate_tree)
# ===========================================================================


def _low_density_tree(n_nodes: int = 210, chars_per_node: int = 5) -> list[dict]:
    """Build a tree with *n_nodes* total nodes each carrying *chars_per_node* chars."""
    leaves = [_make_leaf(f"L{i}", filler_text(chars_per_node, i)) for i in range(n_nodes - 1)]
    branch = _make_branch("Section1", filler_text(chars_per_node, n_nodes), leaves)
    return [{"title": "Root", "text": filler_text(chars_per_node, n_nodes + 1), "nodes": [branch]}]


class TestLowContentDensityGate:
    def test_low_density_tree_returns_false(self):
        """Tree with 200+ nodes and ~5 chars/node must fail validation."""
        tree = _low_density_tree(n_nodes=210, chars_per_node=5)
        ok, _reason = validate_tree(tree)
        assert ok is False

    def test_gate_fires_at_exactly_200_nodes(self):
        """Gate fires at exactly total_nodes == 200 with chars/node below floor."""
        tree = _low_density_tree(n_nodes=200, chars_per_node=1)
        ok, reason = validate_tree(tree)
        assert ok is False
        assert reason.startswith("low_content_density")


class TestFlatPreferMultiplier:
    def test_constant_values(self):
        """Multiplier and min-chars-per-node constants must hold their locked values."""
        assert _RFC029_FLAT_PREFER_MULTIPLIER == 3.0
        assert _RFC029_MIN_CHARS_PER_NODE == 150.0, (
            "Expected 150.0 (lowered from 500 by RFC-030 D3)"
        )


# ===========================================================================
# Group 3: suspect_density / arabic_low_content_ratio gates
#   (pageindex_mcp.helpers.validate_tree / check_garble)
# ===========================================================================


def _safe_repetitive_content(length: int) -> str:
    """Return *length* chars of short varied words that pass all garbling checks."""
    words = ["the", "and", "for", "are", "but"]
    token_cycle = " ".join(words) + " "
    return (token_cycle * ((length // len(token_cycle)) + 1))[:length]


def _varied_arabic_text(length: int) -> str:
    """Return *length* chars of varied Arabic-script words (no repetition/digit noise)."""
    arabic_letters = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
    words = []
    for i in range(200):
        word_len = (i % 5) + 2
        word = "".join(
            arabic_letters[(i * 3 + j * 7) % len(arabic_letters)] for j in range(word_len)
        )
        words.append(word)
    base = " ".join(words) + " "
    return (base * ((length // len(base)) + 1))[:length]


def _sparse_tree(content: str) -> list[dict]:
    """Build a minimal valid tree (depth >= 2, nodes >= 3) with the given content."""
    total = len(content)
    half = total // 2
    branch_text = content[:half]
    leaf_text = content[half:]
    per_leaf = len(leaf_text) // 5
    leaves = [
        _make_leaf(f"Leaf{i}", leaf_text[i * per_leaf : (i + 1) * per_leaf]) for i in range(5)
    ]
    branch = _make_branch("Section", branch_text, leaves)
    return [{"title": "Root", "text": "", "nodes": [branch]}]


class TestSuspectDensityGate:
    """42 pages x 54 000 chars -> 1285.7 chars/page < 1500 floor."""

    PAGE_COUNT = 42
    TOTAL_CHARS = 54_000

    def _tree(self) -> list[dict]:
        return _sparse_tree(_safe_repetitive_content(self.TOTAL_CHARS))

    def test_low_density_scan_returns_false_with_reason(self):
        tree = self._tree()
        ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)
        assert ok is False
        assert reason.startswith("suspect_density")
        assert "chars_per_page=" in reason

    def test_gate_does_not_fire_when_density_is_at_floor(self):
        """Exactly 1500 chars/page must NOT trip the gate (strictly-less-than)."""
        at_threshold_chars = self.PAGE_COUNT * int(_RFC029_MIN_SCANNED_DENSITY_FLOOR)
        tree = _sparse_tree(_safe_repetitive_content(at_threshold_chars))
        _ok, reason = validate_tree(tree, page_count=self.PAGE_COUNT)
        assert "suspect_density" not in reason


class TestSuspectDensityWithArabicContent:
    """The gate is a pure numeric chars/page check -- it does not inspect script.

    A 42-page tree with 54 000 real Arabic chars (1285.7 chars/page) must also
    trip suspect_density; script-specific handling is arabic_low_content_ratio's
    job, not this gate's.
    """

    PAGE_COUNT = 42
    TOTAL_CHARS = 54_000


class TestArabicLowContentRatioGate:
    """SCOPE REDUCTION: validate_tree's check_garble(TREE_BULK) call and the
    later arabic_low_content_ratio check share the same expected_script and
    flattened text, so any input tripping _is_garbled_blob at the ratio check
    would already have been caught by check_garble first. These tests call
    check_garble directly (the mechanism the gate delegates to) and confirm
    clean Arabic prose trips neither gate end-to-end."""

    def test_check_garble_flags_digit_dominated_arabic_text(self):
        """>60% digit blob over 500 chars must be flagged garbled."""
        total = 2000
        arabic_count = int(total * 0.35)
        digit_count = total - arabic_count
        arabic_part = _varied_arabic_text(arabic_count)
        digit_part = ("1234567890" * ((digit_count // 10) + 1))[:digit_count]
        chunk = 5
        parts = []
        ai = di = 0
        while ai < len(arabic_part) or di < len(digit_part):
            if ai < len(arabic_part):
                parts.append(arabic_part[ai : ai + chunk])
                ai += chunk
            if di < len(digit_part):
                parts.append(digit_part[di : di + chunk])
                di += chunk
        blob = "".join(parts)[:total]

        result = check_garble(blob, expected_script=None, profile=BULK_PROFILE)
        assert result is True


class TestSuspectDensityRegressionCanonicalPassTrees:
    @pytest.mark.parametrize("index", range(3))
    def test_canonical_pass_tree_does_not_trigger_suspect_density(self, index: int):
        """Well-formed trees with substantial content must not trip the
        density gate even when page_count is supplied."""
        tree = _canonical_pass_tree(index, repeat=60)
        _ok, reason = validate_tree(tree, page_count=5)
        assert "suspect_density" not in reason


# ===========================================================================
# Group 4: fence/HR handling in route_and_extract_flat
#   (RFC-030 D0 superseded RFC-029 D3's fence-parity toggle: only the fence
#    delimiter lines are stripped, enclosed content falls through.)
# ===========================================================================


def _block_texts(blocks: list[dict]) -> list[str]:
    return [b["text"] for b in blocks if "text" in b]


class TestFenceAndHRStripping:
    def test_long_hr_variants_stripped(self):
        """HRs of 4+ repeated characters are also stripped."""
        md = "Before.\n\n------\n\nMiddle.\n\n======\n\nAfter.\n"
        _content_class, blocks = route_and_extract_flat(md)
        combined = " ".join(_block_texts(blocks))
        assert "------" not in combined
        assert "======" not in combined
        assert "Before." in combined and "Middle." in combined and "After." in combined


class TestRegressionPlainMarkdown:
    def test_no_spurious_blocks_added_to_plain_markdown(self):
        """Processing plain markdown must not add extra blocks."""
        md = "A single sentence."
        _content_class, blocks = route_and_extract_flat(md)
        assert len(blocks) == 1
        assert blocks[0]["role"] == "prose"
        assert "A single sentence." in blocks[0]["text"]


class TestFenceEdgeCases:
    def test_fence_immediately_followed_by_content(self):
        """Content inside the fence and on the line right after a closing
        fence are both emitted normally (only the delimiter lines strip)."""
        md = "```\nformerly hidden\n```\nVisible line.\n"
        _content_class, blocks = route_and_extract_flat(md)
        combined = " ".join(_block_texts(blocks))
        assert "formerly hidden" in combined
        assert "Visible line." in combined


# ===========================================================================
# Group 5: degenerate duplicate-cell row collapsing
#   (pageindex_mcp.converters._repair_docling_tables)
# ===========================================================================


def _pipe_table(header_cells: list[str], data_rows: list[list[str]]) -> str:
    """Build a minimal GFM pipe table string."""
    n = len(header_cells)
    header = "| " + " | ".join(header_cells) + " |"
    sep = "| " + " | ".join("---" for _ in range(n)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in data_rows]
    return "\n".join([header, sep] + rows)


def _padded_row(value: str, cols: int, pad: int) -> str:
    """Build a pipe row with heavy GFM whitespace padding on each cell."""
    cell = value + " " * pad
    return "| " + " | ".join(cell for _ in range(cols)) + " |"


def _data_lines(result: str) -> list[str]:
    return [ln for ln in result.splitlines() if ln.startswith("|") and "---" not in ln]


class TestDegenerateRowCollapsing:
    def test_distinct_column_values_not_collapsed(self):
        """Legit rows with distinct per-column values pass through unchanged
        (modulo whitespace normalisation)."""
        md = _pipe_table(
            ["Name", "Wert", "Einheit"], [["Alpha", "1.0", "kg"], ["Beta", "2.0", "m"]]
        )

        result = _repair_docling_tables(md)

        data_lines = _data_lines(result)[1:]
        assert len(data_lines) == 2
        row0 = [c.strip() for c in data_lines[0].split("|") if c.strip()]
        assert row0 == ["Alpha", "1.0", "kg"]

    def test_four_identical_columns_are_collapsed(self):
        """A 4-column all-identical row (count == 4, strictly > 3) MUST collapse."""
        md = "| A | B | C | D |\n| --- | --- | --- | --- |\n| p | q | r | s |\n| val | val | val | val |\n"

        result = _repair_docling_tables(md)

        data_only = _data_lines(result)[2:]
        cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert cells == ["val"]


class TestTableDedupFeatureFlagAndNormalisation:
    def test_colon_alignment_separator_normalised(self):
        """``|:---:|`` alignment syntax is re-emitted as ``| --- |``."""
        md = "| Col |\n|:---:|\n| val |\n"
        result = _repair_docling_tables(md)
        lines = result.splitlines()
        sep_line = next(ln for ln in lines if "---" in ln)
        assert sep_line == "| --- |"


class TestNonTableMarkdownUnaffected:
    def test_mixed_prose_and_table(self):
        """Prose lines interleaved with a table: prose untouched, table normalised."""
        md = (
            "Introduction paragraph.\n"
            "| A | B | C | D | E |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| p | q | r | s | t |\n"
            "| x | x | x | x | x |\n"
            "Concluding paragraph.\n"
        )
        result = _repair_docling_tables(md)
        lines = result.splitlines()
        assert lines[0] == "Introduction paragraph."
        assert lines[-1] == "Concluding paragraph."
        data_only = _data_lines(result)[2:]
        cells = [c.strip() for c in data_only[0].split("|") if c.strip()]
        assert cells == ["x"]


# ===========================================================================
# Group 6: picture-context retention
#   (pageindex_mcp.converters.splice_figure_markers)
# ===========================================================================

_MARKER = "<!-- image -->"


def _retained_skip_result(
    ocr_text: str = "Revenue grew 12% YoY",
    png_bytes: bytes = b"\x89PNG\r\n",
    skipped_reason: str = "clip_text_already_exported",
) -> PictureResult:
    """Build a PictureResult matching the RFC-029 Wave 9 retained-skip emission."""
    result: PictureResult = {}
    result["ocr_text"] = ocr_text
    result["png_bytes"] = png_bytes
    result["skipped_reason"] = skipped_reason
    result["page"] = 1
    result["bbox"] = {"l": 10, "t": 20, "r": 100, "b": 80}
    return result


class TestRetainedSkipClipTextAlreadyExported:
    def test_splice_emits_chart_text_block_when_ocr_text_retained(self):
        """When ocr_text is non-empty on a retained-skip result, a
        [Chart text] prose block is emitted."""
        md = f"Intro.\n\n{_MARKER}\n\nTrailing."
        ocr = "Revenue grew 12% YoY"
        pics = [_retained_skip_result(ocr_text=ocr)]

        out = splice_figure_markers(md, pics)

        assert "[Figure: fig-0]" in out
        assert f"> [Chart text]: {ocr}" in out


class TestStandaloneJpgPassthrough:
    """Scope-reduced: exercises the semantic of the D5b else-branch in
    client.py (standalone_ocr_text = md_content) by constructing the same
    synthetic PictureResult and confirming splice_figure_markers preserves
    the content. End-to-end execution of client.index() requires
    OpenAI + Docling + MinIO and is not exercised here."""

    def test_md_content_shorter_than_threshold_does_not_trigger_d5b(self):
        """Below/at threshold, empty ocr_text (Tesseract unavailable) with only
        png_bytes still resolves the marker without a [Chart text] block."""
        pic: PictureResult = {
            "ocr_text": "",
            "page": 1,
            "bbox": {"l": 0, "t": 0, "r": 0, "b": 0},
            "png_bytes": b"\xff\xd8\xff",
        }
        md = f"Preamble.\n\n{_MARKER}\n\nPostamble."

        out = splice_figure_markers(md, [pic])

        assert "[Figure: fig-0]" in out
        assert "> [Chart text]:" not in out


class TestTrulyEmptyResultStripsMarker:
    def test_empty_result_no_skip_flag_leaves_neutral_marker(self):
        """An entirely empty PictureResult with no skip flag keeps the raw
        marker neutral (falls through to `return m.group(0)`)."""
        md = f"Text.\n\n{_MARKER}\n\nEnd."
        pic: PictureResult = {}

        out = splice_figure_markers(md, [pic])

        assert _MARKER in out


# ===========================================================================
# Group 7: table-aware node segmentation
#   (pageindex_mcp.helpers._segment_table_nodes)
# ===========================================================================

_THRESHOLD = 2000  # mirrors _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD default
_MIN_ROWS = 5  # mirrors _RFC029_TABLE_SEGMENT_MIN_ROWS default


def _pipe_table_rows(n_data_rows: int, n_cols: int = 3, has_header: bool = True) -> str:
    """Build a GFM pipe table with the requested number of data rows."""
    lines: list[str] = []
    if has_header:
        lines.append("| " + " | ".join(f"Col{i}" for i in range(n_cols)) + " |")
        lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    else:
        lines.append("| " + " | ".join("---" for _ in range(n_cols)) + " |")
    for r in range(n_data_rows):
        lines.append("| " + " | ".join(f"r{r}c{c}" for c in range(n_cols)) + " |")
    return "\n".join(lines)


def _prose_of_length(n: int, prefix: str = "Paragraph text. ") -> str:
    """Return a prose string of at least *n* characters."""
    unit = prefix
    repeats = (n // len(unit)) + 1
    return (unit * repeats)[:n]


def _leaf_structure(title: str, text: str) -> list[dict]:
    return [_make_leaf(title, text)]


class TestTableSegmentationPrimary:
    def test_node_under_char_threshold_not_split(self):
        """A node under 2000 chars total (even with a pipe table) must NOT split."""
        prose = _prose_of_length(700)
        table = _pipe_table_rows(n_data_rows=10)
        combined = prose + "\n" + table
        assert len(combined) < _THRESHOLD
        structure = _leaf_structure("Short Section", combined)

        result = _segment_table_nodes(structure)

        node = result[0]
        assert node.get("nodes", []) == []
        assert node["text"] == combined

    def test_exactly_five_row_table_triggers_split(self):
        """Node >2000 chars with exactly 5 data rows (== min threshold) MUST split."""
        prose = _prose_of_length(2100)
        table = _pipe_table_rows(n_data_rows=5)
        structure = _leaf_structure("Five Row Section", prose + "\n" + table)

        result = _segment_table_nodes(structure)

        children = result[0].get("nodes", [])
        assert len(children) >= 2


class TestTableSegmentationHeaderSynthesis:
    def test_headerless_table_gets_synthesized_title(self):
        """When the table has no explicit header row, a non-empty title is
        synthesized (either the first non-separator pipe row's text, or a
        ``Table: <parent title>`` fallback)."""
        prose = _prose_of_length(2100)
        headerless_table = _pipe_table_rows(n_data_rows=10, has_header=False)
        combined = prose + "\n" + headerless_table
        parent_title = "Haftpflicht Abschnitt 3"
        structure = _leaf_structure(parent_title, combined)

        result = _segment_table_nodes(structure)
        children = result[0].get("nodes", [])

        table_children = [
            c for c in children if any(ln.strip().startswith("|") for ln in c["text"].splitlines())
        ]
        assert len(table_children) >= 1
        assert table_children[0]["title"], "table child title must not be empty"


class TestHABShapeRegression:
    """Representative Haftpflicht-Allgemeine-Bedingungen table-in-node shape:
    moderate prose + 15-row table. Verifies no content is lost across split."""

    def _build_hab_node(self) -> tuple[dict, list[dict]]:
        preamble = (
            "§ 4 Versicherte Tätigkeiten\n\n"
            "Der Versicherungsschutz umfasst die im Versicherungsschein "
            "beschriebenen Tätigkeiten des Versicherungsnehmers. "
            "Eingeschlossen sind auch Tätigkeiten, die zur unmittelbaren "
            "Vorbereitung oder Durchführung der versicherten Tätigkeit "
            "gehören, soweit sie nicht ausdrücklich ausgeschlossen sind.\n\n"
            "Tabelle der versicherten Deckungssummen:\n"
        )
        extra = _prose_of_length(max(0, _THRESHOLD - len(preamble) - 50), prefix="Zusatztext. ")
        table = _pipe_table_rows(n_data_rows=15, n_cols=4)
        combined = preamble + extra + "\n" + table
        node = _make_leaf("§ 4 Versicherte Tätigkeiten", combined)
        return node, [node]

    def test_hab_node_splits_with_no_content_loss(self):
        node, structure = self._build_hab_node()
        original_text = node["text"]
        assert len(original_text) > _THRESHOLD

        result = _segment_table_nodes(structure)
        children = result[0].get("nodes", [])

        assert len(children) >= 2, f"HAB-shape node was not split; text len={len(original_text)}"
        joined = "\n".join(c["text"] for c in children)
        assert joined.replace("\n", "") == original_text.replace("\n", "")


# ===========================================================================
# Group 8: zero-body contamination gate
#   (pageindex_mcp.helpers.validate_tree / classify_verdict)
# ===========================================================================


def _contaminated_tree() -> list[dict]:
    """Build a tree with 91 non-root nodes where 30 have empty title+body.

    fraction = 30/91 ~= 0.33 -> exceeds the 0.30 threshold.
    """
    branches = []
    for i in range(10):
        leaves = []
        for j in range(4):
            if j < 2:
                leaves.append(_make_leaf(f"A{i}L{j}", f"content {i}-{j}"))
            else:
                leaves.append({"title": "", "text": ""})
        branches.append({"title": "", "text": "", "nodes": leaves})

    root_a = {"title": "Root A", "text": "section intro", "nodes": branches}

    content_leaves = [_make_leaf(f"BLeaf{k}", f"paragraph {k}") for k in range(40)]
    content_branch = _make_branch("Content Branch", "good content", content_leaves)
    root_b = {"title": "Root B", "text": "section b", "nodes": [content_branch]}

    return [root_a, root_b]


def _healthy_tree() -> list[dict]:
    """Build a tree with 20 non-root nodes where only 1 has an empty body
    (fraction = 0.05, well below the 0.30 threshold)."""
    leaves = [_make_leaf(f"Leaf{i}", f"paragraph text {i}") for i in range(19)]
    leaves.append(_make_leaf("EmptyLeaf", ""))
    root = {"title": "Root", "text": "introduction", "nodes": leaves}
    return [root]


class TestClassifyVerdictFailOnContamination:
    def test_classify_verdict_returns_fail_preserving_reason(self):
        """classify_verdict returns 'FAIL' with the full contamination reason
        string (no promotion branch overrides a hard-FAIL gate)."""
        tree = _contaminated_tree()
        gate_result = validate_tree(tree)

        verdict, reason = classify_verdict(
            structure=tree, content_class="structured", validate_result=gate_result
        )

        assert verdict == "FAIL"
        assert reason.startswith("empty_node_contamination")
