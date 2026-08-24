"""Heading-depth recovery, numbering-prefix containment, and PDF-outline relevel."""

from __future__ import annotations

import logging
import os
import re
from typing import cast

from ..helpers import compute_verdict
from ..script import (
    decide_rtl,
    normalize_dashes,
)
from .types import Candidate

logger = logging.getLogger(__name__)


_HEADING_RE = re.compile(r"^(#{1,6})(?=\s)", re.MULTILINE)


def _heading_count(md: str) -> int:
    """Count markdown headings in *md* — thin wrapper around ``_HEADING_RE``."""
    return len(_HEADING_RE.findall(md))


def _relevel_headings(md: str) -> str:
    """Promote markdown headings so the shallowest present level becomes H1 (#)."""
    levels = [len(m.group(1)) for m in _HEADING_RE.finditer(md)]
    if not levels:
        return md
    shift = min(levels) - 1
    if shift <= 0:
        return md
    return _HEADING_RE.sub(lambda m: "#" * (len(m.group(1)) - shift), md)


# --- Heading-depth recovery from German-insurance numbering schemes -----------
# Docling's export_to_markdown() renders every section header at a single
# '#'-level, so even when the docling-hierarchical-pdf add-on selects the right
# headings the resulting tree is flat (depth 1) and fails the depth>=2 quality
# gate (HR5 / validate_tree). We re-derive each heading's depth from its
# numbering prefix. Two schemes appear in the validated German insurance corpus
# (2026-05-31):
#   dot notation   (e.g. AKB):  "A.1" -> 2, "A.1.1" -> 3   (bare "A" stays H1)
#   hyphen clauses (e.g. PHV):  "Abschnitt A1" -> 2, "A1-6" -> 3, "A1-6.1" -> 4
# numbering_depth() returns None when no scheme is recognised so generic /
# non-numbered documents keep their existing heading levels untouched.
_HLINE_RE = re.compile(r"^#{1,6}[ \t]+(.*\S)[ \t]*$", re.MULTILINE)
_NUM_SECTION_WORD_RE = re.compile(r"^Abschnitt\s+[A-Z]?\d", re.IGNORECASE)
_NUM_PART_RE = re.compile(r"^(?:Teil|Anhang|Kapitel|Abschnitt)\b", re.IGNORECASE)
_NUM_HYPHEN_RE = re.compile(r"^[A-Z]\d+-\d+(\.\d+)?(?=[ \t]|$)")
_NUM_DOT_RE = re.compile(r"^[A-Z](\.\d+)+(?=[ \t.:]|$)")
_NUM_PARA_RE = re.compile(r"^(?:§\s*)?\d+(\.\d+)+(?=[ \t.:]|$)")

# Arabic legal-gazette numbering (Fix 1 / RFC fizzy-forging-pearl). Arabic laws label
# structure with words, not Latin numbering: الباب/الفصل/القسم/الجزء (chapter/part/
# section) sit at the top; المادة ("Article", optionally bare مادة) is the per-article
# unit one level under. Matched on the Arabic stem (ال- prefix optional) so the depth
# signal is script-native — German/English titles never match these, so this tier is
# additive with zero regression risk on the existing corpus.
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
# RFC-033 D8: second alternative in each pattern matches the mirror-reversed
# (character-order-reversed) form Tesseract produces on some scanned inputs,
# e.g. 'المادة' -> 'ةداملا' (stem reversed, 'ال' prefix reversed to a trailing
# 'لا' suffix). Reversed stems: باب->باب (palindrome), فصل->لصف, قسم->مسق,
# جزء->ءزج, مادة->ةدام, مرسوم->موسرم. RFC-036 D5: same treatment for
# قرار->رارق, قانون->نوناق (gazette-style decree/law markers, part-level).
_AR_PART_RE = re.compile(
    r"^(?:ال)?(?:باب|فصل|قسم|جزء|قرار|مرسوم|قانون)\b"
    r"|^(?:باب|لصف|مسق|ءزج|رارق|موسرم|نوناق)(?:لا)?\b"
)
_AR_ARTICLE_RE = re.compile(r"^(?:ال)?مادة\b|^ةدام(?:لا)?\b")
# RFC-028 D1: char limit for wholesale heading promotion, raised from 60 to
# accommodate Arabic legal headings/titles (66-76+ chars observed).
_AR_HEADING_CHAR_LIMIT = 100
# Zone-3: content-density guard — if injected headings exceed this ratio of
# non-empty lines AND total non-heading content is below the char floor, revert
# the injection to let the flat-prefer path win on content density.
_AR_HEADING_DENSITY_RATIO = 0.30
_AR_HEADING_MIN_CONTENT_CHARS = int(os.getenv("ARABIC_HEADING_MIN_CONTENT_CHARS", "2000"))
# RFC-028 D1: captures the structural marker word plus an immediately
# following PARENTHESIZED numeral (e.g. "مادة (3)") so a fused marker+title
# line exceeding the char limit can be split into a standalone heading (this
# capture) and remaining prose (everything after it). Deliberately requires
# the parenthetical — a bare trailing number ("مادة 2 من هذا القانون...") is
# the shape of a mid-paragraph/citation reference continuing into running
# prose ("Article 2 OF this law..."), not a title, so it must NOT be split
# into a heading (see TestInjectArabicStructuralHeadingsBlockStart's
# wrapped-citation case in test_rfc027_d4.py).
_AR_MARKER_CAPTURE_RE = re.compile(
    r"^(?:ال)?(?:باب|فصل|قسم|جزء|مادة|قرار|مرسوم|قانون)\s*\(\s*\d+\s*\)"
)

# Zone-3: _detect_arabic_reversal DELETED — replaced by decide_rtl (morphology+
# readability scorer) which is strictly stronger than the vocab-list method.
# _AR_KNOWN_WORDS, _AR_KNOWN_WORDS_REVERSED, _AR_REVERSAL_SAMPLE_THRESHOLD removed.


def _inject_arabic_structural_headings(md: str) -> str:
    """Promote al-bab/al-fasl/al-maddah lines to '#' headings (RFC-027 D4).

    Docling never classifies Arabic structural markers as ``SectionHeaderItem``s
    (English equivalents like "Chapter"/"Article" ARE detected), so these lines
    stay plain prose and the heading-depth recovery chain — which can only
    re-level EXISTING headings via ``_AR_PART_RE``/``_AR_ARTICLE_RE`` in
    ``numbering_depth`` — has nothing to work with, leaving the tree flat.

    Promotion is gated ONLY on the marker regex matching the line's **start**
    (RFC-028 D1) — a preceding-blank-line requirement is redundant/harmful for
    continuous OCR output, where scanned Arabic legal text flows without
    blank-line separators between consecutive مادة articles, and previously
    meant only the FIRST marker in a run was ever promoted. The anchor
    protects against mid-paragraph references like "...المشار إليها في
    المادة 5 من هذا القانون..." since those never start their line with the
    marker.

    A matching line up to 100 chars (RFC-028 D1: raised from 60 to
    accommodate Arabic legal headings like "المادة (3) نطاق التطبيق", which
    run 66-76+ chars) is promoted wholesale. A matching line that exceeds 100
    chars is split: the marker (plus any immediately-following numeral/
    parenthetical, e.g. "مادة (3)") becomes a standalone heading and the
    remaining prose is kept as a following line rather than dropped. Depth is
    left to the existing ``_relevel_by_containment``/``_relevel_by_numbering``
    chain.

    RFC-033 D8: when ``decide_rtl`` judges ``md`` to be Tesseract
    mirror-reversed, each line is character-reversed before pattern
    matching (the regexes are forward-oriented) but the ORIGINAL line text —
    whatever Tesseract actually produced — is what gets promoted to a
    heading; only the matching step operates on the flipped text.

    Zone-3 content-density guard: after injection, if injected headings
    exceed 30% of non-empty lines AND total non-heading content is below
    ``ARABIC_HEADING_MIN_CONTENT_CHARS`` (default 2000), the injection is
    reverted — the sparse injected headings would create a false
    structural-depth signal that blocks the flat-prefer recovery path.
    The threshold is configurable via env var."""
    reversed_ocr = decide_rtl(md).reversed
    lines = md.split("\n")
    out: list[str] = []
    _injected_count = 0
    for line in lines:
        t = line.strip()
        if t and not _HEADING_RE.match(t):
            match_t = t[::-1] if reversed_ocr else t
            is_part = bool(_AR_PART_RE.match(match_t))
            is_article = is_part is False and bool(_AR_ARTICLE_RE.match(match_t))
            if is_part or is_article:
                level = "#" if is_part else "##"
                if len(t) <= _AR_HEADING_CHAR_LIMIT:
                    out.append(f"{level} {t}")
                    _injected_count += 1
                    continue
                marker_match = _AR_MARKER_CAPTURE_RE.match(match_t)
                if marker_match:
                    if reversed_ocr:
                        marker_len = marker_match.end()
                        marker = t[len(t) - marker_len :]
                        remainder = t[: len(t) - marker_len].strip()
                    else:
                        marker = marker_match.group(0).strip()
                        remainder = t[marker_match.end() :].strip()
                    out.append(f"{level} {marker}")
                    _injected_count += 1
                    if remainder:
                        out.append(remainder)
                    continue
        out.append(line)

    # Zone-3: content-density guard — revert injection when headings
    # dominate sparse content, preventing false structural-depth signals.
    if _injected_count > 0:
        _non_empty = sum(1 for ln in out if ln.strip())
        if _non_empty > 0 and (_injected_count / _non_empty) > _AR_HEADING_DENSITY_RATIO:
            # Check content chars excluding injected heading lines
            _content_chars = sum(
                len(ln.strip())
                for ln in out
                if ln.strip() and not _HEADING_RE.match(ln.strip())
            )
            if _content_chars < _AR_HEADING_MIN_CONTENT_CHARS:
                logger.info(
                    "Zone-3: reverting Arabic heading injection — "
                    "injected %d headings in %d non-empty lines "
                    "(ratio=%.2f), content chars=%d < %d",
                    _injected_count,
                    _non_empty,
                    _injected_count / _non_empty,
                    _content_chars,
                    _AR_HEADING_MIN_CONTENT_CHARS,
                )
                # lazy import to avoid circular dependency
                from ..metrics import ARABIC_HEADING_INJECTION_REVERTED
                ARABIC_HEADING_INJECTION_REVERTED.inc()
                return md  # revert: return original unmodified markdown

    return "\n".join(out)


_DE_ZIFFER_RE = re.compile(r"^(?:Ziffer|Ziff\.)\s+\d+")

# Line-start anchoring alone does not stop a *clause body* that opens with its
# own number ("Ziffer 3 gilt entsprechend für ...", "Article (5) shall apply
# where ...") from being promoted, which would swallow a whole paragraph into a
# heading title. Real clause titles are short; the limit is deliberately well
# above the longest realistic one (German AHB titles such as "Ziffer 7
# Versicherungsfall, Obliegenheiten des Versicherungsnehmers ..." run past 100
# chars, so the Arabic function's 100-char limit would clip them) and only
# blocks obvious running prose. Over-limit lines are left untouched — nothing
# is split or dropped.
_CLAUSE_HEADING_CHAR_LIMIT = 200


def _inject_german_clause_headings(md: str) -> str:
    """Promote 'Ziffer N'/'Ziff. N' lines to '##' headings (RFC-033 D5).

    German AHB/insurance documents use Ziffer/Ziff. clause numbering that
    Docling does not detect as headings. The line-start anchor prevents
    mid-sentence references like "see Ziffer 1 above" from being promoted, and
    ``_CLAUSE_HEADING_CHAR_LIMIT`` prevents a paragraph that merely opens with a
    clause reference from becoming a heading."""
    lines = md.split("\n")
    out = []
    for line in lines:
        t = line.strip()
        if (
            t
            and len(t) <= _CLAUSE_HEADING_CHAR_LIMIT
            and not _HEADING_RE.match(t)
            and _DE_ZIFFER_RE.match(t)
        ):
            out.append(f"## {t}")
            continue
        out.append(line)
    return "\n".join(out)


_EN_ARTICLE_PROSE_RE = re.compile(r"^Article\s*\(\s*\d+\s*\)", re.IGNORECASE)


def _inject_english_article_headings(md: str) -> str:
    """Promote 'Article (N)' lines to '##' headings (RFC-033 D5).

    UAE/English legal documents use parenthesised 'Article (N)' numbering
    that Docling sometimes misses entirely as a heading, leaving it as plain
    prose. The line-start anchor prevents mid-sentence references like "see
    Article (1) above" from being promoted, and ``_CLAUSE_HEADING_CHAR_LIMIT``
    prevents a paragraph that merely opens with an article reference from
    becoming a heading."""
    lines = md.split("\n")
    out = []
    for line in lines:
        t = line.strip()
        if (
            t
            and len(t) <= _CLAUSE_HEADING_CHAR_LIMIT
            and not _HEADING_RE.match(t)
            and _EN_ARTICLE_PROSE_RE.match(t)
        ):
            out.append(f"## {t}")
            continue
        out.append(line)
    return "\n".join(out)


def numbering_depth(title: str) -> int | None:
    """Infer a 1-based heading depth from a German-insurance numbering prefix.

    Returns None when no recognised numbering scheme is present, so the caller
    leaves such headings at their existing level."""
    t = title.strip()
    # Arabic structural words (Fix 1): chapter/part -> top, Article -> one level under.
    if _AR_PART_RE.match(t):
        return 1
    if _AR_ARTICLE_RE.match(t):
        return 2
    # "Abschnitt A1" is a section nested one level under its "Teil".
    if _NUM_SECTION_WORD_RE.match(t):
        return 2
    # Part / appendix words sit at the top.
    if _NUM_PART_RE.match(t):
        return 1
    # Hyphen clauses: "A1-6" -> 3, "A1-6.1" -> 4.
    if _NUM_HYPHEN_RE.match(t):
        return 4 if "." in t.split()[0] else 3
    # Dot notation: "A.1" -> 2, "A.1.1" -> 3.
    m = _NUM_DOT_RE.match(t)
    if m:
        return 1 + m.group(0).count(".")
    # Plain paragraph / numeric sub-sections: "3.1" -> 2, "§3.1.2" -> 3.
    m = _NUM_PARA_RE.match(t)
    if m:
        return 1 + m.group(0).count(".")
    return None


def _relevel_by_numbering(md: str) -> str:
    """Override each markdown heading's '#'-level from its numbering prefix.

    Headings whose title has no recognised numbering prefix are left unchanged,
    so this is safe to run after ``_relevel_headings`` on any corpus."""

    def repl(m: re.Match[str]) -> str:
        title = m.group(1)
        depth = numbering_depth(title)
        if depth is None:
            return m.group(0)
        # Clamp to the markdown heading range [1, 6]; deeply nested numbered
        # sections (e.g. "A.1.1.1.1.1") would otherwise emit 7+ '#'s, which is
        # not a valid heading. Mirrors the clamp in _relevel_by_containment.
        return "#" * max(1, min(6, depth)) + " " + title

    return _HLINE_RE.sub(repl, md)


# --- Heading-depth recovery by numbering-prefix CONTAINMENT (no per-scheme table) -
# Every STRUCTURAL depth signal Docling exposes on this corpus is flat:
# SectionHeaderItem.level, body-tree traversal depth, and the PDF outline are all
# level 1 (verified 2026-05-31). So depth must be inferred from the heading TEXT.
# We segment each heading's leading numbering LABEL into atomic components
# ("A.1.1"->[A,1,1]; "Abschnitt A1"->[A,1]; bare prose title -> []) and set
# depth = 1 + length of the longest OTHER present label that is a proper prefix.
# This nests an unseen numbering style without a hardcoded regex table, so it is
# the PRIMARY depth source; numbering_depth() above is kept only as a last-resort
# fallback for the degenerate case where containment stays flat.

# Structural words that introduce a numbering label. We collapse the spaced-out
# Docling rendering ("T e i l   A") before matching, so the regex sees "TeilA".
_WORD_RE = re.compile(r"^(teil|anhang|abschnitt|kapitel)\b", re.IGNORECASE)
# Arabic structural words consumed the same way as the Latin _WORD_RE (Fix 1): the
# label that NESTS is the number/letter that follows المادة/الباب/الفصل/القسم/الجزء/مرسوم.
# RFC-033 D8: reversed-form alternative, same construction as _AR_PART_RE/
# _AR_ARTICLE_RE above.
_AR_WORD_RE = re.compile(
    r"^(?:ال)?(?:مادة|باب|فصل|قسم|جزء|مرسوم)\b|^(?:ةدام|باب|لصف|مسق|ءزج|موسرم)(?:لا)?\b"
)
# English "Article N" / "Art. N" and section-symbol "§ N" headings (RFC-015 D3
# Part B / task 1.4): without this, _segment_label() rejects "Article 5" as
# prose (its first alnum component is the multi-letter word "Article", which
# fails the single-letter-or-digit label-first-component gate below) and
# returns [] — an unlabelled/"bare title" heading whose ORIGINAL doc-derived
# level is left untouched by _relevel_by_containment. On corpora where that
# original level is deeply nested under an unrelated sub-bullet, Articles 3-6
# inherit that nesting verbatim ("staircase" mis-nesting) instead of getting an
# explicit depth. Recognising the "Art(icle|.) N" / "§ N" prefix as a
# structural word (consumed the same way as Teil/Abschnitt/Kapitel above)
# lets the bare number become the label, so containment assigns depth 1.
# The number may also be parenthesised — "Article (47)", "§ (12)" — as used by
# UAE/English legal corpora (RFC-033 D4); the optional '(' is matched here and
# stripped downstream by _segment_label's head.strip("()").
_ARTICLE_RE = re.compile(r"^(?:Art(?:icle|\.)\s+\(?\s*\d+|§\s*\(?\s*\d+)", re.IGNORECASE)
_ARTICLE_WORD_RE = re.compile(r"^(?:Art(?:icle|\.)|§)\s*", re.IGNORECASE)


def _collapse_spaced(text: str) -> str:
    """Collapse Docling's letter-spaced headings: 'T e i l   A' -> 'Teil A'.

    Docling renders these with SINGLE spaces between the letters of a word and a
    WIDER gap (2+ spaces) between words. We split on runs of 2+ spaces to recover
    word boundaries, then glue single-spaced letters inside each chunk. A '-'
    surrounded by spaces is kept as a separator word. Ordinary headings (whose
    tokens are multi-letter) are returned unchanged.
    """
    raw_toks = text.split()
    if not (len(raw_toks) >= 4 and sum(1 for t in raw_toks if len(t) == 1) >= len(raw_toks) * 0.6):
        return text
    # split on 2+ spaces -> word-level chunks; within a chunk, single chars glue.
    chunks = re.split(r"\s{2,}", text.strip())
    out = []
    for chunk in chunks:
        ctoks = chunk.split()
        buf = []
        for t in ctoks:
            if t == "-":
                # A '-' inside a chunk is a label separator (e.g. clause code
                # "A1-6.1" rendered spaced as "A 1 - 6 . 1"): glue it to the
                # surrounding letters with NO spaces so the clause code stays
                # intact. Surrounding it with spaces would break hyphenated
                # clause-code detection ("A1-6.1" -> "A1- 6.1").
                buf.append("-")
            else:
                buf.append(t)
        if buf:
            out.append("".join(buf))
    return " ".join(out)


def _split_alnum(tok: str) -> list[str]:
    """Split an alnum label token at every letter<->digit boundary and (..) group.

    "A1"   -> ["A","1"]      "A(GB)" -> ["A","GB"]      "B4" -> ["B","4"]
    "A"    -> ["A"]          "A(GB)1"-> ["A","GB","1"]
    """
    # pull out a parenthesised group first
    parts: list[str] = []
    m = re.match(r"^([A-Za-z]+)(?:\(([A-Za-z0-9]+)\))?(\d+)?$", tok)
    if m:
        if m.group(1):
            parts.append(m.group(1))
        if m.group(2):
            parts.append(m.group(2))
        if m.group(3):
            parts.append(m.group(3))
        return parts
    # fallback: generic letter/digit run split
    return list(re.findall(r"[A-Za-z]+|\d+", tok))


def _segment_label(title: str) -> list[str]:
    """Segment a heading's leading numbering label into atomic components.

    Returns [] when the heading carries no recognisable label (a bare title).
    Word-prefix rule: a leading structural word (Teil/Anhang/Abschnitt/Kapitel)
    is consumed; the label that follows it is what nests. "Teil A"->[A];
    "Abschnitt A1"->[A,1]; "A1-6.1"->[A,1,6,1]; "A.1.1"->[A,1,1];
    "A(GB)-1"->[A,GB,1]; "Versicherte Personen"->[].
    """
    t = _collapse_spaced(title.strip())
    # consume an optional leading structural word (Latin or Arabic, Fix 1)
    wm = _WORD_RE.match(t)
    if wm:
        t = t[wm.end() :].lstrip(" -")
    elif _ARTICLE_RE.match(t):
        # "Article 5" / "Art. 5" / "§ 12" (RFC-015 D3 Part B): consume just the
        # word/symbol, leaving the bare number as the label so it gets an
        # explicit containment depth instead of falling through as prose.
        t = _ARTICLE_WORD_RE.sub("", t, count=1)
    else:
        awm = _AR_WORD_RE.match(t)
        if awm:
            t = t[awm.end() :].lstrip(" -:()")
    # Normalise Arabic-Indic digits so an Arabic article number ("المادة ٩") is
    # recognised by the same digit-aware label logic as Latin numbering. No-op on
    # ASCII, so German/English labels are byte-for-byte unaffected.
    t = t.translate(_AR_DIGITS)
    # the label is the leading run of [alnum . - ( )] up to the first space
    # that starts the descriptive title. Grab the first whitespace-delimited tok.
    head = t.split(maxsplit=1)[0] if t else ""
    if not head:
        return []
    # The label may itself contain '.', '-' and '()' separators.
    # Stop the label at a separator that is followed by a non-label char? Simpler:
    # the head token IS the label candidate. Validate it starts with a letter
    # and contains at least one alnum.
    # Strip trailing punctuation like ':' or '.' used as a terminator? Keep dots
    # that are internal (A.1.) — drop a single trailing '.'/':'.
    head = head.rstrip(":")
    # Strip wrapping parentheses on the whole token ("(9)" -> "9") so a parenthesised
    # Arabic article number survives the leading-char gate. Internal parens of a Latin
    # label like "A(GB)1" are untouched (no leading/trailing paren on the token).
    head = head.strip("()")
    # A label must begin with a letter (clause code "A1-6.1") OR a digit
    # (numeric section "3.1") to be a recognisable numbering label. Rejecting
    # digit-led heads here would drop numeric headings before the digit-aware
    # validation below, leaving that part of the hierarchy flat.
    if not re.match(r"^[A-Za-z0-9]", head):
        return []
    comps: list[str] = []
    # split on the structural separators '.', '-'
    for seg in re.split(r"[.\-]", head):
        seg = seg.strip()
        if not seg:
            continue
        sub = _split_alnum(seg)
        if not sub:
            return []  # contains a non-alnum chunk we don't understand -> no label
        comps.extend(sub)
    # Reject degenerate labels: a single letter that is actually a word start
    # is fine ("A"), but require the whole head to be alnum/sep only — if the
    # head had spaces stripped we already isolated one token, so this holds.
    # Guard: a pure single-letter label is valid (top section).
    if not comps:
        return []
    # Reject labels that are clearly prose (e.g. first word "Was", "Wer"): a real
    # label is short and its first component is a single letter OR is all digits.
    first = comps[0]
    if not (re.fullmatch(r"[A-Za-z]", first) or first.isdigit()):
        return []
    # And the whole token must be short-ish (a clause code, not a sentence word).
    if len(head) > 14:
        return []
    return comps


def _containment_depths(titles: list[str]) -> list[int | None]:
    """Depth of each heading via numbering-prefix containment (grammar inference).

    depth(i) = 1 + length of the longest OTHER label that is a proper prefix of
    label(i). Bare-title headings (label == []) return None so the caller leaves
    that heading's existing level untouched."""
    labels = [_segment_label(t) for t in titles]
    label_set = [tuple(lbl) for lbl in labels]
    present = {lbl for lbl in label_set if lbl}
    depths: list[int | None] = []
    for lab in label_set:
        if not lab:
            depths.append(None)
            continue
        # longest proper prefix that is itself a present label
        best = 0
        for k in range(len(lab) - 1, 0, -1):
            if lab[:k] in present:
                best = k
                break
        depths.append(best + 1)
    return depths


def _relevel_by_containment(md: str) -> str:
    """Override each markdown heading's '#'-level from numbering-prefix containment.

    Headings whose title carries no label (containment depth None) are left
    exactly as-is, so this is safe to run after ``_relevel_headings`` on any
    corpus. Non-heading text and spacing are preserved verbatim."""
    matches = list(_HLINE_RE.finditer(md))
    if not matches:
        return md
    depths = _containment_depths([m.group(1) for m in matches])
    out: list[str] = []
    pos = 0
    for m, depth in zip(matches, depths, strict=False):
        out.append(md[pos : m.start()])
        if depth is None:
            out.append(m.group(0))  # no label -> keep existing level
        else:
            out.append("#" * max(1, min(6, depth)) + " " + m.group(1))
        pos = m.end()
    out.append(md[pos:])
    return "".join(out)


def _max_heading_level(md: str) -> int:
    """Largest markdown heading '#'-run length present, or 0 if none."""
    levels = [len(m.group(1)) for m in _HEADING_RE.finditer(md)]
    return max(levels) if levels else 0


# --- Heading-depth recovery from the PDF OUTLINE (last resort for flat prose) --
# German IPID / FAQ insurance PDFs (Katzen-/Hunde-/Pferde-Kranken/-OP, Meuten,
# Halterhaftpflicht) have headings that are ALL bare prose with no numbering
# prefix, so _relevel_by_containment and _relevel_by_numbering both leave every
# heading at H1 (max_heading_level == 1) -> a FALSE depth<2 rejection of a
# legitimately-structured document. Their only author-declared structure is the
# PDF outline (PyMuPDF get_toc). We map each rendered heading to the outline
# section its PAGE falls in (text matching alone is insufficient: a TOC title such
# as "Informationsblatt zu Versicherungsprodukten" is often NOT in Docling's
# rendered heading set, so it must be located/injected by PAGE, never by text).
# A rendered heading whose text matches its section title becomes that section's
# anchor (H{toc_level}); every other heading in the section becomes a child
# (H{deepest_covering_level + 1}); a covered section title Docling never rendered
# is injected verbatim at its declared level. Cat A numbered docs never reach this
# step (the guard requires max_heading_level < 2 after the numbering chain); Cat D
# leaflets have no usable outline (<2 entries) and stay a legitimate depth<2
# rejection (HR5 — the quality gate is never weakened).
_OUTLINE_MIN_ANCHOR_ALNUM = (
    8  # min alnum chars for a substring title match (avoids short-title false positives)
)


def _outline_norm(s: str) -> str:
    """Normalise a title to lowercase alphanumerics for cross-source matching.

    Unifies dashes, collapses embedded newlines, then strips to [a-z0-9] — the
    same normalisation idiom the add-on's infer() / _patch_hierarchical_infer use,
    so a PyMuPDF outline title reconciles with a Docling-rendered heading despite
    whitespace, dash-variant and punctuation differences."""
    return re.sub(r"[^a-z0-9]", "", normalize_dashes((s or "").replace("\n", " ")).lower())


def _title_matches(norm_heading: str, norm_section: str) -> bool:
    """True when a rendered heading IS its outline section's title.

    Exact normalised equality, or a substring match when the shorter string is
    substantial (>= _OUTLINE_MIN_ANCHOR_ALNUM alnum chars) — this tolerates
    Docling rendering a longer heading than the TOC title (or vice versa) without
    the short-title false positives a bare endswith/startswith would admit."""
    if not norm_heading or not norm_section:
        return False
    if norm_heading == norm_section:
        return True
    shorter, longer = (
        (norm_heading, norm_section)
        if len(norm_heading) <= len(norm_section)
        else (norm_section, norm_heading)
    )
    return len(shorter) >= _OUTLINE_MIN_ANCHOR_ALNUM and shorter in longer


def _collect_heading_pages(doc) -> dict[str, list[int]]:
    """Map normalised-heading-text -> [page_no, ...] from a Docling document.

    Page numbers are 1-indexed (pypdfium2 backend: prov[0].page_no == page index
    + 1), matching PyMuPDF get_toc's page field. Pages are appended in document
    iteration order so repeated identical headings (e.g. the 3x "Besondere
    Bedingungen ..." chapters in Hundehalterhaftpflicht) can be disambiguated by a
    consumption pointer in _apply_outline_levels. Call this on the document state
    matching the markdown the caller will relevel (the RAW pre-add-on document on
    the over-prune raw_md fallback path; the post-add-on document otherwise) so the
    page map and the heading set stay in sync."""
    from collections import defaultdict

    from docling_core.types.doc.document import SectionHeaderItem

    pages: dict[str, list[int]] = defaultdict(list)
    for item, _ in doc.iterate_items(with_groups=False):
        if not isinstance(item, SectionHeaderItem) or not item.prov:
            continue
        key = _outline_norm(item.text or "")
        if key:
            pages[key].append(item.prov[0].page_no)
    return dict(pages)


def _read_pdf_outline(pdf_path: str) -> tuple[list[tuple[int, str, int]], int]:
    """Read the PDF bookmark outline as [(level, title, page_1indexed), ...] in
    document (outline-tree) order, plus the total page count.

    Uses pypdfium2 (BSD-3/Apache-2), NOT PyMuPDF (AGPL-3.0, HR4): this is the only
    first-party structural read on the default / VLM-bound PDF path, so keeping it
    off AGPL means zero first-party AGPL code touches a document the gate may later
    escalate (RFC-004 Q2). pypdfium2 reports level and page index 0-based; we add
    +1 to each to preserve the PyMuPDF get_toc convention the pure consumer
    (_apply_outline_levels) expects. The +1 on level is LOAD-BEARING: a 0-based
    level would collapse depth-1 and depth-2 sections through max(1, min(6, level)).
    A bookmark with no resolvable page destination maps to page 0 (a sentinel that
    never falls inside a 1-indexed section's [start, end) range).

    Returns ([], 0) when the outline has fewer than 2 entries — no usable
    structural signal, so the caller leaves the markdown flat and the quality gate
    rejects it legitimately (Cat D leaflets). Document order is preserved (NOT
    sorted by page): section extents are computed by outline NESTING (the next
    entry whose level <= the current level), which requires reading order."""
    import pypdfium2 as pdfium

    pdoc = pdfium.PdfDocument(pdf_path)
    try:
        toc: list[tuple[int, str, int]] = []
        for bm in pdoc.get_toc():
            dest = bm.get_dest()
            page_index = dest.get_index() if dest is not None else None
            page_1based = (page_index + 1) if page_index is not None else 0
            toc.append((bm.level + 1, bm.get_title() or "", page_1based))
        total_pages = len(pdoc)
    finally:
        pdoc.close()
    if len(toc) < 2:
        return [], 0
    return toc, total_pages


# Complexity grandfathered (outline relevel, depth<2 fix); see pyproject [tool.ruff].
def _apply_outline_levels(  # noqa: C901, PLR0915
    md: str,
    heading_pages: dict[str, list[int]],
    toc: list[tuple[int, str, int]],
    total_pages: int,
) -> str:
    """PURE last-resort relevel: assign each markdown heading an H-level from the
    PDF-outline section its page falls in, injecting any outline section title that
    Docling never rendered. No Docling/PyMuPDF deps -> directly unit-testable.

    Inputs:
      heading_pages : {normalised_title -> [page_no, ...]} in document order
      toc           : [(level, title, page_1indexed), ...] in outline order
      total_pages   : page count (bounds the last section's extent)

    Section extents follow outline NESTING: section i spans [page_i, next_start)
    where next_start is the page of the next entry whose level <= level_i (or
    total_pages + 1 for the last). A page may be covered by several nested sections
    (a parent and its child); a heading is assigned its DEEPEST covering section's
    level + 1 (a child), unless its text matches one of its covering section titles
    (an anchor -> that section's level). A covered section whose title no rendered
    heading matched has its title injected, verbatim, at its declared level before
    the first rendered heading in its range.

    Returns md unchanged when there is no usable outline / no headings, or when the
    rewrite still yields depth<2 (so the gate rejects it rather than receiving a
    worse tree — HR5)."""
    if not toc:
        return md
    matches = list(_HLINE_RE.finditer(md))
    if not matches:
        return md

    # 1. Sections as half-open page ranges [start, end) respecting outline nesting.
    n = len(toc)
    sections: list[dict] = []
    for i, (level, title, start) in enumerate(toc):
        end = total_pages + 1
        for j in range(i + 1, n):
            if toc[j][0] <= level:
                end = toc[j][2]
                break
        sections.append(
            {
                "level": max(1, min(6, level)),
                "norm": _outline_norm(title),
                "raw": re.sub(r"\s+", " ", normalize_dashes(title)).strip(),
                "start": start,
                "end": end,  # exclusive
                "matched": False,  # a rendered heading was this section's title
            }
        )

    # 2. Consumption pointer for repeated identical headings (document order).
    from collections import deque

    page_q: dict[str, deque] = {k: deque(v) for k, v in heading_pages.items()}

    def _pop_page(norm_title: str) -> int | None:
        q = page_q.get(norm_title)
        return q.popleft() if q else None

    # 3. Assign a level to every rendered heading; record the first rendered
    #    heading offset per covering section (the injection insertion point).
    new_levels: list[int | None] = []
    first_off: dict[int, int] = {}
    for m in matches:
        norm_h = _outline_norm(m.group(1))
        page_no = _pop_page(norm_h)
        if page_no is None:
            new_levels.append(None)  # no provenance -> leave at current level
            continue
        covering = [(idx, s) for idx, s in enumerate(sections) if s["start"] <= page_no < s["end"]]
        if not covering:
            new_levels.append(None)  # cover/frontmatter before the first section
            continue
        for idx, _s in covering:
            first_off.setdefault(idx, m.start())
        # anchor = deepest covering section whose title this heading matches
        anchor = None
        for _idx, s in sorted(covering, key=lambda c: -c[1]["level"]):
            if _title_matches(norm_h, s["norm"]):
                anchor = s
                break
        if anchor is not None:
            new_levels.append(anchor["level"])
            anchor["matched"] = True
        else:
            deepest = max(covering, key=lambda c: c[1]["level"])[1]
            new_levels.append(max(1, min(6, deepest["level"] + 1)))

    # 4. Inject section titles Docling never rendered (e.g. the IPID overview).
    injections: dict[int, list[tuple[int, str]]] = {}
    for idx, s in enumerate(sections):
        if s["matched"]:
            continue
        off = first_off.get(idx)
        if off is None:
            continue  # no rendered heading in range -> nothing to anchor to
        line = "#" * s["level"] + " " + s["raw"] + "\n"
        injections.setdefault(off, []).append((s["level"], line))

    # 5. Splice: rewrite levels + emit injected titles (shallowest level first).
    out: list[str] = []
    pos = 0
    for m, lvl in zip(matches, new_levels, strict=False):
        out.append(md[pos : m.start()])
        if m.start() in injections:
            for _lvl, line in sorted(injections[m.start()]):
                out.append(line)
        out.append(m.group(0) if lvl is None else "#" * lvl + " " + m.group(1))
        pos = m.end()
    out.append(md[pos:])
    result_md = "".join(out)

    if _max_heading_level(result_md) < 2:
        return md  # outline gave no usable depth -> stay a legitimate rejection
    return result_md


def _relevel_by_outline(md: str, heading_pages: dict[str, list[int]], pdf_path: str) -> str:
    """Thin I/O wrapper: read the PDF outline, then apply outline-derived levels.

    Last-resort depth recovery for flat-prose docs after the numbering chain fails.
    Never fatal (the caller also wraps it); returns md unchanged on any failure or
    when the outline has <2 entries (Cat D leaflet)."""
    toc, total_pages = _read_pdf_outline(pdf_path)
    if not toc:
        return md
    result_md = _apply_outline_levels(md, heading_pages, toc, total_pages)
    logger.info(
        "_relevel_by_outline: applied outline page-spine to %s (%d sections, max_level %d)",
        pdf_path,
        len(toc),
        _max_heading_level(result_md),
    )
    return result_md


def _splice_landscape_fallback(
    md: str, landscape_fallback_pages: list[dict], heading_pages: dict[str, list[int]]
) -> str:
    """RFC-036 D0d: insert landscape-fallback markdown at its original page
    position in the document's block sequence, instead of appending after the
    last block.

    Insertion offset = the start of the first heading (in ``md``, matched to a
    page via ``heading_pages`` the same way ``_apply_outline_levels`` does)
    whose page number exceeds the fallback page's ``page_no`` — i.e. the next
    heading following that page. Falls back to document end when no such
    heading exists (the fallback page is on/after the document's last heading),
    which reproduces the prior append-at-end behaviour for that case. A no-op
    when ``landscape_fallback_pages`` is empty — documents that never trigger
    the landscape path see no ordering change."""
    if not landscape_fallback_pages:
        return md
    from collections import deque

    matches = list(_HLINE_RE.finditer(md))
    page_q: dict[str, deque] = {k: deque(v) for k, v in heading_pages.items()}
    heading_offsets: list[tuple[int, int | None]] = []
    for m in matches:
        norm_h = _outline_norm(m.group(1))
        q = page_q.get(norm_h)
        heading_offsets.append((m.start(), q.popleft() if q else None))

    inserts: list[tuple[int, int, str]] = []
    for p in landscape_fallback_pages:
        block = p["markdown"].strip()
        if not block:
            continue
        # Fallback page_no is PyMuPDF 0-indexed (_tag_landscape_pages_for_fallback);
        # heading_pages values are Docling prov.page_no, 1-indexed — same
        # correction as _landscape_pages_below_threshold.
        fallback_page_1idx = p["page_no"] + 1
        insert_at = len(md)
        for offset, page_no in heading_offsets:
            if page_no is not None and page_no > fallback_page_1idx:
                insert_at = offset
                break
        inserts.append((insert_at, fallback_page_1idx, block))

    # Descending offset so earlier insertions don't shift later ones; the
    # secondary page_no key keeps same-offset blocks in ascending page order
    # in the final document (last-inserted lands first at a shared offset).
    for insert_at, _page, block in sorted(inserts, reverse=True):
        md = md[:insert_at] + "\n\n" + block + "\n\n" + md[insert_at:]
    return md


def _has_structural_depth(md: str) -> bool:
    """Structural proxy for validate_tree's ``node_count>=3 AND depth>=2`` — used
    only to SELECT the better markdown SOURCE; the real HR5 gate still runs in the
    client. md_to_tree makes one tree node per heading and nests by '#'-level, so a
    depth>=2 tree needs a heading at level>=2 and node_count>=3 needs >=3 headings.
    Conservative by design: a false 'pass' here is still caught by the real gate."""
    return _max_heading_level(md) >= 2 and _heading_count(md) >= 3


def _md_to_structure(md: str) -> list:
    """Build a minimal tree structure from markdown headings for ``classify_verdict``.

    Each heading becomes a node with ``title``/``text``/``nodes`` keys matching
    the tree format that :func:`helpers.classify_verdict`,
    :func:`helpers._tree_node_count`, :func:`helpers._tree_depth`, and
    :func:`helpers._tree_max_leaf_ratio` expect.  Body text between headings
    becomes the ``text`` of the preceding heading node.

    This is a lightweight synchronous alternative to the full ``md_to_tree``
    pipeline (which is async and requires the external ``pageindex`` tool),
    producing just enough structure for ``classify_verdict``'s structural
    metrics.  Text before the first heading is attached to a synthetic root
    node only when at least one heading follows — otherwise the whole document
    is a single flat node (no headings).
    """
    structure: list[dict] = []
    stack: list[tuple[int, dict]] = []  # (level, node)
    text_buf: list[str] = []

    def _flush_text() -> None:
        body = "\n".join(text_buf).strip()
        text_buf.clear()
        if not body:
            return
        if stack:
            # Append to the most recent heading's text
            prev = stack[-1][1].get("text", "")
            stack[-1][1]["text"] = (prev + "\n" + body).strip() if prev else body
        else:
            # Text before any heading — will be attached to a synthetic root
            # only if headings follow (handled after the loop).
            structure.append({"title": "", "text": body, "nodes": []})

    for line in md.split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            _flush_text()
            level = len(m.group(1))
            title = line[m.end() :].strip()
            node: dict = {"title": title, "text": "", "nodes": []}

            # Pop stack until we find a strictly shallower (parent) level.
            while stack and stack[-1][0] >= level:
                stack.pop()

            if stack:
                stack[-1][1]["nodes"].append(node)
            else:
                structure.append(node)

            stack.append((level, node))
        else:
            text_buf.append(line)

    _flush_text()
    return structure


# Zone-3: verdict ranking for source selection.  Lower rank = better verdict.
_VERDICT_RANK: dict[str, int] = {"PASS": 0, "MARGINAL": 1, "FAIL": 2}


def _recover_heading_depth(md: str, heading_pages: dict[str, list[int]], pdf_path: str) -> str:
    """Run the full heading-depth recovery chain on ONE markdown source.

    containment (PRIMARY numbering-prefix depth) -> numbering (per-scheme regex
    FALLBACK) -> PDF outline (LAST RESORT for numberless flat prose). Each step
    runs only if the prior left the tree degenerately flat (max_heading_level<2).
    Dashes are normalised first so hyphen clause codes (A1-6.1) parse. The outline
    step is wrapped so it is never fatal."""
    md = _relevel_by_containment(_relevel_headings(normalize_dashes(md)))
    if _max_heading_level(md) < 2:
        md = _relevel_by_numbering(md)
    if _max_heading_level(md) < 2:
        try:
            md = _relevel_by_outline(md, heading_pages, pdf_path)
        except Exception as exc:
            logger.warning(
                "_relevel_by_outline failed for %s (%s); leaving flat markdown",
                pdf_path,
                exc,
            )
    return md


def _candidate_from_document(
    md: str, heading_pages: dict[str, list[int]], pdf_path: str
) -> Candidate:
    """Build and depth-recover a single pipeline candidate.

    Runs ``_build_candidate`` (injection + normalisation) then
    ``_recover_heading_depth`` (containment -> numbering -> outline) and
    returns an immutable ``Candidate`` bundling the result with its heading-
    page map, pre-computed structural-depth flag, and ``classify_verdict``
    result (Zone-3: single verdict authority).

    Pure function — does NOT call ``_collect_heading_pages`` (the caller
    owns that) and does NOT touch ``extraction_stages``.
    """
    # Lazy import to avoid circular dependency during module split.
    from .pipeline import _build_candidate

    built, rtl_decision = _build_candidate(md)
    recovered = _recover_heading_depth(built, heading_pages, pdf_path)
    has_depth = _has_structural_depth(recovered)

    # Zone-3: compute_verdict on a lightweight markdown-derived structure
    # so source selection uses the single verdict authority instead of the
    # structural-depth proxy alone.  source_selection=True skips _clamp_pass
    # caps that are meaningful only for the final persisted verdict.
    verdict = ""
    if has_depth:
        try:
            structure = _md_to_structure(recovered)
            _vr = compute_verdict(structure, "", source_selection=True)
            verdict = _vr.verdict
        except Exception as exc:
            logger.debug(
                "compute_verdict in _candidate_from_document failed for %s (%s); "
                "falling back to structural-depth proxy",
                pdf_path,
                exc,
            )

    return Candidate(
        md=recovered,
        heading_pages=heading_pages,
        has_depth=has_depth,
        verdict=verdict,
        rtl_decision=rtl_decision,
    )


# RFC-015 D5d: a valid sub-clause component is a digit run with an OPTIONAL single
# lowercase-letter suffix ("10a"), OR a lone lowercase letter ("a"). The lone-letter
# alternative is load-bearing: _segment_label() splits a letter suffix into its OWN
# component ("A.1.1a" -> [A,1,1,a]; "§ 5a" -> [5,a]), so the blueprint's literal
# `\d+[a-z]?` alone would never match the standalone trailing "a" in its own worked
# example ('7','10','a'). Widening to `\d+[a-z]?|[a-z]` promotes letter-suffixed
# sub-clauses (doc acc20e08) while a BARE list marker "a" (label ["a"], no numeric
# anchor prefix) still cannot promote — the k-loop below requires a non-empty prefix.
_SUBCLAUSE_COMP_RE = re.compile(r"\d+[a-z]?|[a-z]")


def _is_numeric_extension(lab: tuple, anchors: set) -> bool:
    """True when ``lab`` is a kept anchor label plus a run of numeric / letter-suffix
    sub-components (RFC-015 D5d).

    There must be a non-empty kept-section label P (in ``anchors``) that is a PROPER
    prefix of ``lab``, and every component beyond P must be a pure digit run, a digit
    run with a single trailing lowercase letter, or a lone lowercase letter. The
    proper-prefix requirement (``k`` never reaches 0) means a bare list marker cannot
    promote itself."""
    return any(
        lab[:k] in anchors and all(_SUBCLAUSE_COMP_RE.fullmatch(c) for c in lab[k:])
        for k in range(len(lab) - 1, 0, -1)
    )


def _repromote_numbered_headings(doc) -> int:
    """Re-promote demoted body TextItems back to headings (no hardcoding).

    The docling-hierarchical-pdf add-on gives a clean heading SELECTION but
    over-prunes: it demotes deep numbered clauses (AKB "A.1.1", "A.1.1.1") to
    body TextItems alongside the font-size junk, capping the tree's depth. This
    walks the post-add-on doc and converts a TextItem back to a SectionHeaderItem
    IFF its numbering label is a proper NUMERIC EXTENSION of a kept-section label:
    there is a non-empty kept-section label P that is a proper prefix of the
    item's label and every component beyond P is a pure digit run ("A.1.1" =
    kept "A.1" + ["1"] -> promote; list marker "a" or mis-segmented prose
    "Fuehren"->[F,hren] -> NOT promoted). The anchors are the add-on's OWN kept
    section labels — nothing is hardcoded. Mutates the doc model in place (so body
    text is preserved for export) using the add-on's set_item_in_doc pattern, and
    returns the number of promotions."""
    from docling_core.types.doc.document import SectionHeaderItem, TextItem

    def label_of(item) -> tuple:
        return tuple(_segment_label(normalize_dashes((item.text or "").strip())))

    # Pass 1: trusted anchors = the add-on's kept section labels (non-empty).
    anchors: set[tuple] = set()
    for item, _ in doc.iterate_items(with_groups=False):
        if isinstance(item, SectionHeaderItem):
            lab = label_of(item)
            if lab:
                anchors.add(lab)

    # Pass 2: promote demoted TextItems whose label numerically extends an anchor.
    n_promo = 0
    for item, _ in list(doc.iterate_items(with_groups=False)):
        if isinstance(item, SectionHeaderItem) or not isinstance(item, TextItem):
            continue
        lab = label_of(item)
        if not lab:
            continue
        if _is_numeric_extension(lab, anchors):
            # TextItem -> SectionHeaderItem, then swap in at its self_ref index
            # (the add-on's set_item_in_doc pattern).
            header = SectionHeaderItem(
                **{
                    k: v
                    for k, v in item.model_dump().items()
                    if k != "label" and k in SectionHeaderItem.model_fields
                }
            )
            _, path, idx = item.self_ref.split("/")
            getattr(doc, path)[int(idx)] = header
            n_promo += 1
    return n_promo


def pdf_to_markdown(pdf_path: str) -> str:
    """Primary PDF route (INDEX-01-C1): pymupdf4llm -> relevel headings -> normalize dashes.
    Raises on empty/failed extraction so the caller can fall back to page_index (INDEX-01-C2)."""
    import pymupdf4llm

    # to_markdown() returns a str with default args; it only returns list[dict]
    # when page_chunks=True (which we do not pass). Cast to str for the type checker.
    md = cast(str, pymupdf4llm.to_markdown(pdf_path))
    if not md or not md.strip():
        raise RuntimeError(f"pdf_to_markdown produced empty output for {pdf_path}")
    return normalize_dashes(_relevel_headings(md))
