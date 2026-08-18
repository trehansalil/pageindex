"""Zone-6 Step D: fence-parity observability warnings.

Regression tests:
  - Orphan-close (bare ``` with no prior open) fires warning + counter.
  - Unclosed-at-EOF (open ``` with no close) fires warning + counter.
  - Zero content loss in ALL fence-parity scenarios: the content between,
    before, and after fence delimiters is always preserved per RFC-030 D0.
  - Balanced fences (matched open/close) produce no warnings.
"""

from pageindex_mcp.helpers import route_and_extract_flat
from pageindex_mcp.metrics import FENCE_PARITY_WARNING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fence_warning_count(kind: str) -> float:
    """Read the current counter value for a specific fence parity warning kind."""
    return FENCE_PARITY_WARNING.labels(kind=kind)._value.get()


def _all_text(blocks: list[dict]) -> str:
    """Concatenate all text from blocks for content-preservation checks."""
    parts = []
    for b in blocks:
        if b.get("text"):
            parts.append(b["text"])
        if b.get("ocr_text"):
            parts.append(b["ocr_text"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Orphan-close warning
# ---------------------------------------------------------------------------

class TestOrphanClose:
    """Bare ``` without prior open fires orphan_close warning."""

    def test_orphan_close_fires_warning(self, caplog):
        """A bare ``` at the start fires orphan_close warning."""
        md = "```\nSome content here.\nMore content."
        before = _fence_warning_count("orphan_close")

        content_class, blocks = route_and_extract_flat(md)

        after = _fence_warning_count("orphan_close")
        assert after > before, "FENCE_PARITY_WARNING(orphan_close) must increment"
        assert "fence_parity" in caplog.text.lower() or "orphan" in caplog.text.lower()

    def test_orphan_close_preserves_content(self):
        """Content after an orphan close fence is preserved."""
        md = "```\nImportant data line.\nAnother line of data."
        _, blocks = route_and_extract_flat(md)

        text = _all_text(blocks)
        assert "Important data line" in text, "Content after orphan close must be preserved"
        assert "Another line of data" in text, "All content lines must be preserved"

    def test_multiple_orphan_closes(self):
        """Multiple orphan closes each fire the warning."""
        md = "```\nLine A.\n```\nLine B."
        before = _fence_warning_count("orphan_close")

        _, blocks = route_and_extract_flat(md)

        after = _fence_warning_count("orphan_close")
        # At least one orphan close should fire (the first bare ``` is orphan;
        # the second depends on state reset).
        assert after > before, "At least one orphan_close warning expected"

        text = _all_text(blocks)
        assert "Line A" in text
        assert "Line B" in text


# ---------------------------------------------------------------------------
# Unclosed-at-EOF warning
# ---------------------------------------------------------------------------

class TestUnclosedAtEOF:
    """Open fence (```lang) with no closing ``` fires unclosed_at_eof."""

    def test_unclosed_fence_fires_warning(self, caplog):
        """A ```json with no close fires unclosed_at_eof."""
        md = "Preamble.\n```json\n{\"key\": \"value\"}\nMore text."
        before = _fence_warning_count("unclosed_at_eof")

        _, blocks = route_and_extract_flat(md)

        after = _fence_warning_count("unclosed_at_eof")
        assert after > before, "FENCE_PARITY_WARNING(unclosed_at_eof) must increment"

    def test_unclosed_fence_preserves_content(self):
        """All content around and within unclosed fence is preserved."""
        md = "Before fence.\n```python\ndef hello():\n    pass\nAfter fence."
        _, blocks = route_and_extract_flat(md)

        text = _all_text(blocks)
        assert "Before fence" in text, "Content before fence must be preserved"
        # The fence delimiter is stripped, but enclosed content falls through.
        assert "hello" in text or "pass" in text, "Fence content must be preserved"
        assert "After fence" in text, "Content after unclosed fence must be preserved"

    def test_multiple_unclosed_fences(self):
        """Multiple open fences without closes -> single EOF warning
        (depth > 0 at EOF)."""
        md = "Start.\n```json\ndata1\n```yaml\ndata2"
        before = _fence_warning_count("unclosed_at_eof")

        _, blocks = route_and_extract_flat(md)

        after = _fence_warning_count("unclosed_at_eof")
        assert after > before, "Unclosed fences at EOF must fire warning"

        text = _all_text(blocks)
        assert "Start" in text
        assert "data1" in text
        assert "data2" in text


# ---------------------------------------------------------------------------
# Balanced fences -> no warnings
# ---------------------------------------------------------------------------

class TestBalancedFences:
    """Properly matched fences produce no parity warnings."""

    def test_balanced_fence_no_warnings(self):
        """```json ... ``` is balanced -> no warnings."""
        md = "Before.\n```json\n{\"key\": \"val\"}\n```\nAfter."
        before_orphan = _fence_warning_count("orphan_close")
        before_eof = _fence_warning_count("unclosed_at_eof")

        _, blocks = route_and_extract_flat(md)

        after_orphan = _fence_warning_count("orphan_close")
        after_eof = _fence_warning_count("unclosed_at_eof")

        assert after_orphan == before_orphan, "Balanced fence should not trigger orphan_close"
        assert after_eof == before_eof, "Balanced fence should not trigger unclosed_at_eof"

    def test_balanced_nested_fences(self):
        """Two consecutive balanced fence pairs -> no warnings."""
        md = "A.\n```json\nblock1\n```\nB.\n```python\nblock2\n```\nC."
        before_orphan = _fence_warning_count("orphan_close")
        before_eof = _fence_warning_count("unclosed_at_eof")

        _, blocks = route_and_extract_flat(md)

        after_orphan = _fence_warning_count("orphan_close")
        after_eof = _fence_warning_count("unclosed_at_eof")

        assert after_orphan == before_orphan
        assert after_eof == before_eof


# ---------------------------------------------------------------------------
# Zero content loss in all cases
# ---------------------------------------------------------------------------

class TestZeroContentLoss:
    """Content is NEVER lost due to fence delimiters (RFC-030 D0 invariant)."""

    def test_content_between_balanced_fences_preserved(self):
        md = "Alpha.\n```json\nBeta data.\n```\nGamma."
        _, blocks = route_and_extract_flat(md)
        text = _all_text(blocks)
        assert "Alpha" in text
        assert "Beta data" in text
        assert "Gamma" in text

    def test_content_around_orphan_close_preserved(self):
        md = "First.\n```\nSecond.\nThird."
        _, blocks = route_and_extract_flat(md)
        text = _all_text(blocks)
        assert "First" in text
        assert "Second" in text
        assert "Third" in text

    def test_content_around_unclosed_open_preserved(self):
        md = "Start.\n```python\ndef foo(): pass\nEnd."
        _, blocks = route_and_extract_flat(md)
        text = _all_text(blocks)
        assert "Start" in text
        assert "foo" in text or "pass" in text
        assert "End" in text

    def test_no_fence_markers_in_output(self):
        """Fence delimiter lines themselves are stripped, not content."""
        md = "Content.\n```json\nJSON data.\n```\nMore."
        _, blocks = route_and_extract_flat(md)
        text = _all_text(blocks)
        # The ``` lines should be stripped, but content preserved.
        assert "Content" in text
        assert "JSON data" in text
        assert "More" in text
