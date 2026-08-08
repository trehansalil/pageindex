"""RFC-034 D3 -- local re-normalization safety net for remote-returned markdown.

`CustomPageIndexClient.index()` (client.py ~972-980, mirrored ~1188-1196)
gates a `reconstruct_bidi_order` pass on remote-returned markdown behind
`_use_remote and REMOTE_MD_RENORMALIZE`, incrementing `REMOTE_MD_RENORMALIZED`
only when the pass actually changes the content. `_apply_d3_gate` below
mirrors that inline block exactly so it can be unit-tested without invoking
the full `index()` pipeline (MinIO/httpx/tree-build side effects).

D3 relies on `reconstruct_bidi_order` being idempotent so the markdown-level
pass and the existing node-level repair loop (client.py:1279-1298) can both
fire on the same document without disagreeing (option b, RFC-034 D3 /
D14). Test 4 below asserts that property directly.
"""

from pageindex_mcp import client as client_module
from pageindex_mcp.converters import reconstruct_bidi_order
from pageindex_mcp.metrics import REMOTE_MD_RENORMALIZED

_REVERSED_HEADING_MD = "تافيرعت :لوألا لصفلا ##\n\nSome English body text follows."
_CORRECTED_HEADING_MD = "## الفصل الأول: تعريفات\n\nSome English body text follows."

_ALREADY_CORRECT_MD = (
    "## الفصل الأول: تعريفات\n\n"
    "This document mixes English body text with Arabic: "
    "هذا نص عربي مضمن داخل نص انجليزي طويل بما يكفي لتفعيل اعادة الترتيب "
    "and continues in English afterwards."
)


def _apply_d3_gate(md_content: str, use_remote: bool = True) -> str:
    """Mirrors the D3 gate in `CustomPageIndexClient.index()` (client.py ~972-980)."""
    if use_remote and client_module.REMOTE_MD_RENORMALIZE:
        renormalized = reconstruct_bidi_order(md_content)
        if renormalized != md_content:
            REMOTE_MD_RENORMALIZED.inc()
            md_content = renormalized
    return md_content


def _counter_value() -> float:
    return REMOTE_MD_RENORMALIZED._value.get()


class TestD3RenormalizationGate:
    def test_reversed_heading_corrected(self):
        before = _counter_value()
        result = _apply_d3_gate(_REVERSED_HEADING_MD)
        assert result == _CORRECTED_HEADING_MD
        assert _counter_value() == before + 1

    def test_already_correct_markdown_unchanged_no_increment(self):
        before = _counter_value()
        result = _apply_d3_gate(_ALREADY_CORRECT_MD)
        assert result == _ALREADY_CORRECT_MD
        assert _counter_value() == before

    def test_disabled_via_config_skips_pass(self, monkeypatch):
        monkeypatch.setattr(client_module, "REMOTE_MD_RENORMALIZE", False)
        before = _counter_value()
        result = _apply_d3_gate(_REVERSED_HEADING_MD)
        assert result == _REVERSED_HEADING_MD
        assert _counter_value() == before

    def test_double_application_idempotent(self):
        once = reconstruct_bidi_order(_REVERSED_HEADING_MD)
        twice = reconstruct_bidi_order(once)
        assert twice == once
