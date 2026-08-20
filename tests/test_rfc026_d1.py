"""Tests for RFC-026 Task 1.2 (D1): image-enrichment-promoted volume floor.

Validates Design Property 2: for any document taking the
``image_enrichment_promoted`` branch (``content_class in ("flat_prose",
"flat_mixed")``, ``image_enrichment_ratio >= 0.8``), the verdict is
``"PASS"`` only when ``total_chars >= MIN_IMAGE_PROMOTED_CHARS``; otherwise
it is capped at ``"MARGINAL"``.
"""

from pageindex_mcp.helpers import classify_verdict


def _structure_with_chars(n_chars):
    return [{"node_id": "1", "title": "", "text": "x" * n_chars, "nodes": []}]


class TestImageEnrichmentPromotedVolumeFloor:
    def test_chars_below_floor_returns_margin(self):
        """38 chars is well below the default 500-char floor -> MARGINAL."""
        structure = _structure_with_chars(38)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "MARGINAL"
        assert reason == "image_enrichment_promoted_below_char_floor"

    def test_chars_above_floor_returns_pass(self):
        """600 chars clears the default 500-char floor -> PASS."""
        structure = _structure_with_chars(600)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_env_override_min_image_promoted_chars(self, monkeypatch):
        """MIN_IMAGE_PROMOTED_CHARS=100 lowers the floor: 150 chars, which
        would MARGINAL under the default 500-char floor, now PASSes."""
        monkeypatch.setenv("MIN_IMAGE_PROMOTED_CHARS", "100")
        from pageindex_mcp.config import reset_pipeline_config
        reset_pipeline_config()
        structure = _structure_with_chars(150)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_env_override_still_caps_below_new_floor(self, monkeypatch):
        """MIN_IMAGE_PROMOTED_CHARS=100: 50 chars is still below the lowered
        floor and stays capped at MARGINAL (tasks §1.4 item c)."""
        monkeypatch.setenv("MIN_IMAGE_PROMOTED_CHARS", "100")
        structure = _structure_with_chars(50)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "MARGINAL"
        assert reason == "image_enrichment_promoted_below_char_floor"

    def test_boundary_one_below_floor_marginal(self):
        """total_chars == 499 (one below the default 500 floor) -> MARGINAL."""
        structure = _structure_with_chars(499)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "MARGINAL"
        assert reason == "image_enrichment_promoted_below_char_floor"

    def test_boundary_one_above_floor_passes(self):
        """total_chars == 501 (one above the default 500 floor) -> PASS."""
        structure = _structure_with_chars(501)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"

    def test_boundary_chars_equal_floor_passes(self):
        """total_chars == MIN_IMAGE_PROMOTED_CHARS exactly (500) is
        boundary-inclusive -- PASS, not MARGINAL."""
        structure = _structure_with_chars(500)
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"
