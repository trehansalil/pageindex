# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Zone 3 — OCR Recovery Cascade contract tests.

Tests the extracted pure functions from the wave-2 refactor:
- _repeating_token_density: repeating-token density measure
- _keep_best_wins: keep-best decision cascade for OCR retry
- decide_ocr_mode parameter forwarding
"""

from unittest.mock import patch

from pageindex_mcp.client.recovery import _keep_best_wins, _repeating_token_density


class TestRepeatingTokenDensity:

    def test_short_text_returns_max(self):
        assert _repeating_token_density("hello world") == 1.0

    def test_under_20_tokens_returns_max(self):
        text = " ".join(f"word{i}" for i in range(19))
        assert _repeating_token_density(text) == 1.0

    def test_all_same_tokens(self):
        text = " ".join(["xkjqz"] * 40)
        assert _repeating_token_density(text) == 1.0

    def test_unique_tokens_low_density(self):
        text = " ".join(f"uniqueword{i}" for i in range(40))
        density = _repeating_token_density(text)
        assert density < 0.1

    def test_mixed_repetition(self):
        text = " ".join(["repeat"] * 20 + [f"unique{i}" for i in range(20)])
        density = _repeating_token_density(text)
        assert 0.4 < density < 0.6


def _tree(text: str) -> dict:
    return {"structure": [{"node_id": "1", "title": "", "text": text, "nodes": []}]}


class TestKeepBestWins:

    def test_zero_chars_pre_any_post_wins(self):
        result = _keep_best_wins(
            pre_result=_tree(""),
            pre_total_chars=0,
            post_result=_tree("hello world new content"),
            post_ok=True,
            expected_script=None,
            script_context=None,
            filename="test.pdf",
        )
        assert result is True

    def test_char_count_regression_reverts(self):
        result = _keep_best_wins(
            pre_result=_tree("a" * 500),
            pre_total_chars=500,
            post_result=_tree("b" * 100),
            post_ok=True,
            expected_script=None,
            script_context=None,
            filename="test.pdf",
        )
        assert result is False

    def test_more_chars_non_garbled_pre_wins(self):
        clean_pre = "This is a perfectly ordinary section of legible English prose text."
        clean_post = clean_pre + " And some more legible text added by OCR retry."
        result = _keep_best_wins(
            pre_result=_tree(clean_pre),
            pre_total_chars=len(clean_pre),
            post_result=_tree(clean_post),
            post_ok=True,
            expected_script=None,
            script_context=None,
            filename="test.pdf",
        )
        assert result is True


class TestDecideOcrModeForwarding:

    def test_forwards_garble_status_document_type_ocr_langs(self):
        with patch("pageindex_mcp.picture_plane.decide_ocr_strategy") as mock_strategy:
            from pageindex_mcp.picture_plane import OcrDecision, OcrMode, decide_ocr_mode

            mock_strategy.return_value = OcrDecision(mode=OcrMode.NONE)

            decide_ocr_mode(
                ocr_escalation_enabled=True,
                has_image_markers=True,
                garble_status=True,
                document_type="pdf",
                ocr_langs=["deu", "ara"],
            )

            mock_strategy.assert_called_once()
            call_kwargs = mock_strategy.call_args[1]
            assert call_kwargs["garble_status"] is True
            assert call_kwargs["document_type"] == "pdf"
            assert call_kwargs["ocr_langs"] == ["deu", "ara"]
