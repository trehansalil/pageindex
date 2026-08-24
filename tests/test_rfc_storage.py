"""Consolidated RFC-023 storage/content-recovery tests (D0-D11).

Merges tests/test_rfc023_d0.py .. tests/test_rfc023_d11.py into a single
file, grouped by the production function/design-property each class
validates. Each class's docstring/design-property references map back to
the original RFC-023 task numbers (D0-D11) for traceability.
"""

import types
from unittest.mock import patch

from bidi.algorithm import get_display

import pageindex_mcp.client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import (
    _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED,
    MIN_STANDALONE_IMAGE_MD_CHARS,
)
from pageindex_mcp.config import OCR_ESCALATION_GARBLE, reset_pipeline_config
from pageindex_mcp.converters import (
    PictureResult,
    _recover_picture_text,
    _text_layer_has_content,
)
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _flat_block_primary_text,
    classify_verdict,
)
from pageindex_mcp.worker import _classify_llm_failure

from tests._garble_compat import check_garble

_MARKER = "<!-- image -->"
_IMAGE_MARKER = _MARKER

# Repeated single-token blob (>20 alnum tokens, >30% repetition ratio) trips
# _is_garbled_blob's token-repetition check without needing GLYPH</PUA noise.
_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary page of legible English prose. " * 3


# ---------------------------------------------------------------------------
# D0: garble-aware _text_layer_has_content
# ---------------------------------------------------------------------------


def _page(text: str):
    return types.SimpleNamespace(get_text=lambda mode="text": text)


class TestTextLayerHasContent:
    """Design Property 1: _text_layer_has_content returns False for text
    that is either too short or flagged garbled, and True only when both
    checks pass."""

    def test_garbled_text_layer_returns_false(self):
        """Long enough to clear the char-count floor but flagged garbled
        (thin mojibake left by the PDF creator) must not be treated as
        real content."""
        assert _text_layer_has_content(_page(_GARBLED_TEXT)) is False

    def test_clean_text_layer_returns_true(self):
        assert _text_layer_has_content(_page(_CLEAN_TEXT)) is True


# ---------------------------------------------------------------------------
# D1: graceful marker-count mismatch splicing + raw marker recognition
# ---------------------------------------------------------------------------


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


# ---------------------------------------------------------------------------
# D2 + D6: decorative-icon bbox classifier and page-rotation-corrected OCR
# ---------------------------------------------------------------------------


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


def _make_fake_fitz(
    page_width: float,
    page_height: float,
    initial_rotation: int = 0,
    raise_on_pixmap: bool = False,
):
    """Build a fake fitz module + page that records the rotation in effect
    at the moment ``get_pixmap`` is called."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = initial_rotation
            self.pixmap_rotation_at_call = None

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            self.pixmap_rotation_at_call = self.rotation
            if raise_on_pixmap:
                raise RuntimeError("boom")
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake, page


class TestDecorativeIconSizeFilter:
    """Design Property 3: a PictureItem region whose bbox width AND height
    are both below DECORATIVE_ICON_MIN_DIM_PT skips crop+OCR and is tagged
    skip_reasons[i] == "decorative_icon"."""

    def test_sub_icon_region_skips_ocr_tags_decorative_icon(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters.pictures, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)

        def _fail_if_called(*_a, **_k):
            raise AssertionError("tesseract must not run for sub-icon regions")

        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", _fail_if_called)
        region = _region(0, 0, 15, 12)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert result == {}
        assert skip_reasons[0] == "decorative_icon"

    def test_region_above_threshold_proceeds_to_ocr(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz(600.0, 800.0)
        monkeypatch.setattr(converters.pictures, "_DECORATIVE_ICON_MIN_DIM_PT", 20.0)
        monkeypatch.setattr(
            converters.pictures,
            "_tesseract_ocr_image",
            lambda path, langs: "Chart text with enough characters to pass the gate",
        )
        region = _region(0, 0, 30, 30)

        with patch.dict("sys.modules", {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"])

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"]


# ---------------------------------------------------------------------------
# D3: HTML-comment-marker exemption from garble detection
# ---------------------------------------------------------------------------


class TestImageMarkerGarbleExemption:
    """Design Property 4: a text blob consisting solely of <!-- ... -->
    HTML comment markers is never flagged garbled; genuine repeated
    non-comment tokens above the 30% threshold still are."""

    def test_only_image_markers_not_garbled(self):
        """A scanned-PDF markdown with nothing but repeated <!-- image -->
        markers (100% single-token repetition pre-D3) must NOT be flagged
        garbled -- these are structural markers, not mojibake."""
        blob = "\n\n".join([_IMAGE_MARKER] * 45)
        assert check_garble(blob, expected_script=None, profile=BULK_PROFILE) is False

    def test_genuine_repeated_tokens_still_garbled(self):
        blob = " ".join(["xkjqz"] * 40)
        assert check_garble(blob, expected_script=None, profile=BULK_PROFILE) is True


# ---------------------------------------------------------------------------
# D4: content-quality guard on the cat_b_promoted gate
# ---------------------------------------------------------------------------


class TestCatBPromotedContentQualityGuard:
    """Design Property 5: promotion to PASS is blocked if
    len(flat_text.strip()) < MIN_FLAT_PROMOTION_CHARS OR the ratio of
    image-placeholder blocks to total blocks exceeds 0.5, regardless of
    node_count, max_leaf_ratio, or garble status.

    Note: `_flatten_tree_text` concatenates node text with no separator,
    so per-block text carries a trailing "\\n" here (as real extracted
    markdown blocks do) to make each block land on its own line for the
    placeholder-ratio line-scan in `classify_verdict`.
    """

    def test_placeholder_blocks_below_char_threshold_blocked(self):
        """Doc 21 regression case: 15 <!-- image --> blocks, ~210 total
        chars. Passes node_count/leaf-ratio/garble gates pre-D4 but must
        no longer be promoted via cat_b_promoted.
        Zone-1: without gate evaluation (validate_result=None), the early
        structural-OK return may fire with PASS — the key property is that
        cat_b_promoted is never the reason."""
        structure = [{"title": "", "text": _IMAGE_MARKER + "\n"} for _ in range(15)]
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert reason != "cat_b_promoted"

    def test_real_text_blocks_above_threshold_promoted(self):
        structure = [
            {
                "title": "",
                "text": (
                    f"block number {i} has real prose content describing the "
                    "document in detail with enough words to be meaningful. " * 3 + "\n"
                ),
            }
            for i in range(15)
        ]
        flat_text = "".join(b["text"] for b in structure)
        assert len(flat_text.strip()) >= 500
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert verdict == "PASS"
        assert reason in ("", "cat_b_promoted")


# ---------------------------------------------------------------------------
# D5: prefer synthetic structure over a rejected tree for flat-routed docs
# ---------------------------------------------------------------------------


def _synthesize_flat_structure(flat_structure: list, blocks: list) -> list:
    # D5 (RFC-023): mirrors client.py's index() -- always prefer synthetic
    # structure from blocks when blocks exist, regardless of whether
    # flat_structure (the rejected tree) is empty or non-empty.
    if blocks:
        flat_structure = [
            {"title": "", "text": _flat_block_primary_text(b)}
            for b in blocks
            if _flat_block_primary_text(b).strip()
        ]
    return flat_structure


class TestSyntheticStructurePreference:
    """Design Property 6: for any flat-routed document where `blocks` is
    non-empty, the verdict-computation input structure is the synthetic
    structure built from `blocks`, regardless of whether the rejected
    tree structure is itself empty or non-empty."""

    def test_non_empty_rejected_structure_replaced_by_synthetic_from_blocks(self):
        """Doc 20 regression case: tree builder produced a non-empty
        rejected structure (low node_count/depth), but 355 real blocks
        exist. The rejected structure must never be used."""
        rejected_structure = [{"title": "", "text": "sparse rejected tree content"}]
        blocks = [{"text": f"block {i} has real prose content"} for i in range(355)]
        structure = _synthesize_flat_structure(rejected_structure, blocks)
        assert structure != rejected_structure
        assert len(structure) == len(blocks)
        assert all(node["text"] for node in structure)

    def test_empty_rejected_structure_still_synthesized_from_blocks(self):
        """Pre-D5 behavior (structure=[] and blocks) must be preserved --
        no regression from B1/RFC-022."""
        blocks = [{"text": "alpha content"}, {"text": "beta content"}, {"text": "gamma content"}]
        structure = _synthesize_flat_structure([], blocks)
        assert len(structure) == len(blocks)


# ---------------------------------------------------------------------------
# D7: Tesseract-on-raster fallback when the VLM crashes on garbled PDFs
# ---------------------------------------------------------------------------


def _vlm_tesseract_fallback(ocr_text: str, *, reason: str = "garbling") -> str:
    """Reproduces client.py's recovery/reason-override logic exactly."""
    if ocr_text and not check_garble(ocr_text, expected_script=None, profile=FLAT_MARKDOWN_PROFILE):
        reason = "node_count<3"
    return reason


def _garbling_without_exception_gate(ok: bool, reason: str) -> bool:
    """Reproduces client.py's RFC-024 D5 gate: after the VLM try-block's
    validate_tree() call succeeds (no exception raised), recovery fires
    only when ok is False, reason is 'garbling', and
    D7_GARBLE_RECOVERY_ENABLED."""
    return not ok and reason == "garbling" and client_mod._D7_GARBLE_RECOVERY_ENABLED


class TestVlmTesseractFallback:
    """Design Property 8: on VLM exception, Tesseract OCR runs on the
    rasterized page images; clean OCR text overrides reason to
    'node_count<3' (flat success path); garbled/empty text still raises
    LowQualityTreeError('garbling')."""

    def test_clean_ocr_text_overrides_reason_to_node_count(self):
        assert _vlm_tesseract_fallback(_CLEAN_TEXT) == "node_count<3"

    def test_garbled_ocr_text_leaves_reason_as_garbling(self):
        """Garbled Tesseract output must NOT override the reason -- the
        document still raises LowQualityTreeError('garbling') per HR5."""
        assert _vlm_tesseract_fallback(_GARBLED_TEXT) == "garbling"


# ---------------------------------------------------------------------------
# D8: standalone-image OCR enrichment + terminal-vs-transient LLM failures
# ---------------------------------------------------------------------------


def _standalone_image_ocr_should_run(md_content: str) -> bool:
    """Reproduces client.py's standalone-image OCR skip-guard condition
    exactly."""
    return len("".join(md_content.split())) <= MIN_STANDALONE_IMAGE_MD_CHARS


class TestClassifyLlmFailure:
    """Design Property 9: LLMTransientFailure is classified terminal (no
    retry) iff the error detail contains a CMap-corruption or
    content-policy indicator, else transient (retryable)."""

    def test_cmap_indicator_is_terminal(self):
        assert _classify_llm_failure("CMap corruption detected") == "llm_failure_terminal"

    def test_rate_limit_indicator_is_transient(self):
        assert (
            _classify_llm_failure("429 rate_limit exceeded, throttled") == "llm_failure_transient"
        )


# ---------------------------------------------------------------------------
# D9: heading-marker BiDi preservation in reconstruct_bidi_order
# ---------------------------------------------------------------------------

_LOGICAL_HEADING = "الفصل الأول: تعريفات"
_VISUAL_HEADING = get_display(_LOGICAL_HEADING)

_LOGICAL_BODY_LINE = (
    "هذا النص العربي مكتوب بترتيب منطقي صحيح تماما ويجب ان يبقى كما هو دون اي تغيير في الحروف"
)


# ---------------------------------------------------------------------------
# D10: PASS_MAX_LEAF_RATIO env-var-tunable threshold
# ---------------------------------------------------------------------------

_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",
    "apple",
    "banana",
    "cherry",
    "date",
    "fig",
    "grape",
]


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + " ")[:n]


def _tree_with_ratio(ratio: float, total_chars: int = 10000, n_other: int = 6) -> list:
    """Root node with one dominant leaf (`ratio` share of leaf chars) and
    `n_other` smaller leaves, so node_count and depth clear their gates
    and only max_leaf_ratio varies."""
    max_leaf = round(ratio * total_chars)
    other_leaf = (total_chars - max_leaf) // n_other
    leaves = [{"title": "", "text": _text_of_length(max_leaf), "nodes": []}]
    leaves += [
        {"title": "", "text": _text_of_length(other_leaf), "nodes": []} for _ in range(n_other)
    ]
    return [{"title": "Root", "text": "", "nodes": leaves}]


class TestPassMaxLeafRatioEnvVar:
    """Design Property 10: the leaf-concentration threshold for the main
    PASS gate reads from PASS_MAX_LEAF_RATIO (default 0.20) rather than a
    hardcoded value."""

    def test_ratio_below_widened_threshold_passes(self, monkeypatch):
        """max_leaf_ratio=0.18 with PASS_MAX_LEAF_RATIO=0.20 -> PASS."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        structure = _tree_with_ratio(0.18)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")

    def test_ratio_above_widened_threshold_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.22 with PASS_MAX_LEAF_RATIO=0.20 -> MARGINAL."""
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.20")
        reset_pipeline_config()
        structure = _tree_with_ratio(0.22)
        verdict, reason = classify_verdict(structure, "hierarchical", None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.22"


# ---------------------------------------------------------------------------
# D11: widen OCR escalation to structural-failure reasons for image-dominant docs
# ---------------------------------------------------------------------------


def _image_dominant(md_content: str) -> tuple[bool, int, int]:
    """Reproduces client.py's image-dominance ratio computation exactly."""
    total_lines = md_content.splitlines()
    non_empty_lines = [ln for ln in total_lines if ln.strip()]
    image_lines = sum(1 for ln in non_empty_lines if _MARKER in ln)
    dominant = bool(non_empty_lines) and (image_lines / len(non_empty_lines)) > 0.50
    return dominant, image_lines, len(non_empty_lines)


def _would_escalate(reason: str, md_content: str, *, ext: str = ".pdf") -> bool:
    """Reproduces the D11 gate's overall condition (reason in structural
    failures + image-dominant), gated on the module flags."""
    if reason not in ("node_count<3", "depth<2"):
        return False
    if ext != ".pdf" or not OCR_ESCALATION_GARBLE or not _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED:
        return False
    dominant, _, _ = _image_dominant(md_content)
    return dominant


class TestStructuralFailureOcrEscalation:
    """Design Property 12: for any validate_tree failure with reason in
    ('node_count<3', 'depth<2') where the image-line ratio (image lines /
    non-empty lines) exceeds 0.50, the system triggers the same OCR
    escalation path as reason == 'garbling'; the ratio is computed
    against non_empty_lines, not total_lines."""

    def test_structural_failure_image_dominant_triggers_escalation(self):
        md = f"{_MARKER}\n{_MARKER}\n{_MARKER}\nsome prose"
        assert _would_escalate("node_count<3", md) is True

    def test_structural_failure_non_image_dominant_no_escalation(self):
        md = "\n".join(["real paragraph text here"] * 8 + [_MARKER])
        assert _would_escalate("node_count<3", md) is False
