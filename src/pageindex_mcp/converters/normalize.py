from __future__ import annotations

import dataclasses
import re
import unicodedata

from ..script import AR_CHAR_RE as _AR_SCRIPT_RE
from ..script import RtlDecision, apply_rtl, decide_rtl

_INDENTED_HEADING_RE = re.compile(r"^[ \t]+(#{1,6}\s)", re.MULTILINE)


def _normalize_indented_headings(md: str) -> str:
    """Strip leading whitespace before markdown heading markers."""
    return _INDENTED_HEADING_RE.sub(r"\1", md)


# RFC-015 D5c: Docling occasionally emits several '#{1,6} '-prefixed headings on ONE
# physical line ("text### Heading"); the line-anchored heading regexes downstream see
# only the first, so the rest fuse into a giant tail blob (doc 7dcf7cb7). The lookbehind
# `[^\n#]` matches only a heading marker preceded by a non-newline, non-'#' char, so a
# marker mid-line ("text### X") splits while true line-start headings AND the interior
# of a genuine multi-'#' run ("## X" must NOT split into "#\n# X") are left untouched.
# (Tightened from the RFC-015 pseudocode's `[^\n]`, which wrongly split multi-'#' runs.)
_RUN_TOGETHER_HEADING_RE = re.compile(r"(?<=[^\n#])(#{1,6}\s)")


def _split_run_together_headings(md: str) -> str:
    """Insert a newline before any heading marker Docling emitted mid-line (RFC-015 D5c).

    Runs BEFORE the hash-sentinel fix (D4) and heading-depth inference so a run-together
    ``text### Heading`` is separated onto its own line first — both D4's per-line pass
    and the splitter's ordinal matching only inspect one marker per line."""
    return _RUN_TOGETHER_HEADING_RE.sub(r"\n\1", md)


# RFC-015 D4: consume WHOLE '#+' runs (not just interior '#'). RFC-010 D5 used
# `(?<=\S)#(?=\S)` (non-whitespace both sides), so when the corrupted في run's outer
# edges sat next to whitespace — the normal case, في being a standalone word — the
# boundary '#'s survived, leaving `#في#`/`#فيفي#`. That residue poisoned the splitter's
# oversized-ordinal anchor, fusing whole legal instruments into one leaf (doc aebf15b4).
_INLINE_HASH_RE = re.compile(r"#+")
_HEADING_MARKER_LINE_RE = re.compile(r"#{1,6}[ \t]")


def _fix_fi_hash_substitution(md: str) -> str:
    """Restore في from Docling's ``#`` substitution in Arabic text (RFC-015 D4).

    Docling renders the standalone Arabic word في as ``#``. This converts every inline
    ``#+`` run back to في, per line, while preserving genuine line-initial markdown
    heading markers (``#{1,6}`` followed by a space). Widened from the RFC-010 D5
    interior-only regex so boundary/standalone ``#`` are also consumed (see the note
    above). Runs EARLIER in the pipeline (before heading-depth inference) so في is a
    single token by the time the heading regex sees it. Pure local string surgery — no
    LLM, no network (HR3)."""
    if not md:
        return md
    arabic = sum(1 for c in md if "؀" <= c <= "ۿ")
    if arabic / len(md) <= 0.15:
        return md
    out: list[str] = []
    for line in md.splitlines(keepends=True):
        stripped = line.lstrip()
        # Preserve a genuine markdown heading marker ("## Heading"); convert everything
        # else — inline '#' runs that are corrupted في — to في.
        if _HEADING_MARKER_LINE_RE.match(stripped):
            out.append(line)
        else:
            out.append(_INLINE_HASH_RE.sub("في", line))
    return "".join(out)


# Split a leading markdown heading marker off a line so reconstruct_bidi_order reorders
# only the title text, leaving the '#' prefix in place for depth inference.
_BIDI_HEADING_PREFIX_RE = re.compile(r"^(\s*#{1,6}[ \t]+)(.*)$", re.DOTALL)


def reconstruct_bidi_order(
    text: str,
    expected_script: str | None = None,
) -> tuple[str, RtlDecision | None]:
    """Zone-3/6: apply_rtl shim replacing the old bidi reconstructor.

    Two-level strategy (preserves RFC-023 D9 per-heading correction):
    1. Document-level: if decide_rtl says the whole text is reversed,
       apply_rtl repairs all lines.
    2. Per-heading: even when the document is NOT reversed overall,
       each heading line is checked individually — a visual-order
       heading in an otherwise-logical document still gets corrected.

    Zone-6: returns ``(result_text, RtlDecision | None)`` so the decision
    can be threaded through the pipeline without re-computation.  The
    zero-Arabic early-return has been replaced with a guard that skips
    only the document-level ``decide_rtl`` call; the per-heading loop
    still runs so bilingual documents get heading-level bidi repair.

    ``expected_script`` is accepted for call-site compatibility but is
    unused (``decide_rtl`` infers script from content).
    """
    if not text:
        return text, None
    arabic = len(_AR_SCRIPT_RE.findall(text))

    # Zone-6: skip ONLY the document-level decide_rtl when no Arabic is
    # present, but still fall through to the per-heading loop so bilingual
    # documents with localised Arabic headings get heading-level repair.
    decision: RtlDecision | None = None
    if arabic > 0:
        decision = decide_rtl(text)
        if decision.reversed:
            return apply_rtl(text, reversed_flag=True), decision

    out: list[str] = []
    changed = False
    for line in text.splitlines(keepends=True):
        m = _BIDI_HEADING_PREFIX_RE.match(line)
        if m:
            heading_text = m.group(2)
            if decide_rtl(heading_text.strip(), sample_count=1).reversed:
                repaired = apply_rtl(heading_text.rstrip(), reversed_flag=True)
                eol = line[len(line.rstrip()) :]
                out.append(m.group(1) + repaired + eol)
                changed = True
                continue
        out.append(line)
    return ("".join(out) if changed else text), decision


def _pre_inference_normalize(text: str) -> tuple[str, RtlDecision | None]:
    """Markdown clean-up run BEFORE heading-depth inference (RFC-015 D5c/D4/D7).

    Ordering is load-bearing: D5c (split run-together headings) must precede D4 (the
    per-line hash-sentinel fix, so ``##Foo ###Bar`` is split before the one-marker-per-
    line pass), which must precede D7 (BiDi reorder) and depth inference (so في is a
    single token by the time the heading regex parses it).

    Zone-6: NFKC canonicalization of Arabic Presentation Forms (U+FB50-FDFF,
    U+FE70-FEFF) now runs AFTER ``reconstruct_bidi_order`` so that
    ``_word_has_reversed_morphology`` sees presentation-form codepoints intact
    when they exist.  The ``had_presentation_forms`` signal is captured before
    NFKC and attached to the ``RtlDecision`` for downstream garble-gate use.
    (Supersedes RFC-029 §1.1 Design Property 1 ordering; idempotence is
    preserved because NFKC is still gated on detection.)
    """
    text = _split_run_together_headings(text)  # D5c
    text = _fix_fi_hash_substitution(text)  # D4 (moved earlier in the pipeline)
    text, rtl_decision = reconstruct_bidi_order(text)  # D7 (Zone-3: sole bidi normalization step)

    # Zone-6: capture presentation-form signal BEFORE NFKC destroys the
    # codepoints, then canonicalize.  The boolean is threaded through
    # RtlDecision.had_presentation_forms so the garble gate (helpers.py)
    # can still detect presentation-form artefacts post-NFKC.
    # Ranges: Arabic Presentation Forms-A U+FB50-U+FDFF,
    #         Arabic Presentation Forms-B U+FE70-U+FEFF.
    had_pres_forms = any("ﭐ" <= ch <= "﷿" or "ﹰ" <= ch <= "﻿" for ch in text)
    if had_pres_forms:
        text = unicodedata.normalize("NFKC", text)
    if had_pres_forms and rtl_decision is not None:
        rtl_decision = dataclasses.replace(rtl_decision, had_presentation_forms=True)

    return text, rtl_decision
