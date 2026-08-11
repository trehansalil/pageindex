"""Canonical Arabic / RTL script-detection primitives."""

from __future__ import annotations

import re

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
    """Return 'Arab', 'Latn', or None based on majority script."""
    ar = 0
    la = 0
    for c in text:
        if is_arabic_char(c):
            ar += 1
        elif c.isascii() and c.isalpha():
            la += 1
    if ar == 0 and la == 0:
        return None
    return "Arab" if ar >= la else "Latn"


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
