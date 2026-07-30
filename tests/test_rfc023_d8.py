"""Tests for RFC-023 Tasks 4.1 (D8a) and 4.3 (D8b): standalone-image OCR
enrichment and terminal-vs-transient LLM failure classification.

Validates Design Property 9: for any standalone image file whose
Docling-extracted ``md_content`` contains <= ``MIN_STANDALONE_IMAGE_MD_CHARS``
(default 100) non-whitespace characters, the system SHALL run Tesseract OCR
on the raw image bytes and populate the synthetic ``PictureResult.ocr_text``;
otherwise the OCR step SHALL be skipped. For any ``LLMTransientFailure``
raised by a child process, ``_classify_llm_failure`` SHALL classify it as
terminal (added to ``_TERMINAL_CHILD_REASONS``, no retry) if and only if the
error detail contains a CMap-corruption or content-policy indicator, and as
transient (retryable, MAX_TRIES=2) otherwise.

``client.py``'s standalone-image route (~line 753-790) is inline inside the
large async ``index()`` method rather than a standalone function, so the D8a
tests pin the exact char-count skip-guard against the real
``MIN_STANDALONE_IMAGE_MD_CHARS`` module constant, mirroring the
``test_rfc023_d11.py`` characterization-test pattern. The D8b tests call
``_classify_llm_failure`` and the ``_TERMINAL_CHILD_REASONS`` membership
directly since those are standalone functions/constants in ``worker.py``.
"""

from pageindex_mcp.client import MIN_STANDALONE_IMAGE_MD_CHARS
from pageindex_mcp.worker import _TERMINAL_CHILD_REASONS, _classify_llm_failure


def _standalone_image_ocr_should_run(md_content: str) -> bool:
    """Reproduces client.py:771's skip-guard condition exactly."""
    return len("".join(md_content.split())) <= MIN_STANDALONE_IMAGE_MD_CHARS


class TestStandaloneImageOcrEnrichment:
    def test_empty_md_content_runs_ocr(self):
        """Docling extracted nothing -- Tesseract OCR must run to populate
        the synthetic PictureResult.ocr_text."""
        assert _standalone_image_ocr_should_run("") is True

    def test_sparse_md_content_at_threshold_runs_ocr(self):
        """Non-whitespace char count exactly at MIN_STANDALONE_IMAGE_MD_CHARS
        is still <= the threshold -- OCR runs (boundary is inclusive)."""
        md = "a" * MIN_STANDALONE_IMAGE_MD_CHARS
        assert _standalone_image_ocr_should_run(md) is True

    def test_rich_md_content_over_threshold_skips_ocr(self):
        """Docling already extracted meaningful prose (> threshold non-
        whitespace chars) -- OCR must be skipped to avoid double-counting."""
        md = "a" * (MIN_STANDALONE_IMAGE_MD_CHARS + 1)
        assert _standalone_image_ocr_should_run(md) is False

    def test_whitespace_only_md_content_runs_ocr(self):
        """Whitespace is stripped before counting -- a whitespace-only
        md_content has zero non-whitespace chars and OCR must run."""
        assert _standalone_image_ocr_should_run("   \n\n\t  ") is True


class TestClassifyLlmFailure:
    def test_cmap_indicator_is_terminal(self):
        assert _classify_llm_failure("CMap corruption detected") == "llm_failure_terminal"

    def test_content_policy_indicator_is_terminal(self):
        assert _classify_llm_failure("rejected: content_policy violation") == "llm_failure_terminal"

    def test_content_filter_indicator_is_terminal(self):
        assert _classify_llm_failure("blocked by content_filter") == "llm_failure_terminal"

    def test_rate_limit_indicator_is_transient(self):
        assert (
            _classify_llm_failure("429 rate_limit exceeded, throttled") == "llm_failure_transient"
        )

    def test_unrecognized_detail_is_transient(self):
        """Fails open toward retry rather than silent data loss."""
        assert (
            _classify_llm_failure("some unrelated transient network hiccup")
            == "llm_failure_transient"
        )

    def test_cmap_and_rate_limit_both_present_is_terminal(self):
        """D8b boundary case: a stderr_tail carrying both a rate-limit
        indicator AND a CMap indicator (e.g. a rate-limited request whose
        retry then hit a CMap-corrupt PDF) must classify as terminal --
        deterministic-failure precedence wins over the transient indicator
        so arq does not loop retries forever on an undecodable document."""
        stderr_tail = "429 rate_limit exceeded; retry hit CMap corruption"
        assert _classify_llm_failure(stderr_tail) == "llm_failure_terminal"

    def test_terminal_classification_is_in_terminal_reasons(self):
        assert "llm_failure_terminal" in _TERMINAL_CHILD_REASONS

    def test_transient_classification_is_not_in_terminal_reasons(self):
        assert "llm_failure_transient" not in _TERMINAL_CHILD_REASONS
