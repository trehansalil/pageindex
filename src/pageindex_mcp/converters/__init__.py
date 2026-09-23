"""converters package — re-exports every public symbol for backward compatibility."""

from __future__ import annotations

# --- Re-exports from sibling modules (external callers import via converters) ---
from ..script import _AR_COMMON_WORDS as _AR_COMMON_WORDS
from ..script import AR_CHAR_RE as _AR_LETTER_RE
from ..script import AR_CHAR_RE as _AR_SCRIPT_RE
from ..script import (
    RtlDecision,
    apply_rtl,
    decide_rtl,
    normalize_dashes,
)
from ..script import arabic_readability_score as _arabic_readability_score
from ..script import is_arabic_char as _is_arabic_char

# --- docling_conv.py ---
from .docling_conv import (
    _RFC029_TABLE_DEDUP_ENABLED,
    _RFC029_TABLE_MIN_COLLAPSE_COLS,
    _build_pdf_pipeline_options,
    _docling_converter,
    _patch_hierarchical_infer,
    _repair_docling_tables,
    _run_pdf_inspector,
    chunked_docling_timeout_s,
    probe_conversion_route,
)

# --- preclassify.py ---
from .preclassify import (
    PreClassification,
    detect_lang_from_text_layer,
    merge_lang_sources,
    preclassify_document,
)

# --- formats.py ---
from .formats import (
    docx_to_markdown,
    html_to_markdown_with_images,
    image_to_markdown,
    libreoffice_to_pdf,
    pptx_to_markdown,
    tesseract_ocr_pdf_pages,
    vlm_extract_markdown,
    xlsx_to_markdown,
)

# --- headings.py ---
from .headings import (
    _AR_ARTICLE_RE,
    _AR_MARKER_CAPTURE_RE,
    _AR_PART_RE,
    _AR_WORD_RE,
    _HEADING_RE,
    _apply_outline_levels,
    _candidate_from_document,
    _collapse_spaced,
    _containment_depths,
    _has_structural_depth,
    _heading_count,
    _inject_arabic_structural_headings,
    _inject_english_article_headings,
    _inject_german_clause_headings,
    _is_numeric_extension,
    _max_heading_level,
    _outline_norm,
    _read_pdf_outline,
    _recover_heading_depth,
    _relevel_by_containment,
    _relevel_headings,
    _repromote_numbered_headings,
    _segment_label,
    _splice_landscape_fallback,
    _split_alnum,
    _title_matches,
    numbering_depth,
    pdf_to_markdown,
)

# --- normalize.py ---
from .normalize import (
    BIDI_NORM_VERSION,
    _fix_fi_hash_substitution,
    _normalize_indented_headings,
    _split_run_together_headings,
    reconstruct_bidi_order,
)

# --- ocr_langs.py ---
from .ocr_langs import (
    _try_download_tessdata,
    detect_ocr_langs,
    ensure_tessdata,
)

# --- pictures.py ---
from .pictures import (
    _COVERAGE_EXEMPT_NO_TEXT_LAYER,
    _DECORATIVE_ICON_MIN_DIM_PT,
    _DOC_TEXT_FALLBACK_MIN_CHARS,
    _GATE_CONFIG,
    _IMAGE_MARKER,
    _MAX_FULLPAGE_PICTURE_OCR_REGIONS,
    _PICTURE_OCR_MIN_CHARS,
    _PICTURE_PAGE_COVERAGE_THRESHOLD,
    LANDSCAPE_CHAR_THRESHOLD,
    LANDSCAPE_REEXTRACT_DEADLINE_SECONDS,
    MAX_LANDSCAPE_PAGES,
    _add_vlm_descriptions,
    _bbox_to_fitz_rect,
    _clip_text_contained,
    _document_level_text_fallback,
    _landscape_pages_below_threshold,
    _landscape_rasterize_rotate_reextract,
    _normalize_for_containment,
    _normalize_pdf_page_rotation,
    _page_rotation_correction_info,
    _pre_inference_normalize,
    _recover_picture_results,
    _recover_picture_text,
    _tag_landscape_pages_for_fallback,
    _tesseract_ocr_image,
    _text_layer_has_content,
    splice_figure_markers,
    splice_picture_text_for_tree,
    zdr_egress_gate,
)

# --- pipeline.py ---
from .pipeline import (
    ConverterChainEntry,
    ConverterFailurePolicy,
    as_chain_entry,
    _build_candidate,
    _run_stages,
    pdf_markdown_converters,
    pdf_to_markdown_docling,
)

# --- types.py ---
from .types import Candidate, PictureResult, TessdataUnavailableError

__all__ = [
    "LANDSCAPE_CHAR_THRESHOLD",
    "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS",
    "MAX_LANDSCAPE_PAGES",
    "_AR_ARTICLE_RE",
    # re-exports from ..script
    "_AR_COMMON_WORDS",
    "_AR_LETTER_RE",
    "_AR_MARKER_CAPTURE_RE",
    "_AR_PART_RE",
    "_AR_SCRIPT_RE",
    "_AR_WORD_RE",
    "_COVERAGE_EXEMPT_NO_TEXT_LAYER",
    "_DECORATIVE_ICON_MIN_DIM_PT",
    "_DOC_TEXT_FALLBACK_MIN_CHARS",
    "_GATE_CONFIG",
    "_HEADING_RE",
    "_IMAGE_MARKER",
    "_MAX_FULLPAGE_PICTURE_OCR_REGIONS",
    "_PICTURE_OCR_MIN_CHARS",
    "_PICTURE_PAGE_COVERAGE_THRESHOLD",
    "_RFC029_TABLE_DEDUP_ENABLED",
    "_RFC029_TABLE_MIN_COLLAPSE_COLS",
    "ConverterChainEntry",
    "ConverterFailurePolicy",
    "as_chain_entry",
    # types
    "Candidate",
    "BIDI_NORM_VERSION",
    "PictureResult",
    "RtlDecision",
    "TessdataUnavailableError",
    # pictures
    "_add_vlm_descriptions",
    # headings
    "_apply_outline_levels",
    "_arabic_readability_score",
    "_bbox_to_fitz_rect",
    # pipeline
    "_build_candidate",
    # docling_conv
    "_build_pdf_pipeline_options",
    "_candidate_from_document",
    "_clip_text_contained",
    "_collapse_spaced",
    "_containment_depths",
    "_docling_converter",
    "_document_level_text_fallback",
    # normalize
    "_fix_fi_hash_substitution",
    "_has_structural_depth",
    "_heading_count",
    "_inject_arabic_structural_headings",
    "_inject_english_article_headings",
    "_inject_german_clause_headings",
    "_is_arabic_char",
    "_is_numeric_extension",
    "_landscape_pages_below_threshold",
    "_landscape_rasterize_rotate_reextract",
    "_max_heading_level",
    "_normalize_for_containment",
    "_normalize_indented_headings",
    "_normalize_pdf_page_rotation",
    "_outline_norm",
    "_page_rotation_correction_info",
    "_patch_hierarchical_infer",
    "_pre_inference_normalize",
    "_read_pdf_outline",
    "_recover_heading_depth",
    "_recover_picture_results",
    "_recover_picture_text",
    "_relevel_by_containment",
    "_relevel_headings",
    "_repair_docling_tables",
    "_repromote_numbered_headings",
    "_run_pdf_inspector",
    "_run_stages",
    "_segment_label",
    "_splice_landscape_fallback",
    "_split_alnum",
    "_split_run_together_headings",
    "_tag_landscape_pages_for_fallback",
    "_tesseract_ocr_image",
    "_text_layer_has_content",
    "_title_matches",
    "_try_download_tessdata",
    "apply_rtl",
    "chunked_docling_timeout_s",
    "decide_rtl",
    "detect_lang_from_text_layer",
    "detect_ocr_langs",
    "docx_to_markdown",
    "ensure_tessdata",
    "html_to_markdown_with_images",
    "image_to_markdown",
    "libreoffice_to_pdf",
    "normalize_dashes",
    "numbering_depth",
    "pdf_markdown_converters",
    "pdf_to_markdown",
    "pdf_to_markdown_docling",
    "pptx_to_markdown",
    "PreClassification",
    "merge_lang_sources",
    "preclassify_document",
    "probe_conversion_route",
    "reconstruct_bidi_order",
    "splice_figure_markers",
    "splice_picture_text_for_tree",
    "tesseract_ocr_pdf_pages",
    "vlm_extract_markdown",
    "xlsx_to_markdown",
    "zdr_egress_gate",
]
