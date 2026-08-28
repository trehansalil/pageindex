"""Garble detection: prongs, config, and per-node checking."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import pipeline_config
from ..script import (
    ARABIC_RANGES,
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
    if not text:
        return False
    pf_count = sum(1 for c in text if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES))
    ar_count = sum(1 for c in text if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES))
    return ar_count > 0 and (pf_count / ar_count) > 0.50


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


def garble_prongs(
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
            nonsense = sum(1 for t in latin_tokens if _is_morphologically_nonsense(t))
            if nonsense / len(latin_tokens) > nonsense_threshold:
                prongs.add("latin_gibberish")

    _sparse_text = original_text if original_text is not None else norm
    if len(_sparse_text) >= 100:
        _sparse_matches = _MIXED_SCRIPT_RE.findall(_sparse_text)
        if (len(_sparse_matches) / max(len(_sparse_text.split()), 1)) > 0.02:
            prongs.add("sparse_mojibake")

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
    title: str = "",
    script_context: ScriptContext,
    config: GarbleConfig,
    blob_kind: BlobKind = BlobKind.TREE_TEXT,
    original_defect: TreeDefect | None = None,
) -> GarbleReport:
    """Unified garble evaluation entry point (Zone-3).

    Single-surface API: all garble heuristics (bulk prongs + sparse mojibake
    + presentation-forms) run inside ``garble_prongs``.

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
    if not _had_pf:
        _pf = sum(1 for c in blob if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES))
        _arc = sum(1 for c in blob if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES))
        if _arc > 0 and (_pf / _arc) > 0.50:
            _had_pf = True
        elif _arc > 0 and _pf == 0 and _effective_script == "Arabic":
            # Pipeline NFKC normalization decomposes presentation-form codepoints
            # (U+FB50-FEFF) before text reaches detect_garble. When the dominant
            # script is Arabic but zero presentation forms survive, assume the
            # raw document had them — the ScriptContext.from_document path scans
            # pre-normalization text and gets this right; this fallback covers
            # callers that don't.
            _had_pf = True

    _use_raw_md = blob_kind == BlobKind.RAW_MARKDOWN and config.garble_flat_markdown_normalize
    _norm_kind = BlobKind.RAW_MARKDOWN if _use_raw_md else BlobKind.TREE_TEXT
    norm = normalize_for_garble(blob, _norm_kind)
    if not norm.strip():
        norm = blob

    prongs = garble_prongs(
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
    return GarbleReport(
        is_garbled=bool(prongs),
        fired_prongs=prongs,
        garble_ratio=1.0 if prongs else 0.0,
    )



# Zone-4: _rebuild_garble_config_compat and check_garble deleted — detect_garble
# is now the sole public entry point.  GarbleReport.__bool__ is the drop-in
# replacement for check_garble's bool return value.


_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x21-\x7E]{1,8}[؀-ۿ]"
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{1,8}"
)


_GARBLE_NODE_RATIO_THRESHOLD_RAW = pipeline_config.garble_node_ratio_threshold
_GARBLE_NODE_RATIO_THRESHOLD = pipeline_config.garble_node_ratio_threshold
_EMPTY_NODE_FRACTION_THRESHOLD = pipeline_config.empty_node_fraction_threshold
_RFC029_FLAT_PREFER_MULTIPLIER = pipeline_config.rfc029_flat_prefer_multiplier
_RFC029_MIN_CHARS_PER_NODE = pipeline_config.rfc029_min_chars_per_node
_RFC029_MIN_CHARS_PER_NODE_DEEP = pipeline_config.rfc029_min_chars_per_node_deep
_RFC029_DEEP_TREE_DEPTH_THRESHOLD = 4
_RFC029_MIN_SCANNED_DENSITY_FLOOR = pipeline_config.rfc029_min_scanned_density_floor

infer_script = _infer_script


def _collect_all_node_text(nodes: list[dict]) -> str:
    """Recursively collect all node text into a single concatenated string.

    Zone-5 fix: also extracts table block content from 'headers', 'rows',
    and 'row_records' via tree_validation._node_text_parts, so per-node
    garble checking sees table-heavy nodes.
    """
    from .tree_validation import _node_text_parts

    parts: list[str] = []
    for node in nodes:
        node_parts = _node_text_parts(node)
        for p in node_parts:
            if p.strip():
                parts.append(p)
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
    runs garble_prongs on the joined text of all nodes.  This catches
    garble patterns that fall below garble_digit_floor per node but surface
    in aggregate.
    """
    _doc_script = script_context.dominant_script

    garbled = 0
    for node in nodes:
        node_garbled = False
        text = node.get("text") or ""
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
    # garbled, run garble_prongs on the joined text to catch patterns that
    # fall below garble_digit_floor per node but surface in aggregate.
    if _is_toplevel and garbled == 0:
        _concat = _collect_all_node_text(nodes)
        _norm = normalize_for_garble(_concat, BlobKind.TREE_TEXT)
        _fallback_prongs = garble_prongs(
            _norm,
            expected_script=_doc_script,
            original_text=_concat,
            had_presentation_forms=script_context.had_presentation_forms,
            config=config,
        )
        if _fallback_prongs:
            logger.info(
                "Whole-tree concatenated fallback detected garble: prongs=%s",
                _fallback_prongs,
            )
            garbled = 1
    return garbled


def _garble_check_flat_blocks(
    blocks: list[dict],
    *,
    script_context: ScriptContext,
    config: GarbleConfig,
) -> GarbleReport | None:
    """Zone-1: per-block garble check for flat-routed documents.

    Runs detect_garble on each block individually (using
    _flat_block_primary_text), eliminating the dilution problem where a
    single garbled table amid clean prose would pass the whole-blob check.

    Returns a synthetic GarbleReport if any block is garbled, None otherwise.
    """
    from .flat import _flat_block_primary_text

    all_fired: set[str] = set()
    garbled_count = 0
    checked_count = 0

    for block in blocks:
        text = _flat_block_primary_text(block)
        if not text or not text.strip():
            continue
        checked_count += 1
        report = detect_garble(
            text,
            script_context=script_context,
            config=config,
            blob_kind=BlobKind.RAW_MARKDOWN,
        )
        if report:
            garbled_count += 1
            all_fired.update(report.fired_prongs)

    if not garbled_count:
        return None

    return GarbleReport(
        is_garbled=True,
        fired_prongs=frozenset(all_fired),
        garble_ratio=garbled_count / checked_count if checked_count else 0.0,
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
    _ctx = script_context if script_context is not None else ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=_infer_presentation_forms(text),
        source="garble_ratio",
    )
    window = 2000
    if len(text) <= window:
        return (
            1.0
            if detect_garble(
                text, script_context=_ctx, config=_garble_config,
                blob_kind=BlobKind.TREE_TEXT,
            )
            else 0.0
        )
    chunks = [text[i : i + window] for i in range(0, len(text), window)]
    garbled_chunks = sum(
        1
        for c in chunks
        if detect_garble(
            c, script_context=_ctx, config=_garble_config,
            blob_kind=BlobKind.TREE_TEXT,
        )
    )
    return garbled_chunks / len(chunks)


# re-import for garble_prongs usage
from ..script import is_arabic_char as _is_arabic_char  # noqa: E402
