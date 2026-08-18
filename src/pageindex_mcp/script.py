"""Canonical Arabic / RTL script-detection primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# Canonical Unicode block ranges
# ---------------------------------------------------------------------------

ARABIC_RANGES: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A / Extended-B
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

LOGICAL_RANGES: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
)

PRESENTATION_RANGES: tuple[tuple[int, int], ...] = (
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

# ---------------------------------------------------------------------------
# Pre-compiled regexes derived from the canonical ranges
# ---------------------------------------------------------------------------

_CHAR_CLASS = "[" + "".join(
    f"\\u{lo:04X}-\\u{hi:04X}" for lo, hi in ARABIC_RANGES
) + "]"

AR_CHAR_RE: re.Pattern[str] = re.compile(_CHAR_CLASS)
AR_RUN_RE: re.Pattern[str] = re.compile(_CHAR_CLASS + "+")

# ---------------------------------------------------------------------------
# Dash normalisation (moved from converters.py — dependency-free leaf)
# ---------------------------------------------------------------------------

_DASH_TRANSLATION = {
    0x2010: "-",  # hyphen
    0x2011: "-",  # non-breaking hyphen — used in PHV clause codes (e.g. A1‑6.1)
    0x2013: "-",  # en-dash
    0x2014: "-",  # em-dash
    0x2212: "-",  # minus sign
}


def normalize_dashes(s: str) -> str:
    """Map Unicode hyphen/dash variants to ASCII '-' (CONV-01-C2).

    Includes the non-breaking hyphen (U+2011) the German PHV PDFs use inside
    clause codes like ``A1‑6.1``; normalising it lets numbering-depth recovery
    (``numbering_depth``) parse those codes."""
    return s.translate(_DASH_TRANSLATION)


# ---------------------------------------------------------------------------
# Readability scoring data
# ---------------------------------------------------------------------------

_AR_COMMON_WORDS: frozenset[str] = frozenset([
    "في",
    "من",
    "على",
    "إلى",
    "أن",
    "هذا",
    "هذه",
    "التي",
    "الذي",
    "عن",
    "مع",
    "بين",
    "كان",
    "ما",
    # Governance / legal domain terms
    "حوكمة",
    "بيانات",
    "سياسة",
    "إدارة",
    "تنظيم",
    "قرار",
    "وزارة",
    "لائحة",
    "تنفيذية",
    "مرسوم",
    "قانون",
    "نظام",
    "مادة",
    "حكومة",
    "هيئة",
])

_AR_DEFINITE_RE: re.Pattern[str] = re.compile(r"\bال\w+")

# ---------------------------------------------------------------------------
# Character-level helpers
# ---------------------------------------------------------------------------


def is_arabic_char(c: str) -> bool:
    """True if *c* falls in any ARABIC_RANGES block."""
    cp = ord(c)
    for lo, hi in ARABIC_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def arabic_char_count(text: str) -> int:
    """Count of characters in Arabic ranges."""
    return sum(1 for c in text if is_arabic_char(c))


def arabic_ratio(text: str) -> float:
    """Fraction of characters that are in Arabic ranges (0.0 for empty)."""
    if not text:
        return 0.0
    return arabic_char_count(text) / len(text)


def arabic_letter_ratio(text: str) -> float:
    """Arabic / (Arabic + Latin) character ratio."""
    ar = 0
    la = 0
    for c in text:
        if is_arabic_char(c):
            ar += 1
        elif c.isascii() and c.isalpha():
            la += 1
    total = ar + la
    return ar / total if total else 0.0


# ---------------------------------------------------------------------------
# Script inference
# ---------------------------------------------------------------------------


def infer_script(text: str) -> str | None:
    """Return 'Arab', 'Latn', or None based on majority script.

    Delegates to the canonical implementation in ``helpers._infer_script``
    which provides min-length (< 10 chars), min-signal (< 5 script chars),
    extended-Latin (U+00C0-U+024F), and strict-majority (> 50%) guards.

    Zone-7: unified to eliminate dual script-inference paths.
    """
    from .helpers import _infer_script  # late: avoids circular at module-load time
    return _infer_script(text)


# ---------------------------------------------------------------------------
# Readability scoring
# ---------------------------------------------------------------------------


def arabic_readability_score(words: list[str]) -> int:
    """Score Arabic text readability by common-word and definite-article hits."""
    score = 0
    for w in words:
        if w in _AR_COMMON_WORDS:
            score += 2
        if _AR_DEFINITE_RE.match(w):
            score += 1
    return score


# ---------------------------------------------------------------------------
# Unicode Joining_Type table (moved from helpers.py — dependency-free leaf)
# ---------------------------------------------------------------------------

# RFC-034 D7: vendored from ArabicShaping.txt (Unicode 17.0.0), scoped to
# the three base-Arabic blocks that presentation forms (FB50-FDFF, FE70-FEFF)
# decompose into under NFKC normalisation: Arabic, Arabic Supplement,
# Arabic Extended-A. R=Right_Joining, L=Left_Joining, D=Dual_Joining,
# C=Join_Causing, U=Non_Joining.
_JOINING_TYPE: dict[int, str] = {
    # Arabic (U+0600-U+06FF), 160 entries
    0x0600: "U", 0x0601: "U", 0x0602: "U", 0x0603: "U", 0x0604: "U", 0x0605: "U", 0x0608: "U", 0x060B: "U",
    0x0620: "D", 0x0621: "U", 0x0622: "R", 0x0623: "R", 0x0624: "R", 0x0625: "R", 0x0626: "D", 0x0627: "R",
    0x0628: "D", 0x0629: "R", 0x062A: "D", 0x062B: "D", 0x062C: "D", 0x062D: "D", 0x062E: "D", 0x062F: "R",
    0x0630: "R", 0x0631: "R", 0x0632: "R", 0x0633: "D", 0x0634: "D", 0x0635: "D", 0x0636: "D", 0x0637: "D",
    0x0638: "D", 0x0639: "D", 0x063A: "D", 0x063B: "D", 0x063C: "D", 0x063D: "D", 0x063E: "D", 0x063F: "D",
    0x0640: "C", 0x0641: "D", 0x0642: "D", 0x0643: "D", 0x0644: "D", 0x0645: "D", 0x0646: "D", 0x0647: "D",
    0x0648: "R", 0x0649: "D", 0x064A: "D", 0x066E: "D", 0x066F: "D", 0x0671: "R", 0x0672: "R", 0x0673: "R",
    0x0674: "U", 0x0675: "R", 0x0676: "R", 0x0677: "R", 0x0678: "D", 0x0679: "D", 0x067A: "D", 0x067B: "D",
    0x067C: "D", 0x067D: "D", 0x067E: "D", 0x067F: "D", 0x0680: "D", 0x0681: "D", 0x0682: "D", 0x0683: "D",
    0x0684: "D", 0x0685: "D", 0x0686: "D", 0x0687: "D", 0x0688: "R", 0x0689: "R", 0x068A: "R", 0x068B: "R",
    0x068C: "R", 0x068D: "R", 0x068E: "R", 0x068F: "R", 0x0690: "R", 0x0691: "R", 0x0692: "R", 0x0693: "R",
    0x0694: "R", 0x0695: "R", 0x0696: "R", 0x0697: "R", 0x0698: "R", 0x0699: "R", 0x069A: "D", 0x069B: "D",
    0x069C: "D", 0x069D: "D", 0x069E: "D", 0x069F: "D", 0x06A0: "D", 0x06A1: "D", 0x06A2: "D", 0x06A3: "D",
    0x06A4: "D", 0x06A5: "D", 0x06A6: "D", 0x06A7: "D", 0x06A8: "D", 0x06A9: "D", 0x06AA: "D", 0x06AB: "D",
    0x06AC: "D", 0x06AD: "D", 0x06AE: "D", 0x06AF: "D", 0x06B0: "D", 0x06B1: "D", 0x06B2: "D", 0x06B3: "D",
    0x06B4: "D", 0x06B5: "D", 0x06B6: "D", 0x06B7: "D", 0x06B8: "D", 0x06B9: "D", 0x06BA: "D", 0x06BB: "D",
    0x06BC: "D", 0x06BD: "D", 0x06BE: "D", 0x06BF: "D", 0x06C0: "R", 0x06C1: "D", 0x06C2: "D", 0x06C3: "R",
    0x06C4: "R", 0x06C5: "R", 0x06C6: "R", 0x06C7: "R", 0x06C8: "R", 0x06C9: "R", 0x06CA: "R", 0x06CB: "R",
    0x06CC: "D", 0x06CD: "R", 0x06CE: "D", 0x06CF: "R", 0x06D0: "D", 0x06D1: "D", 0x06D2: "R", 0x06D3: "R",
    0x06D5: "R", 0x06DD: "U", 0x06EE: "R", 0x06EF: "R", 0x06FA: "D", 0x06FB: "D", 0x06FC: "D", 0x06FF: "D",
    # Arabic Supplement (U+0750-U+077F), 48 entries
    0x0750: "D", 0x0751: "D", 0x0752: "D", 0x0753: "D", 0x0754: "D", 0x0755: "D", 0x0756: "D", 0x0757: "D",
    0x0758: "D", 0x0759: "R", 0x075A: "R", 0x075B: "R", 0x075C: "D", 0x075D: "D", 0x075E: "D", 0x075F: "D",
    0x0760: "D", 0x0761: "D", 0x0762: "D", 0x0763: "D", 0x0764: "D", 0x0765: "D", 0x0766: "D", 0x0767: "D",
    0x0768: "D", 0x0769: "D", 0x076A: "D", 0x076B: "R", 0x076C: "R", 0x076D: "D", 0x076E: "D", 0x076F: "D",
    0x0770: "D", 0x0771: "R", 0x0772: "D", 0x0773: "R", 0x0774: "R", 0x0775: "D", 0x0776: "D", 0x0777: "D",
    0x0778: "R", 0x0779: "R", 0x077A: "D", 0x077B: "D", 0x077C: "D", 0x077D: "D", 0x077E: "D", 0x077F: "D",
    # Arabic Extended-A (U+08A0-U+08FF), 42 entries
    0x08A0: "D", 0x08A1: "D", 0x08A2: "D", 0x08A3: "D", 0x08A4: "D", 0x08A5: "D", 0x08A6: "D", 0x08A7: "D",
    0x08A8: "D", 0x08A9: "D", 0x08AA: "R", 0x08AB: "R", 0x08AC: "R", 0x08AD: "U", 0x08AE: "R", 0x08AF: "D",
    0x08B0: "D", 0x08B1: "R", 0x08B2: "R", 0x08B3: "D", 0x08B4: "D", 0x08B5: "D", 0x08B6: "D", 0x08B7: "D",
    0x08B8: "D", 0x08B9: "R", 0x08BA: "D", 0x08BB: "D", 0x08BC: "D", 0x08BD: "D", 0x08BE: "D", 0x08BF: "D",
    0x08C0: "D", 0x08C1: "D", 0x08C2: "D", 0x08C3: "D", 0x08C4: "D", 0x08C5: "D", 0x08C6: "D", 0x08C7: "D",
    0x08C8: "D", 0x08E2: "U",
}


def _arabic_word_joins(word: str) -> int:
    """Count adjacent-pair cursive joins in *word* as stored, using
    Joining_Type: a join exists between word[i] and word[i+1] when word[i]
    can join forward (Dual/Left/Join_Causing) and word[i+1] can join
    backward (Dual/Right/Join_Causing). Reversing a correctly-ordered word's
    character order breaks most of its joins (joining is direction-specific),
    which is the RFC-034 D7 replacement for the presentation-form check."""
    joins = 0
    for i in range(len(word) - 1):
        if _JOINING_TYPE.get(ord(word[i]), "U") in ("D", "L", "C") and _JOINING_TYPE.get(
            ord(word[i + 1]), "U"
        ) in ("D", "R", "C"):
            joins += 1
    return joins


def _word_has_reversed_morphology(word: str) -> bool:
    """RFC-034 D7: vocabulary-independent reversal signal, using Unicode
    Joining_Type on base Arabic codepoints (via ``_JOINING_TYPE``) rather than
    ``unicodedata.name()`` presentation-form checks. Since upstream NFKC
    normalization decomposes presentation forms to base Arabic before this
    runs, the presentation-form check was a null detector (0% TPR).

    Cursive joining is direction-specific (``_arabic_word_joins``): a
    correctly-ordered Arabic word almost always has at least one adjacent
    pair that joins. Reversing the character order breaks nearly all of
    those joins (a join valid in one direction is not valid in the other),
    so a word with zero joins as stored that WOULD gain a join if reversed
    is a strong, vocabulary-independent reversal signal.

    Words shorter than 4 chars are excluded: with only one or two adjacent
    pairs to sample, common short function words (e.g. "دم", "رب" — a
    Right_Joining letter followed by a Dual_Joining one, which never joins
    forward and is a completely ordinary, non-reversed pattern) hit
    zero-joins-as-stored by chance and would false-positive."""
    if len(word) < 4:
        return False
    return _arabic_word_joins(word) == 0 and _arabic_word_joins(word[::-1]) > 0


# ---------------------------------------------------------------------------
# Order verdict (RTL reversal detection primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderVerdict:
    reversed: bool
    sampled: int
    reason: str = ""


def order_verdict(
    text: str | list[str],
    *,
    unit: str = "line",
    min_len: int = 0,
    arabic_ratio_min: float = 0.0,
    density: str = "chars",
    require_multiword: bool = False,
    sample_count: int | None = None,
    method: str = "readability_display",
    aggregate: bool = True,
    fail_threshold: float | None = None,
    require_orig_positive: bool = False,
    known_words: tuple[str, ...] = (),
    known_words_reversed: tuple[str, ...] = (),
    reason_on_fail: str = "",
) -> OrderVerdict:
    """Generic RTL order-detection primitive.

    Splits *text* into candidate units, qualifies them by Arabic-content
    heuristics, then scores each via *method* and returns a single verdict.
    """
    from bidi.algorithm import get_display

    if isinstance(text, list):
        candidates = text
    elif unit == "single":
        candidates = [text]
    else:
        candidates = text.splitlines()

    sampled = 0
    failed_count = 0
    orig_total = 0
    candidate_total = 0
    morphology_flag = False

    for raw in candidates:
        stripped = raw.strip() if isinstance(raw, str) else raw
        if not stripped:
            continue
        if len(stripped) < min_len:
            continue

        denom = len(stripped) if density == "chars" else len(stripped.replace(" ", ""))
        if denom == 0:
            continue
        ar_count = sum(1 for c in stripped if is_arabic_char(c))
        if arabic_ratio_min > 0.0 and ar_count / denom <= arabic_ratio_min:
            continue

        if require_multiword and len(stripped.split()) < 2:
            continue

        sampled += 1

        if method == "vocab_list":
            has_reversed = any(w in stripped for w in known_words_reversed)
            has_forward = any(w in stripped for w in known_words)
            unit_failed = has_reversed and not has_forward
        elif method == "readability_display":
            orig_words = stripped.split()
            disp_words = get_display(stripped).split()
            orig_score = arabic_readability_score(orig_words)
            disp_score = arabic_readability_score(disp_words)
            if aggregate:
                orig_total += orig_score
                candidate_total += disp_score
                if any(_word_has_reversed_morphology(w) for w in orig_words):
                    morphology_flag = True
                unit_failed = False
            else:
                unit_failed = disp_score > orig_score
        elif method == "readability_word_reverse":
            words = stripped.split()
            reversed_words = list(reversed(words))
            fwd_score = arabic_readability_score(words)
            rev_score = arabic_readability_score(reversed_words)
            unit_failed = rev_score > fwd_score
        elif method == "morphology_or_display":
            orig_words = stripped.split()
            disp_words = get_display(" ".join(orig_words)).split()
            orig_score = arabic_readability_score(orig_words)
            disp_score = arabic_readability_score(disp_words)
            morph = any(_word_has_reversed_morphology(w) for w in orig_words)
            if aggregate:
                orig_total += orig_score
                candidate_total += disp_score
                if morph:
                    morphology_flag = True
                unit_failed = False
            else:
                unit_failed = morph or disp_score > orig_score
        elif method == "readability_display_tie_morphology":
            orig_words = stripped.split()
            disp_words = get_display(stripped).split()
            orig_score = arabic_readability_score(orig_words)
            disp_score = arabic_readability_score(disp_words)
            if orig_score == 0 and disp_score == 0:
                unit_failed = any(_word_has_reversed_morphology(w) for w in orig_words)
            else:
                unit_failed = disp_score > orig_score
        else:
            unit_failed = False

        if unit_failed:
            failed_count += 1

        if sample_count is not None and sampled >= sample_count:
            break

    if sampled == 0:
        return OrderVerdict(reversed=False, sampled=0)

    if aggregate and method in ("readability_display", "morphology_or_display"):
        if require_orig_positive:
            is_reversed = (orig_total > 0 and candidate_total > orig_total) or morphology_flag
        else:
            is_reversed = candidate_total > orig_total or morphology_flag
    elif aggregate and method == "readability_display_tie_morphology":
        is_reversed = failed_count > 0
    elif not aggregate:
        if fail_threshold is None:
            is_reversed = failed_count > 0
        else:
            is_reversed = (failed_count / sampled) > fail_threshold
    else:
        is_reversed = failed_count > 0

    reason = reason_on_fail if is_reversed else ""
    return OrderVerdict(reversed=is_reversed, sampled=sampled, reason=reason)


# ---------------------------------------------------------------------------
# Zone-3 consolidated RTL deciders
# ---------------------------------------------------------------------------

GARBLE_DIGIT_FLOOR: int = 500
"""Minimum blob length for digit-ratio garble prong (Zone-3 constant)."""


class BlobKind(StrEnum):
    """Discriminates raw-markdown from tree-extracted text for garble
    normalization (Zone-3: normalize_for_garble)."""
    RAW_MARKDOWN = "RAW_MARKDOWN"
    TREE_TEXT = "TREE_TEXT"


_GARBLE_STRIP_RE = re.compile(r"#{1,6}\s|<!--.*?-->|\|", re.DOTALL)


def normalize_for_garble(blob: str, kind: BlobKind) -> str:
    """Normalize *blob* before garble ratio computation.

    ``RAW_MARKDOWN`` strips heading markers (``#``), table pipes (``|``),
    HTML comments (``<!-- ... -->``), and collapses whitespace so that
    markdown scaffolding does not inflate the denominator.

    ``TREE_TEXT`` returns the blob as-is (tree text is already stripped
    of markdown syntax).
    """
    if kind == BlobKind.TREE_TEXT:
        return blob
    cleaned = _GARBLE_STRIP_RE.sub(" ", blob)
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class RtlDecision:
    """Result of the consolidated RTL decision (Zone-3: decide_rtl)."""
    reversed: bool
    repair_effective: bool
    sampled: int
    method: str


def decide_rtl(text: str, *, sample_count: int = 8) -> RtlDecision:
    """Consolidated RTL decider -- ONE threshold, ONE sample count.

    Wraps ``order_verdict`` with a fixed Arabic-ratio floor of 0.15 and
    the ``morphology_or_display`` method (vocabulary + morphology signals
    OR-combined). Returns an ``RtlDecision`` whose ``repair_effective``
    field is True when ``apply_rtl`` would improve readability.

    This replaces the six separate RTL decision sites that previously
    existed across helpers.py and converters.py.
    """
    # Quick bail: not enough Arabic content.
    if not text:
        return RtlDecision(reversed=False, repair_effective=False,
                           sampled=0, method="morphology_or_display")
    ar_count = sum(1 for c in text if is_arabic_char(c))
    if ar_count / max(len(text), 1) <= 0.15:
        return RtlDecision(reversed=False, repair_effective=False,
                           sampled=0, method="morphology_or_display")

    verdict = order_verdict(
        text,
        unit="line",
        min_len=10,
        arabic_ratio_min=0.3,
        density="chars",
        method="morphology_or_display",
        aggregate=True,
        sample_count=sample_count,
    )

    if not verdict.reversed:
        return RtlDecision(reversed=False, repair_effective=False,
                           sampled=verdict.sampled,
                           method="morphology_or_display")

    # Probe whether apply_rtl would actually improve readability.
    from bidi.algorithm import get_display

    probe_lines = text.splitlines()
    orig_score = 0
    repaired_score = 0
    for line in probe_lines[:sample_count]:
        stripped = line.strip()
        if not stripped or len(stripped) < 10:
            continue
        ar = sum(1 for c in stripped if is_arabic_char(c))
        if ar / len(stripped) <= 0.3:
            continue
        orig_words = stripped.split()
        orig_score += arabic_readability_score(orig_words)
        disp_words = get_display(stripped).split()
        repaired_score += arabic_readability_score(disp_words)

    repair_effective = repaired_score > orig_score

    return RtlDecision(
        reversed=True,
        repair_effective=repair_effective,
        sampled=verdict.sampled,
        method="morphology_or_display",
    )


def apply_rtl(text: str, *, reversed_flag: bool) -> str:
    """Single-pass best-candidate RTL repair (Zone-3).

    Evaluates three candidates for each line (as-is, get_display,
    word-reversed) and picks the one with the highest Arabic
    readability score. Applied uniformly to headings and body.

    When *reversed_flag* is False the text is returned unchanged.
    """
    if not reversed_flag or not text:
        return text

    from bidi.algorithm import get_display

    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        ar_count = sum(1 for c in stripped if is_arabic_char(c))
        if ar_count / max(len(stripped), 1) <= 0.15:
            out.append(line)
            continue

        # Detect and preserve leading markdown heading prefix.
        heading_prefix = ""
        body = stripped
        hdr_match = re.match(r"^(\s*#{1,6}[ \t]+)(.*)", stripped, re.DOTALL)
        if hdr_match:
            heading_prefix = hdr_match.group(1)
            body = hdr_match.group(2)

        # Three candidates
        candidate_asis = body
        candidate_display = get_display(body)
        candidate_word_rev = " ".join(reversed(body.split()))

        best = candidate_asis
        best_score = arabic_readability_score(candidate_asis.split())

        disp_score = arabic_readability_score(candidate_display.split())
        if disp_score > best_score:
            best = candidate_display
            best_score = disp_score

        rev_score = arabic_readability_score(candidate_word_rev.split())
        if rev_score > best_score:
            best = candidate_word_rev

        # Reconstruct line preserving original indent/trailing whitespace.
        indent = line[: len(line) - len(line.lstrip())]
        trail = line[len(line.rstrip()):]
        out.append(indent + heading_prefix + best + trail)
    return "".join(out)
