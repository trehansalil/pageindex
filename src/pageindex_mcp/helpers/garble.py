"""Garble detection: prongs, config, and per-node checking."""

from __future__ import annotations

import logging
import re
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import pipeline_config
from ..obs import decision
from ..script import (
    ARABIC_RANGES,
    PF_SIGNAL_RATIO,
    PRESENTATION_RANGES,
    BlobKind,
    ScriptContext,
    _infer_script,
    _word_has_reversed_morphology,
    normalize_for_garble,
)
from .types import TreeDefect

if TYPE_CHECKING:
    from ..config import PipelineConfig

logger = logging.getLogger(__name__)

# RFC-046 D12 / task 12.5: per-document cap on the two DEBUG, per-blob
# decision events below (garble_prong_evaluation, garble_verdict). A
# ContextVar mirrors obs.context's phase-seq counter: each asyncio task
# (one per document in the arq worker) gets its own copy at task creation,
# so concurrent documents never share -- or under/over-count -- each
# other's caps.
_DECISION_EMIT_COUNTS: ContextVar[dict] = ContextVar("garble_decision_emit_counts")


def _under_emit_cap(event: str, cap: int) -> bool:
    """True while *event* has fired fewer than *cap* times for this document.

    Never raises -- a bug here must degrade to "skip this debug record",
    never break the document (R12.8 posture).
    """
    try:
        counts = _DECISION_EMIT_COUNTS.get(None) or {}
        n = counts.get(event, 0)
        if n >= cap:
            return False
        updated = dict(counts)
        updated[event] = n + 1
        _DECISION_EMIT_COUNTS.set(updated)
        return True
    except Exception:
        return False


_GARBLE_PRONG_EVAL_CAP = 50
_GARBLE_VERDICT_CAP = 50


def _has_any_presentation_form(text: str) -> bool:
    """True when *text* contains at least one Arabic Presentation Form codepoint."""
    return any(0xFB50 <= ord(ch) <= 0xFDFF or 0xFE70 <= ord(ch) <= 0xFEFF for ch in text)


def _pf_ratio(text: str) -> float:
    """Ratio of Arabic Presentation Form codepoints to all Arabic codepoints."""
    if not text:
        return 0.0
    pf_count = sum(1 for c in text if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES))
    ar_count = sum(1 for c in text if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES))
    if ar_count == 0:
        return 0.0
    return pf_count / ar_count


def _infer_presentation_forms(text: str) -> bool:
    """Best-effort Arabic Presentation-Forms detection from *text*.

    Returns True when Arabic Presentation Forms (U+FB50-FDFF, U+FE70-FEFF)
    constitute > 50% of all Arabic-range characters.  Post-NFKC this ratio
    is always 0 (the codepoints decompose into logical Arabic), so callers
    on post-normalization text correctly get False -- the
    ``ScriptContext.from_document`` path scans pre-normalization text and
    gets the real answer; this helper is the fallback for call sites that
    construct ScriptContext without access to pre-NFKC text.

    Zone-7 fix: extracted to close the ``had_presentation_forms=False``
    hardcoding pattern across 10+ fallback ScriptContext constructions.
    """
    return _pf_ratio(text) > PF_SIGNAL_RATIO


_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_COMMON_WORDS: frozenset[str] = frozenset(
    {
        # English stopwords + common short words
        "the",
        "be",
        "to",
        "of",
        "and",
        "in",
        "that",
        "have",
        "it",
        "for",
        "not",
        "on",
        "with",
        "he",
        "as",
        "you",
        "do",
        "at",
        "this",
        "but",
        "his",
        "by",
        "from",
        "they",
        "we",
        "say",
        "her",
        "she",
        "or",
        "an",
        "will",
        "my",
        "one",
        "all",
        "would",
        "there",
        "their",
        "what",
        "so",
        "up",
        "out",
        "if",
        "about",
        "who",
        "get",
        "which",
        "go",
        "me",
        "when",
        "make",
        "can",
        "like",
        "time",
        "no",
        "just",
        "him",
        "know",
        "take",
        "people",
        "into",
        "year",
        "your",
        "good",
        "some",
        "could",
        "them",
        "see",
        "other",
        "than",
        "then",
        "now",
        "look",
        "only",
        "come",
        "its",
        "over",
        "think",
        "also",
        "back",
        "after",
        "use",
        "two",
        "how",
        "our",
        "work",
        "first",
        "well",
        "way",
        "even",
        "new",
        "want",
        "because",
        "any",
        "these",
        "give",
        "day",
        "most",
        "us",
        "is",
        "are",
        "was",
        "were",
        "been",
        "has",
        "had",
        "did",
        "does",
        "may",
        "must",
        "shall",
        "should",
        "might",
        "need",
        "very",
        "more",
        "much",
        "own",
        "such",
        "here",
        "where",
        "why",
        "each",
        "few",
        "both",
        "between",
        "under",
        "same",
        "still",
        "before",
        "through",
        "during",
        "without",
        "within",
        "per",
        "de",
        "re",
        # German stopwords
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
        "eines",
        "und",
        "ist",
        "sind",
        "war",
        "hat",
        "mit",
        "auf",
        "für",
        "von",
        "aus",
        "bei",
        "nach",
        "zum",
        "zur",
        "sich",
        "nicht",
        "auch",
        "als",
        "nur",
        "noch",
        "oder",
        "aber",
        "wenn",
        "wird",
        "über",
        "ich",
        "wir",
        "sie",
        "man",
        "kann",
        "diese",
        "dieser",
        "diesem",
        "diesen",
        "dieses",
        "werden",
        "durch",
        "unter",
        "zwischen",
        "gegen",
        "ohne",
        "bis",
        "sein",
        "seine",
        "seinem",
        "seinen",
        "seiner",
        "ihre",
        "ihrem",
        "ihren",
        "ihrer",
        "mehr",
        "vor",
        "haben",
        "dass",
        "schon",
        "immer",
        "wieder",
        # Common technical/insurance terms that appear in bilingual docs
        "gmbh",
        "ag",
        "nr",
        "abs",
        "bzw",
        "etc",
        "max",
        "min",
        "pdf",
        "doc",
        "page",
        "file",
        "text",
        "data",
        "type",
        "article",
        "section",
        "paragraph",
        "clause",
        "item",
    }
)


def _latin_token_ratio(text: str) -> tuple[float, list[str]]:
    """Return (ratio_of_latin_tokens, latin_token_list) for garble scoring."""
    tokens = text.split()
    if not tokens:
        return 0.0, []
    latin_tokens = _LATIN_TOKEN_RE.findall(text)
    return len(latin_tokens) / len(tokens), latin_tokens


_VOWELS = frozenset("aeiouAEIOU")


def _is_morphologically_nonsense(token: str) -> bool:
    """Return True if a Latin token looks like garble rather than a real word.

    QF3 (RFC-021): hybrid morphological + whitelist approach.  The old
    pure-whitelist approach (~160 stopwords) mis-classified legitimate
    bilingual domain English as nonsense.  The fix:

    * **Hard failures** (always nonsense regardless of length):
      - digit-letter mixing ("xKjQ7", "mZpR3")
      - no vowels at all ("xkjqz", "vbwm")
    * **Long tokens (>=5 chars)** that survive the hard checks are treated
      as morphologically plausible domain words (e.g. "service",
      "infrastructure", "compliance") -- NOT nonsense.
    * **Short tokens (3-4 chars)** that survive the hard checks fall back
      to the ``_COMMON_WORDS`` whitelist.  This catches Tesseract
      syllable garble ("Bab", "rel", "teb") which has vowels but isn't
      a real word, while still passing common short words ("the", "for").
    * Tokens <=2 chars and short all-caps acronyms (<=5 chars) are exempt.
    """
    if len(token) <= 2:
        return False
    if token.isupper() and len(token) <= 5:
        return False
    has_alpha = False
    has_digit = False
    for c in token:
        if c.isalpha():
            has_alpha = True
        elif c.isdigit():
            has_digit = True
        if has_alpha and has_digit:
            return True
    if not any(c in _VOWELS for c in token):
        return True
    if len(token) >= 5:
        return False
    return token.lower() not in _COMMON_WORDS


def _emit_prong_decision(
    prongs: frozenset[str],
    *,
    norm_blob_len: int,
    expected_script: str | None,
    had_presentation_forms: bool,
    cfg: GarbleConfig,
    reason: str,
) -> None:
    """DEBUG, capped emit for the per-blob ``garble_prong_evaluation`` point.

    Extracted so the two call sites inside :func:`_garble_prongs` (the
    empty-blob early return and the normal exit) stay one statement each --
    keeping the already-large parent function under the repo's
    max-statements gate (R12.9's isEnabledFor-before-cost-check still lives
    here, just out of the caller's line count).
    """
    if not (
        logger.isEnabledFor(logging.DEBUG)
        and _under_emit_cap("garble_prong_evaluation", _GARBLE_PRONG_EVAL_CAP)
    ):
        return
    decision(
        event="garble_prong_evaluation",
        choice="fired" if prongs else "clean",
        reason=reason,
        attrs={
            "norm_blob_len": norm_blob_len,
            "expected_script": expected_script,
            "had_presentation_forms": had_presentation_forms,
            "fired_prong_count": len(prongs),
            "fired_prongs": sorted(prongs),
            "garble_latin_ratio": cfg.garble_latin_ratio,
            "garble_nonsense_ratio": cfg.garble_nonsense_ratio,
            "garble_digit_floor": cfg.garble_digit_floor,
        },
        logger=logger,
    )


def _garble_prongs(  # noqa: PLR0915
    norm_blob: str,
    *,
    expected_script: str | None = None,
    original_text: str | None = None,
    had_presentation_forms: bool = False,
    config: GarbleConfig | None = None,
) -> frozenset[str]:
    """Return the set of garble-detection prongs that fired on *norm_blob*.

    Each prong name corresponds to a specific heuristic check. An empty
    frozenset means no garbling detected.

    Zone-1 purification: ``norm_blob`` is expected to be PRE-NORMALIZED
    (callers run ``normalize_for_garble`` before invoking this function).
    ``expected_script`` is keyword-only; callers that need inference must
    call ``_infer_script`` explicitly before passing the value here.

    ``original_text``: the UN-normalized blob, used for the sparse_mojibake
    prong (RFC-015 D8 calibration requires raw text, not norm_blob).

    ``had_presentation_forms``: pre-computed boolean indicating that
    Arabic Presentation-Forms ratio > 50% of Arabic-range chars was
    detected (typically from RtlDecision or computed by detect_garble
    before NFKC normalization destroys the codepoints).

    ``config``: Zone-3 consolidated garble config.  When ``None``
    (backward compat), falls back to the module-level ``_garble_config``.
    """
    cfg = config if config is not None else _garble_config
    prongs: set[str] = set()

    if not norm_blob.strip():
        _emit_prong_decision(
            frozenset({"empty"}),
            norm_blob_len=0,
            expected_script=expected_script,
            had_presentation_forms=had_presentation_forms,
            cfg=cfg,
            reason="empty_blob",
        )
        return frozenset({"empty"})

    norm = norm_blob

    if "\x00" in norm or "�" in norm:
        prongs.add("null_replacement_bytes")
    if "GLYPH<" in norm:
        prongs.add("glyph_marker")

    bad = sum(1 for c in norm if ord(c) < 32 and c not in "\n\r\t")
    if (bad / max(len(norm), 1)) > 0.05:
        prongs.add("control_chars")

    pua = sum(1 for c in norm if 0xE000 <= ord(c) <= 0xF8FF)
    if (pua / max(len(norm), 1)) > 0.03:
        prongs.add("pua_chars")

    if had_presentation_forms:
        prongs.add("presentation_forms")

    arabic_tokens = [t for t in norm.split() if any(_is_arabic_char(c) for c in t)]
    if arabic_tokens:
        single_char_fragments = sum(1 for t in arabic_tokens if len(t) == 1 and t != "و")
        if (single_char_fragments / len(arabic_tokens)) > 0.40:
            prongs.add("single_letter_fragments")

    if len(norm) > cfg.garble_digit_floor:
        digits = sum(1 for c in norm if c.isdigit())
        if (digits / len(norm)) > 0.60:
            prongs.add("digit_ratio")
    elif len(norm) >= 50:
        # Zone-garble fix: secondary short-text numeric-junk check.
        # Stricter 90% threshold (vs 60% for long text) prevents
        # false-positives on legitimate short numeric content (dates,
        # currency amounts, version strings).
        _short_digits = sum(1 for c in norm if c.isdigit())
        if (_short_digits / len(norm)) > 0.90:
            prongs.add("numeric_junk_short")

    stripped = re.sub(r"<!--.*?-->", "", norm)
    tokens = [t for t in stripped.split() if any(c.isalnum() for c in t)]
    if len(tokens) > 20:
        most_common_count = Counter(tokens).most_common(1)[0][1]
        if (most_common_count / len(tokens)) > 0.30:
            prongs.add("token_repetition")

    _effective_script = expected_script
    if cfg.garble_latin_gibberish_enabled:
        latin_ratio_threshold = cfg.garble_latin_ratio
        nonsense_threshold = cfg.garble_nonsense_ratio
        ratio, latin_tokens = _latin_token_ratio(norm)
        if ratio > latin_ratio_threshold and len(latin_tokens) >= 5:
            # Zone-garble fix: when expected script is Arabic but text is
            # predominantly Latin, use a lowered nonsense threshold (0.40)
            # to catch Latin tessdata mojibake (Chain 5 script-mismatch).
            _active_nonsense = nonsense_threshold
            if _effective_script == "Arab" and ratio > latin_ratio_threshold:
                _active_nonsense = min(nonsense_threshold, 0.40)
            nonsense = sum(1 for t in latin_tokens if _is_morphologically_nonsense(t))
            if nonsense / len(latin_tokens) > _active_nonsense:
                prongs.add("latin_gibberish")

    # RFC-045: script-mismatch prong.  When expected script is Arabic but
    # the text is overwhelmingly Latin (>80%), the extraction is garbled
    # regardless of whether individual tokens are morphologically nonsense.
    # This catches pure-image Arabic PDFs where Docling produces Latin
    # gibberish that individually looks like plausible short words but
    # collectively is wrong-script.  Strip HTML comments first — figure
    # markers (<!-- image -->) contain the Latin word "image" which dilutes
    # the ratio on image-heavy documents.
    if _effective_script == "Arab" and cfg.garble_latin_gibberish_enabled:
        _sm_stripped = re.sub(r"<!--.*?-->", "", norm)
        _sm_ratio, _sm_tokens = _latin_token_ratio(_sm_stripped)
        if _sm_ratio > 0.80 and len(_sm_tokens) >= 10:
            prongs.add("script_mismatch")

    _sparse_text = original_text if original_text is not None else norm
    if len(_sparse_text) >= 100:
        _sparse_matches = _MIXED_SCRIPT_RE.findall(_sparse_text)
        if (len(_sparse_matches) / max(len(_sparse_text.split()), 1)) > 0.02:
            prongs.add("sparse_mojibake")

    _emit_prong_decision(
        frozenset(prongs),
        norm_blob_len=len(norm_blob),
        expected_script=expected_script,
        had_presentation_forms=had_presentation_forms,
        cfg=cfg,
        reason="prongs_evaluated",
    )

    return frozenset(prongs)


@dataclass(frozen=True)
class GarbleProfile:
    """Zone-1 consolidation: replaces the 8-member GarbleContext StrEnum and
    its 3 dispatch layers with a frozen dataclass carrying the two semantic
    boolean fields that actually differ across call sites.

    * ``normalize_markdown``: when True (and the GARBLE_FLAT_MARKDOWN_NORMALIZE
      env var is enabled), uses RAW_MARKDOWN normalization instead of TREE_TEXT.
    * ``short_circuit_prior_garble``: when True (and the GARBLE_SHORT_TEXT_DEFAULT
      env var is enabled), short-circuits to True for short text (< 200 chars)
      with a pre-existing garbling defect (RFC-025 D2).
    """

    normalize_markdown: bool = False
    short_circuit_prior_garble: bool = False


BULK_PROFILE = GarbleProfile()
FLAT_MARKDOWN_PROFILE = GarbleProfile(normalize_markdown=True, short_circuit_prior_garble=True)

_GARBLE_SHORT_TEXT_DEFAULT = pipeline_config.garble_short_text_default
_GARBLE_FLAT_MARKDOWN_NORMALIZE = pipeline_config.garble_flat_markdown_normalize


@dataclass(frozen=True)
class GarbleConfig:
    """Zone-3: consolidated garble detection configuration.

    Sourced from :data:`pipeline_config` (not ``os.environ``).  Replaces
    7 scattered ``os.environ.get`` calls with a single frozen snapshot.
    Defaults match the prior scattered defaults exactly.
    """

    garble_latin_gibberish_enabled: bool = True
    garble_latin_ratio: float = 0.4
    garble_nonsense_ratio: float = 0.7
    garble_short_text_default: bool = True
    garble_flat_markdown_normalize: bool = True
    garble_node_ratio_threshold: float = 0.10
    garble_digit_floor: int = 500

    @classmethod
    def from_config(cls, cfg: PipelineConfig) -> GarbleConfig:
        """Build GarbleConfig from a frozen PipelineConfig."""
        return cls(
            garble_latin_gibberish_enabled=cfg.garble_latin_gibberish_enabled,
            garble_latin_ratio=cfg.garble_latin_ratio,
            garble_nonsense_ratio=cfg.garble_nonsense_ratio,
            garble_short_text_default=cfg.garble_short_text_default,
            garble_flat_markdown_normalize=cfg.garble_flat_markdown_normalize,
            garble_node_ratio_threshold=cfg.garble_node_ratio_threshold,
            garble_digit_floor=cfg.garble_digit_floor,
        )


_garble_config: GarbleConfig = GarbleConfig.from_config(pipeline_config)


@dataclass(frozen=True)
class GarbleReport:
    """Zone-3: structured result from :func:`detect_garble`.

    Carries the boolean verdict alongside the prongs that fired and the
    garble ratio, so callers can inspect *why* garbling was detected
    without re-running the heuristics.  ``__bool__`` returns
    ``is_garbled`` so the report is drop-in compatible with the prior
    bare-``bool`` return value of ``detect_garble``.

    ``fired_prongs``
        The set of garble-detection prongs that triggered (empty when
        ``is_garbled`` is ``False``).  E.g. ``{"pua_chars", "digit_ratio"}``.

    ``garble_ratio``
        Windowed garble ratio (fraction of 2000-char windows that
        individually trigger garble detection).  ``0.0`` when not garbled.
    """

    is_garbled: bool
    fired_prongs: frozenset[str] = frozenset()
    garble_ratio: float = 0.0

    def __bool__(self) -> bool:
        """Drop-in backward-compat: ``if detect_garble(...)`` works."""
        return self.is_garbled


def detect_garble(
    text: str,
    *,
    script_context: ScriptContext,
    config: GarbleConfig,
    blob_kind: BlobKind = BlobKind.TREE_TEXT,
    original_defect: TreeDefect | None = None,
) -> GarbleReport:
    """Unified garble evaluation entry point (Zone-3).

    Single-surface API: all garble heuristics (bulk prongs + sparse mojibake
    + presentation-forms) run inside ``_garble_prongs``.

    Returns a :class:`GarbleReport` carrying the boolean verdict, the set
    of prongs that fired, and the garble ratio.  The report's ``__bool__``
    method returns ``is_garbled`` so existing ``if detect_garble(...)``
    call sites keep working without changes.

    ``script_context`` provides the document-level script and
    presentation-forms flag (computed once per index entry, pre-NFKC).
    ``config`` provides the garble detection thresholds (sourced from
    ``pipeline_config``, not ``os.environ``).
    ``blob_kind`` selects normalization strategy (replaces the
    ``GarbleProfile.normalize_markdown`` boolean).
    ``original_defect`` enables the short-circuit for flat-markdown
    short-text garble-by-default (RFC-025 D2).
    """
    blob = text or ""

    # RFC-025 D2 short-text with prior garble defect.
    # Zone-7 fix: the old unconditional short-circuit forced is_garbled=True
    # for ALL text < 200 chars with a prior garble defect, regardless of
    # content -- marking clean short text ("Kurzer Text") as garbled.
    # Now: run the actual garble prongs first.  If any prong fires,
    # include the short_text_prior_garble tag alongside the real prongs.
    # If no prong fires, the text IS clean and is not forced garbled.
    _short_text_prior = (
        blob_kind == BlobKind.RAW_MARKDOWN
        and config.garble_short_text_default
        and len(blob) < 200
        and original_defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)
    )

    _effective_script = script_context.dominant_script
    if _effective_script is None:
        _effective_script = _infer_script(blob)

    _had_pf = script_context.had_presentation_forms
    if not _had_pf:  # noqa: SIM102
        if _pf_ratio(blob) > PF_SIGNAL_RATIO:
            _had_pf = True
        # Removed: the prior fallback here unconditionally set _had_pf=True
        # for ANY Arabic text with zero presentation forms, assuming NFKC had
        # decomposed them.  This was a false-positive factory — most Arabic
        # documents never use presentation forms.  ScriptContext.from_document
        # scans pre-NFKC text and sets had_presentation_forms correctly;
        # callers without pre-NFKC access correctly default to False.

    _use_raw_md = blob_kind == BlobKind.RAW_MARKDOWN and config.garble_flat_markdown_normalize
    _norm_kind = BlobKind.RAW_MARKDOWN if _use_raw_md else BlobKind.TREE_TEXT
    norm = normalize_for_garble(blob, _norm_kind)
    if not norm.strip():
        norm = blob

    prongs = _garble_prongs(
        norm,
        expected_script=_effective_script,
        original_text=blob,
        had_presentation_forms=_had_pf,
        config=config,
    )
    # Zone-7: when short_text_prior applies and prongs fired, tag the
    # report with short_text_prior_garble for diagnostic visibility.
    # When prongs did NOT fire, the text is clean -- do not force garbled.
    if _short_text_prior and prongs:
        prongs = prongs | frozenset({"short_text_prior_garble"})

    if logger.isEnabledFor(logging.DEBUG) and _under_emit_cap(
        "garble_verdict", _GARBLE_VERDICT_CAP
    ):
        decision(
            event="garble_verdict",
            choice="garbled" if prongs else "clean",
            reason=("short_text_prior_forced" if (_short_text_prior and prongs) else "prong_scan"),
            attrs={
                "fired_prongs": sorted(prongs),
                "blob_kind": blob_kind.value,
                "blob_len": len(blob),
                "dominant_script": _effective_script,
                "had_presentation_forms": _had_pf,
                "short_text_prior_applicable": _short_text_prior,
            },
            logger=logger,
        )

    return GarbleReport(
        is_garbled=bool(prongs),
        fired_prongs=prongs,
        garble_ratio=1.0 if prongs else 0.0,
    )


# Zone-4: _rebuild_garble_config_compat and check_garble deleted — detect_garble
# is now the sole public entry point.  GarbleReport.__bool__ is the drop-in
# replacement for check_garble's bool return value.


_MIXED_SCRIPT_RE = re.compile(
    # Alt 1: Arabic + (ASCII run containing ≥1 letter) + Arabic
    r"[؀-ۿ](?=[\x21-\x7E]{0,7}[A-Za-z])[\x21-\x7E]{1,8}[؀-ۿ]"
    # Alt 2: letter-led ASCII run + Arabic + any ASCII run
    r"|[A-Za-z][\x21-\x7E]{0,7}[؀-ۿ][\x21-\x7E]{1,8}"
    # Alt 3: any ASCII run + Arabic + ASCII run containing letter
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{0,7}[A-Za-z]"
)


_GARBLE_NODE_RATIO_THRESHOLD_RAW = pipeline_config.garble_node_ratio_threshold
_GARBLE_NODE_RATIO_THRESHOLD = pipeline_config.garble_node_ratio_threshold
# RFC-047 D2 (post-gate-FAIL): minimum fraction of garbled CHARACTER MASS
# (not block count) for the per-block gate to condemn.  Character-mass
# ratio survives large N: a single garbled block among 297 clean ones is
# 1/297=0.003 by block count but may be 60% by character mass.  Sits
# beside: garble_node_ratio_threshold (per-NODE, feeds _gate_node_garbling)
# and garble_window_ratio_threshold (whole-blob, feeds _gate_garbling).
# Plain module constant — promote to GarbleConfig only when a second
# consumer appears (YAGNI).
_GARBLE_CHAR_MASS_THRESHOLD = 0.10
_EMPTY_NODE_FRACTION_THRESHOLD = pipeline_config.empty_node_fraction_threshold
_RFC029_FLAT_PREFER_MULTIPLIER = pipeline_config.rfc029_flat_prefer_multiplier
_RFC029_MIN_CHARS_PER_NODE = pipeline_config.rfc029_min_chars_per_node
_RFC029_MIN_CHARS_PER_NODE_DEEP = pipeline_config.rfc029_min_chars_per_node_deep
_RFC029_DEEP_TREE_DEPTH_THRESHOLD = 4
_RFC029_MIN_SCANNED_DENSITY_FLOOR = pipeline_config.rfc029_min_scanned_density_floor
_RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC = pipeline_config.rfc029_min_scanned_density_floor_arabic


def _collect_all_node_text(nodes: list[dict]) -> str:
    """Recursively collect all node text into a single concatenated string.

    Zone-5 fix: also extracts table block content from 'headers', 'rows',
    and 'row_records' via block_text, so per-node garble checking sees
    table-heavy nodes.

    D2 (RFC-041): uses block_text(node, GARBLE_CHECK) instead of
    _node_text_parts for consistency with the unified accessor.
    """
    from .flat import BlockTextPurpose, block_text

    parts: list[str] = []
    for node in nodes:
        title = str(node.get("title", ""))
        if title.strip():
            parts.append(title)
        body = block_text(node, BlockTextPurpose.GARBLE_CHECK)
        if body and body.strip() and body != title:
            parts.append(body)
        children = node.get("nodes") or []
        if children:
            child_text = _collect_all_node_text(children)
            if child_text:
                parts.append(child_text)
    return "\n".join(parts)


def _garble_check_nodes(
    nodes: list[dict],
    *,
    script_context: ScriptContext,
    config: GarbleConfig,
    _is_toplevel: bool = True,
) -> int:
    """Recursively count nodes whose text or title is individually garbled.

    Zone-3: ``script_context`` and ``config`` are required.  The
    document-level script comes from ``script_context.dominant_script``;
    per-node override (QF3/RFC-021) is still computed for nodes >= 50
    chars whose text-inferred script disagrees with the document-level
    script.

    When ``_is_toplevel`` is True (default, top-level call) and per-node
    detection returned 0 garbled nodes, a concatenated whole-tree fallback
    runs detect_garble on the joined text of all nodes.  This catches
    garble patterns that fall below garble_digit_floor per node but surface
    in aggregate.
    """
    from .flat import BlockTextPurpose, block_text  # deferred: avoid circular import

    _doc_script = script_context.dominant_script

    garbled = 0
    for node in nodes:
        node_garbled = False
        # D2 (RFC-041): use block_text(node, GARBLE_CHECK) instead of
        # _node_text_parts to see table content consistently.
        # Exclude title — it has a dedicated morphology + garble check below.
        text = block_text(node, BlockTextPurpose.GARBLE_CHECK)
        _node_title = str(node.get("title", ""))
        if text == _node_title:
            text = ""
        if text.strip():
            if _doc_script is not None:
                inferred = _infer_script(text) if len(text) >= 50 else None
                if inferred is not None and inferred != _doc_script:
                    logger.warning(
                        "Script mismatch: filename-derived=%s, text-inferred=%s "
                        "(using text-inferred for this node)",
                        _doc_script,
                        inferred,
                    )
                    node_script = inferred
                else:
                    node_script = _doc_script
            else:
                node_script = _infer_script(text) if len(text) >= 50 else None
            _node_ctx = ScriptContext(
                dominant_script=node_script,
                had_presentation_forms=script_context.had_presentation_forms,
                source="per_node",
            )
            if detect_garble(text, script_context=_node_ctx, config=config):
                node_garbled = True
        title = node.get("title") or ""
        if title.strip() and (
            any(_word_has_reversed_morphology(w) for w in title.split())
            or detect_garble(
                title,
                script_context=ScriptContext(
                    dominant_script=_doc_script,
                    had_presentation_forms=script_context.had_presentation_forms,
                    source="per_node_title",
                ),
                config=config,
            )
        ):
            node_garbled = True
        if node_garbled:
            garbled += 1
        children = node.get("nodes") or []
        garbled += _garble_check_nodes(
            children,
            script_context=script_context,
            config=config,
            _is_toplevel=False,
        )
    # Concatenated whole-tree fallback: when per-node detection found nothing
    # garbled, run detect_garble on the joined text to catch patterns that
    # fall below garble_digit_floor per node but surface in aggregate.
    # D1: uses detect_garble (not _garble_prongs) so short-text rule and
    # PF recovery logic apply consistently.
    if _is_toplevel:
        _per_node_garbled_count = garbled
        if garbled == 0:
            _concat = _collect_all_node_text(nodes)
            _fallback_ctx = ScriptContext(
                dominant_script=_doc_script,
                had_presentation_forms=script_context.had_presentation_forms,
                source="whole_tree_fallback",
            )
            _fallback_report = detect_garble(
                _concat,
                script_context=_fallback_ctx,
                config=config,
            )
            if _fallback_report:
                # RFC-046 D12: migrates the prior `logger.info` here rather
                # than duplicating it (registry note) -- same information,
                # now carrying the computed per-node count alongside the
                # fallback's forced outcome (R12.5).
                decision(
                    event="garble_whole_tree_fallback",
                    choice="fallback_fired",
                    reason="per_node_clean_whole_tree_garbled",
                    attrs={
                        "per_node_garbled_count": _per_node_garbled_count,
                        "concat_text_len": len(_concat),
                        "fired_prongs": sorted(_fallback_report.fired_prongs),
                    },
                    logger=logger,
                )
                garbled = 1
            else:
                decision(
                    event="garble_whole_tree_fallback",
                    choice="fallback_clean",
                    reason="per_node_clean_whole_tree_clean",
                    attrs={
                        "per_node_garbled_count": _per_node_garbled_count,
                        "concat_text_len": len(_concat),
                        "fired_prongs": [],
                    },
                    logger=logger,
                )
        else:
            decision(
                event="garble_whole_tree_fallback",
                choice="fallback_not_reached",
                reason="per_node_garble_already_found",
                attrs={
                    "per_node_garbled_count": _per_node_garbled_count,
                    "concat_text_len": 0,
                    "fired_prongs": [],
                },
                logger=logger,
            )
    return garbled


def _garble_check_flat_blocks(
    blocks: list[dict],
    *,
    script_context: ScriptContext,
    config: GarbleConfig,
) -> GarbleReport:
    """Zone-1: per-block garble check for flat-routed documents.

    Runs detect_garble on each block individually, eliminating the dilution
    problem where a single garbled table amid clean prose would pass the
    whole-blob check.

    RFC-047 D2 (post-gate-FAIL): threshold uses CHARACTER MASS ratio
    (garbled_chars / total_chars), not block-count ratio.  Block-count
    ratio breaks above N=10 blocks; character-mass survives any N because
    a single large garbled block carries proportional weight.

    Always returns a GarbleReport — ``is_garbled=False`` when clean or
    below threshold, ``True`` when condemned.  Callers use truthiness
    (``if report:``) which maps to ``is_garbled`` via ``__bool__``.
    Sub-threshold prongs and ratio are preserved so ``flat_meta`` can
    record them (HR5: never silently persist low-quality data).
    """
    from .flat import BlockTextPurpose, block_text

    all_fired: set[str] = set()
    garbled_count = 0
    checked_count = 0
    total_chars = 0
    garbled_chars = 0

    for block in blocks:
        text = block_text(block, BlockTextPurpose.CHAR_COUNT)
        if not text or not text.strip():
            continue
        checked_count += 1
        block_char_count = len(text)
        total_chars += block_char_count
        report = detect_garble(
            text,
            script_context=script_context,
            config=config,
            blob_kind=BlobKind.RAW_MARKDOWN,
        )
        if report:
            garbled_count += 1
            garbled_chars += block_char_count
            all_fired.update(report.fired_prongs)

    _block_ratio = garbled_count / checked_count if checked_count else 0.0
    _char_ratio = garbled_chars / total_chars if total_chars else 0.0

    _common_attrs = {
        "checked_count": checked_count,
        "garbled_count": garbled_count,
        "block_ratio": _block_ratio,
        "char_ratio": _char_ratio,
        "total_chars": total_chars,
        "garbled_chars": garbled_chars,
    }

    if not garbled_count:
        decision(
            event="garble_flat_block_verdict",
            choice="clean",
            reason="no_blocks_garbled",
            attrs={**_common_attrs, "fired_prongs": []},
            logger=logger,
        )
        return GarbleReport(
            is_garbled=False,
            fired_prongs=frozenset(),
            garble_ratio=0.0,
        )

    if _char_ratio < _GARBLE_CHAR_MASS_THRESHOLD:
        decision(
            event="garble_flat_block_verdict",
            choice="below_threshold",
            reason="garbled_char_mass_below_threshold",
            attrs={
                **_common_attrs,
                "threshold": _GARBLE_CHAR_MASS_THRESHOLD,
                "fired_prongs": sorted(all_fired),
            },
            logger=logger,
        )
        return GarbleReport(
            is_garbled=False,
            fired_prongs=frozenset(all_fired),
            garble_ratio=_char_ratio,
        )

    decision(
        event="garble_flat_block_verdict",
        choice="garbled",
        reason="char_mass_garbled",
        attrs={**_common_attrs, "fired_prongs": sorted(all_fired)},
        logger=logger,
    )
    return GarbleReport(
        is_garbled=True,
        fired_prongs=frozenset(all_fired),
        garble_ratio=_char_ratio,
    )


def ocr_noise_ratio(text: str) -> float:
    if not text:
        return 0.0
    noise = sum(
        1
        for c in text
        if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or (ord(c) < 32 and c not in "\n\r\t")
    )
    return noise / len(text)


def hash_pipe_ratio(text: str) -> float:
    """Ratio of '#' and '|' characters in *text*.

    Zone-5 defensive guard: lines that look like markdown table rows
    (start with '|') have their pipe characters exempted from the count,
    so table content included via _flatten_tree_text (Zone-5 fix) does
    not inflate the ratio and block category-C promotion in verdict.py.
    """
    if not text:
        return 0.0
    count = 0
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("|"):
            # Exempt pipe chars in markdown table rows; still count '#'
            count += sum(1 for c in line if c == "#")
        else:
            count += sum(1 for c in line if c in "#|")
    return count / len(text)


def _garble_ratio(text, expected_script=None, *, script_context=None):
    """Windowed garble ratio: fraction of fixed-size windows that individually
    trigger garble detection. RFC-033 D1: no longer re-checks the full text
    (detect_garble already gates in classify_verdict).
    Uses detect_garble with TREE_TEXT blob kind and frozen _garble_config.

    Zone-4: accepts optional ``script_context`` for proper
    had_presentation_forms threading; falls back to building one from
    ``expected_script`` when not provided (backward compat).
    """
    _ctx = (
        script_context
        if script_context is not None
        else ScriptContext(
            dominant_script=expected_script,
            # pre-NFKC: post-normalize but safe — returns False on destroyed PF
            had_presentation_forms=_infer_presentation_forms(text),
            source="garble_ratio",
        )
    )
    window = 2000
    if len(text) <= window:
        return (
            1.0
            if detect_garble(
                text,
                script_context=_ctx,
                config=_garble_config,
                blob_kind=BlobKind.TREE_TEXT,
            )
            else 0.0
        )
    chunks = [text[i : i + window] for i in range(0, len(text), window)]
    garbled_chunks = sum(
        1
        for c in chunks
        if detect_garble(
            c,
            script_context=_ctx,
            config=_garble_config,
            blob_kind=BlobKind.TREE_TEXT,
        )
    )
    return garbled_chunks / len(chunks)


# re-import for _garble_prongs usage
from ..script import is_arabic_char as _is_arabic_char  # noqa: E402
