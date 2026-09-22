"""CustomPageIndexClient — multi-format document indexing with MinIO persistence."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pageindex import PageIndexClient

from ..cache import get_doc
from ..config import (
    CLIENT_BUILD_SHA,
    CONVERTER_TRANSIENT_RETRY_COUNT,
    CURRENT_PIPELINE_VERSION,
    VERDICT_DOWNGRADE_ENABLED,
    ZDRComplianceError,
    pipeline_config,
    settings,
)
from ..converters import (
    ConverterFailurePolicy,
    PictureResult,
    TessdataUnavailableError,
    _tesseract_ocr_image,
    as_chain_entry,
    detect_ocr_langs,
    docx_to_markdown,
    ensure_tessdata,
    html_to_markdown_with_images,
    image_to_markdown,
    libreoffice_to_pdf,
    pdf_markdown_converters,
    pptx_to_markdown,
    reconstruct_bidi_order,
    splice_figure_markers,
    splice_picture_text_for_tree,
    xlsx_to_markdown,
    zdr_egress_gate,
)
from ..helpers import (
    GATES,
    Candidate,
    ExtractionState,
    LowQualityTreeError,
    Route,
    TreeDefect,
    TreeSignals,
    VerdictThresholds,
    _extract_page_hits,
    _flat_block_primary_text,
    _flatten_tree_text,
    _garble_check_flat_blocks,
    _garble_config,
    _has_any_presentation_form,
    _infer_presentation_forms,
    _pf_ratio,
    _strip_text,
    _strip_toc_heading_nodes_guarded,
    _synthesize_preamble_node,
    _tree_max_leaf_ratio,
    _tree_node_count,
    compute_verdict,
    arbitrate,
    detect_garble,
    finalize_gate_and_route,
    prepare_tree,
    route_and_extract_flat,
    validate_tree,
)
from ..metrics import (
    AGPL_FALLBACK_TOTAL,
    BIDI_RENORM_SKIPPED,
    FLAT_DOCS_TOTAL,
    HR3_EGRESS_BLOCKED_TOTAL,
    LOW_QUALITY_TREES,
    PDF_EXTRACT_FALLBACKS,
    PDF_INSPECTOR_FORCED_OCR,
    PDF_PRIMARY_CONVERTER_FAILURES,
    RAW_UPLOAD_FAILURES,
    REMOTE_MD_RENORMALIZED,
    VLM_FALLBACK_TOTAL,
)
from ..obs import bind_log_context
from ..obs.decisions import decision
from ..picture_plane import OcrEngine, strip_unresolved_image_markers
from ..script import PF_SIGNAL_RATIO, BlobKind, RtlDecision, ScriptContext
from ..storage import (
    hash_cache_get,
    hash_cache_set,
    list_processed_docs,
    save_doc,
    save_doc_meta,
    save_flat_doc,
    save_raw,
)
from ..worker.constants import INSPECTOR_CONFIDENCE_THRESHOLD

logger = logging.getLogger(__name__)

_MAX_DESC_CHARS = 4000

# D3a content-density heuristic: if page-0 extractable text is shorter than
# this on a multi-page PDF, the document is likely scanned/image-based and
# needs full-page OCR upfront.  Decouples the OCR trigger from garble-
# detection sensitivity so that scanned Arabic PDFs with no presentation-form
# codepoints still get OCR.  200 chars is roughly one short paragraph — well
# above any header/footer-only text layer but low enough to catch genuinely
# scanned pages (which typically yield 0 chars from fitz).
D3A_SPARSE_PAGE_CHAR_FLOOR = 200

# Standalone-image garble detection: image OCR is inherently noisier than
# PDF text extraction, so we use a lower nonsense-ratio threshold (0.45
# vs the default 0.70) to catch Latin-script OCR garble from misrecognised
# non-Latin source images (e.g. Arabic pie charts OCR'd with eng tessdata).
IMAGE_OCR_NONSENSE_RATIO = 0.45

# RFC-034 D17: bilingual documents (>30% Latin interleaved with Arabic) skip
# the D3 reconstruct_bidi_order re-normalization pass -- it collapses blocks
# on mixed-script content instead of correcting stale-remote heading reversal.
_BIDI_RENORM_LATIN_GUARD = 0.30


def _latin_fraction(md_content: str) -> float:
    """Fraction of `md_content` that is ASCII-alphabetic (RFC-034 D17)."""
    return sum(1 for c in md_content if c.isascii() and c.isalpha()) / max(len(md_content), 1)


def _renormalize_bidi_guarded(
    md_content: str,
    filename: str,
) -> tuple[str, RtlDecision | None]:
    """RFC-034 D3 re-normalization with the D17 bilingual guard.

    Applies `reconstruct_bidi_order` unless the document's Latin-character
    fraction exceeds `_BIDI_RENORM_LATIN_GUARD`, in which case the pass is
    skipped (it collapses blocks on mixed-script content) and the skip is
    logged plus counted so it is observable in Prometheus.

    Zone-6: returns ``(text, RtlDecision | None)`` so the remote path can
    thread the same decision into ``validate_tree`` without recomputing.
    When the bilingual guard skips, an explicit sentinel decision is
    returned (``method='bilingual_guard_skip'``) instead of silent None.

    Bidi-RTL-split fix: now runs NFKC canonicalization on Arabic
    Presentation Forms and captures ``had_presentation_forms`` on the
    RtlDecision, matching the local-path depth in
    ``normalize._pre_inference_normalize``.  Without this, documents
    routed through the remote Docling path retained presentation-form
    codepoints that the local path canonicalized, and the bidi coherence
    gate (``_gate_bidi_degraded``) could not detect degradation because
    the signal was absent from the decision object.
    """
    import dataclasses
    import unicodedata

    from ..converters.normalize import BIDI_NORM_VERSION

    latin_frac = _latin_fraction(md_content)
    if latin_frac > _BIDI_RENORM_LATIN_GUARD:
        BIDI_RENORM_SKIPPED.inc()
        decision(
            event="bidi_renorm_bilingual_guard",
            choice="skipped_bilingual_guard",
            reason="latin fraction exceeds guard threshold",
            attrs={
                "latin_frac": latin_frac,
                "bidi_renorm_latin_guard": _BIDI_RENORM_LATIN_GUARD,
                "bidi_norm_version": BIDI_NORM_VERSION,
            },
        )
        return md_content, RtlDecision(
            reversed=False,
            repair_effective=False,
            sampled=0,
            method="bilingual_guard_skip",
        )
    renorm, rtl_decision = reconstruct_bidi_order(md_content)
    decision(
        event="bidi_renorm_bilingual_guard",
        choice="applied",
        reason="latin fraction within guard threshold",
        attrs={
            "latin_frac": latin_frac,
            "bidi_renorm_latin_guard": _BIDI_RENORM_LATIN_GUARD,
            "bidi_norm_version": BIDI_NORM_VERSION,
        },
    )
    if renorm != md_content:
        REMOTE_MD_RENORMALIZED.inc()
        logger.debug(
            "D3 re-normalization changed %d chars for %s (bidi_norm_v%d)",
            len(md_content) - len(renorm),
            filename,
            BIDI_NORM_VERSION,
        )

    # NFKC triggers on ANY presentation-form codepoint (side effect).
    # The had_presentation_forms SIGNAL is ratio-gated at PF_SIGNAL_RATIO
    # so a single ﷲ among unshaped Arabic does not condemn the document.
    has_any_pf = _has_any_presentation_form(renorm)
    pf_signal = _pf_ratio(renorm) > PF_SIGNAL_RATIO if has_any_pf else False
    if has_any_pf:
        renorm = unicodedata.normalize("NFKC", renorm)
    if pf_signal and rtl_decision is not None:
        rtl_decision = dataclasses.replace(rtl_decision, had_presentation_forms=True)
    decision(
        event="bidi_presentation_form_canonicalization",
        choice="nfkc_applied" if has_any_pf else "not_needed",
        reason="presentation-form codepoints found"
        if has_any_pf
        else "no presentation-form codepoints",
        attrs={"had_presentation_forms": pf_signal, "pf_any": has_any_pf},
    )

    return renorm, rtl_decision


def _detect_config_drift(job_start_config: dict | None, effective_cfg: dict) -> dict | None:
    """Zone-7: return job_start_config only when it diverges from the config
    freshly snapshotted at job execution time, else None. A standalone
    function (rather than inline in index()) so the comparison is unit
    testable without invoking the full indexing pipeline.
    """
    drift = job_start_config is not None and job_start_config != effective_cfg
    decision(
        event="config_drift_detected",
        choice="drift_detected" if drift else "no_drift",
        reason="job_start_config differs from effective config"
        if drift
        else "configs match or no job_start_config",
        attrs={"drift_present": drift},
    )
    if drift:
        return job_start_config
    return None


def _generate_flat_doc_description(text: str, model: str | None = None, *, doc_id: str = "") -> str:
    """Generate an LLM description for a flat document from its markdown text.

    HR3 (audit findings 2/3): rides ``zdr_egress_gate`` — when ``pii_corpus`` is
    set and the endpoint is not ZDR-allowlisted, NO document text egresses and
    the description is empty. The gated ``api_base`` is passed explicitly to
    ``litellm.completion`` so the inspected endpoint is the one used."""
    allowed, api_base = zdr_egress_gate("flat doc description", doc_id=doc_id)
    if not allowed:
        return ""

    from litellm import completion

    if not model:
        model = settings.llm_model
    snippet = text[:_MAX_DESC_CHARS]
    try:
        resp = completion(
            model=model,
            api_base=api_base,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are an expert in generating descriptions of a document. "
                        "You are given the text of a document. Your task is to generate "
                        "one-sentence description of the document, that makes it easy to "
                        "distinguish this document from other documents.\n\n"
                        f"Document Text:\n{snippet}\n\n"
                        "Directly return the description, do not include any other text."
                    ),
                }
            ],
            max_tokens=200,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("flat doc description generation failed: %s", exc)
        return ""


# Zone-8: _IMAGE_EXTS, _IMAGE_STANDALONE_PIPELINE_ENABLED, and
# MIN_STANDALONE_IMAGE_MD_CHARS are now imported from images.py (canonical source)
# to eliminate constant duplication across two files.
from .images import _IMAGE_EXTS, MIN_STANDALONE_IMAGE_MD_CHARS  # noqa: E402

_SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".html", ".xlsx"} | _IMAGE_EXTS
# Zone-4: legacy _OCR_ESCALATION removed; split flags _OCR_ESCALATION_GARBLE /
# _OCR_ESCALATION_PER_PICTURE imported from config.py (canonical source).
# RFC-027 D2: PDFs rejected as node_count<3 with fewer than this many chars (zero or
# near-zero/garbled scanned content) also earn the force_full_page_ocr retry, not just
# the garbling reasons above -- calibrated to the Run-10 corpus (highest affected doc
# القرار التنظيمي at 230 garbled chars; legitimate sparse docs all exceed 400 chars).
# Zone-5 config layering: deprecated read-through alias.  The canonical value
# is ``pipeline_config.low_content_ocr_char_floor`` (and the live copy used by
# the recovery paths lives in client/recovery.py).  Kept only so existing
# ``client.indexer.LOW_CONTENT_OCR_CHAR_FLOOR`` readers keep resolving.
LOW_CONTENT_OCR_CHAR_FLOOR = pipeline_config.low_content_ocr_char_floor
# Zone-5 dead-code removal: _VLM_TESSERACT_FALLBACK_ENABLED,
# _D7_GARBLE_RECOVERY_ENABLED, _RFC029_FLAT_PREFER_MULTIPLIER and
# _RFC029_MIN_CHARS_PER_NODE used to be re-read from os.getenv here as a second
# config layer.  Nothing in this module ever read them (the live values are
# client/recovery.py's pipeline_config reads and helpers' constants), and the
# _RFC029_MIN_CHARS_PER_NODE copy had drifted to a stale default of 500 vs the
# canonical 150.  Removed — read ``pipeline_config.<attr>`` instead.
# RFC-023 D11: _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED is read from
# pipeline_config (Zone-2 flag decoupling — eliminates env-var read duplication).


def _split_converter_output(out) -> tuple[str, list, list]:
    """Normalize PDF-converter result ``(markdown, pic_results, extraction_stages)``.

    Chain callables return ``(md, pics, stages)``; 2-tuple (legacy chain
    entries, remote-docling branch) tolerated mapped empty stages;
    bare string maps empty pic_results stages."""
    if isinstance(out, tuple):
        if len(out) >= 3:
            md, pics, stages = out[0], out[1], out[2]
            return md, list(pics or []), list(stages or [])
        md, pics = out[0], out[1]
        return md, list(pics or []), []
    return out, [], []


# ---------------------------------------------------------------------------
# Converter chain: transient-failure classification
# ---------------------------------------------------------------------------

# Exception types that indicate a transient (retryable) infrastructure
# failure rather than a structural (parse/import) error.  When a converter
# raises one of these, the chain walker should NOT silently fall through to
# an AGPL-licensed converter — the operator did not intend AGPL conversion
# just because a network hop timed out (HR4).
_TRANSIENT_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)


def _classify_transient_failure(exc: BaseException) -> bool:
    """Return ``True`` when *exc* looks like a transient infrastructure failure.

    Checks:
    1. Direct isinstance match against ``_TRANSIENT_EXCEPTION_TYPES``
       (``TimeoutError``, ``ConnectionError``, ``OSError``, ``asyncio.TimeoutError``).
    2. ``httpx.TimeoutException`` / ``httpx.ConnectError`` (lazy check via
       module name so httpx is not a hard import-time dependency).
    3. Any exception carrying a ``status_code`` attribute >= 500 (covers
       ``httpx.HTTPStatusError`` and similar HTTP-wrapper exceptions for
       server-side errors like 502/503/504).

    Everything else (``ValueError``, ``RuntimeError``, ``ImportError``,
    parse-level exceptions) is classified as structural.
    """
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return True

    # httpx exceptions: TimeoutException and ConnectError are not subclasses
    # of the stdlib types above, so check by module/class name to avoid a
    # hard dependency on httpx at import time.
    exc_module = type(exc).__module__ or ""
    if exc_module.startswith("httpx"):
        exc_class = type(exc).__name__
        if exc_class in (
            "TimeoutException",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "ConnectError",
        ):
            return True

    # HTTP 5xx status code (server error) on any exception that carries one.
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            if int(status_code) >= 500:
                return True
        except (TypeError, ValueError):
            pass

    # Also check for httpx response attribute (HTTPStatusError stores the
    # response on exc.response).
    response = getattr(exc, "response", None)
    if response is not None:
        resp_status = getattr(response, "status_code", None)
        if resp_status is not None:
            try:
                if int(resp_status) >= 500:
                    return True
            except (TypeError, ValueError):
                pass

    return False


# Imports from sibling submodules — used by the class methods below.
from . import remote as _remote_mod  # noqa: E402
from .images import (  # noqa: E402
    TREE_PATH_PICTURE_SPLICE_ENABLED,
    _apply_picture_enrichment,
    _dominant_orientation,
    _log_pic_splice_trace,
)
from .llm import (  # noqa: E402
    _llm_with_retry,
)
from .recovery import RecoveryMixin  # noqa: E402
from .remote import (  # noqa: E402
    _converter_contract,
    _remote_pdf_to_markdown,
)


class CustomPageIndexClient(RecoveryMixin, PageIndexClient):
    """
    Extends PageIndexClient to support .docx, .pptx, .html, and .txt formats
    and persist all indexed data to MinIO instead of a local filesystem workspace.

    Usage:
        client = CustomPageIndexClient()
        doc_id = await client.index("/path/to/file.docx")
        structure = await client.get_document_structure(doc_id)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
    ):
        super().__init__(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.llm_model
        self.retrieve_model = retrieve_model
        # RFC-004 Amendment 1 (Step 5 integration): set to the deterministic
        # content_class when index() routes a doc to the flat success path; stays
        # None for a normal tree doc. converters_cli reads this after index()
        # returns so the worker job hash can carry content_class (FLAT-04-C1).
        self.last_content_class: str | None = None
        # Zone-7: verdict fields computed during _persist_tree_result or
        # _persist_flat_result, surfaced to converters_cli via the same
        # getattr pattern as last_content_class so the worker parent can
        # thread them into _upsert_registry_row, closing the MinIO re-read
        # race window for verdict data.
        self.last_verdict_fields: dict[str, Any] | None = None
        self._staging_key: str | None = None

    # ------------------------------------------------------------------
    # Indexing — Zone-2 extracted helpers
    # ------------------------------------------------------------------

    async def _reconvert_and_revalidate(
        self,
        state: ExtractionState,
        md_content: str,
        *,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Write md → run_md_to_tree → split → segment → validate. Mutates state."""
        if state.tmp_md_path and os.path.exists(state.tmp_md_path):
            os.unlink(state.tmp_md_path)
        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        ) as md_tmp:
            md_tmp.write(md_content)
            state.tmp_md_path = md_tmp.name
        state.result = await self._run_md_to_tree(state.tmp_md_path)
        state.result["structure"] = prepare_tree(
            state.result.get("structure", []),
            orientation=_dominant_orientation(state.landscape_pages),
        )
        _vt_raw = validate_tree(
            state.result.get("structure", []),
            expected_script=script_context if script_context is not None else expected_script,
            page_count=state.pdf_page_count,
            rtl_decision=state.rtl_decision,
        )
        finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing)

    async def _convert_to_tree(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        pdf_classification: dict | None,
        *,
        pre_classification: dict | None = None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Conversion front-end: dispatch by extension, run initial validate_tree. Mutates state."""
        if ext == ".pdf":
            md_content = None

            inspector_force_ocr = False
            if (
                pipeline_config.pdf_inspector_preclassify
                and pdf_classification is not None
                and pdf_classification.get("pdf_type") in ("scanned", "image_based")
                and pdf_classification.get("confidence", 0) >= INSPECTOR_CONFIDENCE_THRESHOLD
            ):
                inspector_force_ocr = True
                PDF_INSPECTOR_FORCED_OCR.inc()
            decision(
                event="pdf_inspector_force_ocr",
                choice="forced_by_inspector" if inspector_force_ocr else "not_forced",
                reason="inspector preclassify triggered"
                if inspector_force_ocr
                else "inspector gate not met",
                attrs={
                    "pdf_type": pdf_classification.get("pdf_type") if pdf_classification else None,
                    "confidence": pdf_classification.get("confidence", 0)
                    if pdf_classification
                    else 0,
                    "inspector_confidence_threshold": INSPECTOR_CONFIDENCE_THRESHOLD,
                },
            )

            # --- OCR language resolution: prefer pre-classification content-derived
            # langs when PRECLASSIFY_ENABLED, else fall back to filename heuristic.
            _preclass_ocr_langs = (pre_classification or {}).get("ocr_langs")
            if pipeline_config.preclassify_enabled and _preclass_ocr_langs:
                _ocr_lang_override = _preclass_ocr_langs
            else:
                _ocr_lang_override = detect_ocr_langs(filename)

            state.pre_garbled = False
            state.pdf_page_count = None

            # When preclassify already detected a garbled text layer, consume
            # that signal directly instead of re-probing with fitz.  The D3a
            # fitz block below still runs for landscape_pages / page_count.
            if (
                pipeline_config.preclassify_enabled
                and (pre_classification or {}).get("garbled_text_layer")
            ):
                state.pre_garbled = True
                decision(
                    event="d3a_pre_garble_probe",
                    choice="pre_garbled_from_preclassify",
                    reason="preclassify detected garbled text layer",
                    attrs={
                        "text_layer_chars": (pre_classification or {}).get("text_layer_chars", 0),
                        "alpha_ratio": (pre_classification or {}).get("alpha_ratio", 0.0),
                        "junk_ratio": (pre_classification or {}).get("junk_ratio", 0.0),
                    },
                )

            if not pipeline_config.allow_agpl_fallback:
                decision(
                    event="d3a_agpl_probe_gate",
                    choice="probe_skipped_agpl_disabled",
                    reason="ALLOW_AGPL_FALLBACK=false blocks fitz",
                    attrs={"allow_agpl_fallback": False},
                )
            else:
                try:
                    import fitz

                    with fitz.open(file_path) as probe_pdf:
                        state.pdf_page_count = (
                            probe_pdf.page_count if probe_pdf.page_count > 0 else None
                        )
                        # Zone-6 Step C: capture per-page landscape orientation
                        # alongside the existing D3a probe so table segmentation
                        # can use orientation-aware thresholds.  Reuses the same
                        # landscape heuristic as converters.py's
                        # _tag_landscape_pages_for_fallback (rotation % 180 != 0
                        # OR width > height) without importing the private fn.
                        _landscape_pages = []
                        for _pg_idx, _pg in enumerate(probe_pdf):
                            try:
                                _rot = _pg.rotation
                                _w = _pg.rect.width
                                _h = _pg.rect.height
                                _is_ls = (_rot % 180 != 0) or (_w > _h)
                            except Exception:
                                _is_ls = False
                            _landscape_pages.append({"page_no": _pg_idx, "is_landscape": _is_ls})
                        state.landscape_pages = _landscape_pages

                        if probe_pdf.page_count > 0:
                            raw_text = probe_pdf[0].get_text()
                            _probe_ctx = (
                                script_context
                                if script_context is not None
                                else ScriptContext(
                                    dominant_script=expected_script,
                                    had_presentation_forms=_infer_presentation_forms(raw_text),  # pre-NFKC: raw PDF text
                                    source="pre_garble_probe",
                                )
                            )
                            _p0_chars = len(raw_text.strip())
                            if raw_text.strip() and detect_garble(
                                raw_text,
                                script_context=_probe_ctx,
                                config=_garble_config,
                                blob_kind=BlobKind.RAW_MARKDOWN,
                            ):
                                state.pre_garbled = True
                                decision(
                                    event="d3a_pre_garble_probe",
                                    choice="pre_garbled_garble_detected",
                                    reason="raw text layer garbled",
                                    attrs={
                                        "page0_text_chars": _p0_chars,
                                        "page_count": probe_pdf.page_count,
                                        "d3a_sparse_page_char_floor": D3A_SPARSE_PAGE_CHAR_FLOOR,
                                        "error_type": None,
                                    },
                                )
                            elif (
                                probe_pdf.page_count >= 2 and _p0_chars < D3A_SPARSE_PAGE_CHAR_FLOOR
                            ):
                                state.pre_garbled = True
                                decision(
                                    event="d3a_pre_garble_probe",
                                    choice="pre_garbled_sparse_text",
                                    reason="page-0 text too sparse for multi-page PDF",
                                    attrs={
                                        "page0_text_chars": _p0_chars,
                                        "page_count": probe_pdf.page_count,
                                        "d3a_sparse_page_char_floor": D3A_SPARSE_PAGE_CHAR_FLOOR,
                                        "error_type": None,
                                    },
                                )
                            else:
                                decision(
                                    event="d3a_pre_garble_probe",
                                    choice="clean_not_pre_garbled",
                                    reason="text layer clean",
                                    attrs={
                                        "page0_text_chars": _p0_chars,
                                        "page_count": probe_pdf.page_count,
                                        "d3a_sparse_page_char_floor": D3A_SPARSE_PAGE_CHAR_FLOOR,
                                        "error_type": None,
                                    },
                                )
                except Exception as _probe_exc:
                    decision(
                        event="d3a_pre_garble_probe",
                        choice="probe_error",
                        reason="exception during fitz probe",
                        attrs={
                            "page0_text_chars": 0,
                            "page_count": 0,
                            "d3a_sparse_page_char_floor": D3A_SPARSE_PAGE_CHAR_FLOOR,
                            "error_type": type(_probe_exc).__name__,
                        },
                    )
                else:
                    decision(
                        event="d3a_agpl_probe_gate",
                        choice="probe_run",
                        reason="AGPL fallback enabled, probe completed",
                        attrs={"allow_agpl_fallback": True},
                    )

            PRE_GARBLE_FORCE_OCR_ENABLED = pipeline_config.pre_garble_force_ocr_enabled

            # Zone-2: force_full_page is a pre-conversion decision independent
            # of has_image_markers (which is unknown until the converter returns).
            # The PER_PICTURE decision is deferred to the converter chain
            # (_recover_picture_results) where has_image_markers reflects actual
            # content.
            #
            # AUTHORITY INVERSION (RFC-044 D5): this decision causes Docling to
            # run native full-page OCR BEFORE decide_ocr_strategy is consulted.
            # decide_ocr_strategy learns what already happened (via
            # full_page_already_applied) rather than deciding what should happen.
            # Inputs (pdf_classification, pre_garbled) should feed INTO
            # decide_ocr_strategy in a future RFC (Phase B consolidation).
            # See design-rfc044-recovery-dispatch-wiring.md D5 for phased plan.
            force_full_page = inspector_force_ocr or (
                state.pre_garbled and PRE_GARBLE_FORCE_OCR_ENABLED
            )
            if force_full_page:
                _ffp_choice = (
                    "forced_by_inspector" if inspector_force_ocr else "forced_by_pre_garble"
                )
            else:
                _ffp_choice = "not_forced"
            decision(
                event="force_full_page_ocr_decision",
                choice=_ffp_choice,
                reason="inspector or pre-garble triggered"
                if force_full_page
                else "no force trigger",
                attrs={
                    "inspector_force_ocr": inspector_force_ocr,
                    "pre_garbled": state.pre_garbled,
                    "pre_garble_force_ocr_enabled": PRE_GARBLE_FORCE_OCR_ENABLED,
                },
            )

            chain = [as_chain_entry(e) for e in pdf_markdown_converters()]
            primary_name = chain[0].name if chain else None
            state.used_converter = None
            state.extraction_stages_captured = []
            state.use_remote = bool(
                getattr(settings, "docling_service_url", None) and self._staging_key
            )
            decision(
                event="pdf_route_remote_or_local",
                choice="remote_docling" if state.use_remote else "local_converter_chain",
                reason="remote service configured"
                if state.use_remote
                else "no remote service or staging key",
                attrs={
                    "docling_service_url_set": bool(getattr(settings, "docling_service_url", None)),
                    "staging_key_set": bool(self._staging_key),
                },
            )
            _transient_attempts: int = 0  # Zone-7: per-converter transient retry counter
            for idx, entry in enumerate(chain):
                conv_name = entry.name
                conv_fn = entry.fn
                _conv_supports_ocr = entry.supports_ocr
                try:
                    logger.info("Extracting PDF to markdown via %s: %s", conv_name, filename)
                    if state.use_remote and _conv_supports_ocr:
                        _dispatch_mode = (
                            "remote_supports_ocr" if _conv_supports_ocr else "remote_plain"
                        )
                        decision(
                            event="pdf_converter_dispatch_mode",
                            choice=_dispatch_mode,
                            reason="remote converter selected",
                            attrs={
                                "converter_name": conv_name,
                                "supports_ocr": _conv_supports_ocr,
                                "force_full_page": force_full_page,
                            },
                        )
                        if force_full_page:
                            md_content, state.pic_results = await _remote_pdf_to_markdown(
                                self._staging_key,
                                force_full_page_ocr=True,
                                ocr_lang_override=_ocr_lang_override,
                                expected_script=expected_script,
                            )
                        else:
                            if state.pre_garbled:
                                logger.info(
                                    "D3a pre-garble probe fired for %s but OCR deferral "
                                    "active; deferring to Fix-3 retry path",
                                    filename,
                                )
                            md_content, state.pic_results = await _remote_pdf_to_markdown(
                                self._staging_key,
                                expected_script=expected_script,
                            )
                    elif force_full_page and _conv_supports_ocr:
                        decision(
                            event="pdf_converter_dispatch_mode",
                            choice="local_force_full_page",
                            reason="local converter with forced full-page OCR",
                            attrs={
                                "converter_name": conv_name,
                                "supports_ocr": _conv_supports_ocr,
                                "force_full_page": force_full_page,
                            },
                        )
                        md_content, state.pic_results, stages_out = _split_converter_output(
                            await asyncio.to_thread(
                                conv_fn,
                                file_path,
                                True,
                                ocr_lang_override=_ocr_lang_override,
                                expected_script=expected_script,
                            )
                        )
                        if stages_out:
                            state.extraction_stages_captured = stages_out
                    else:
                        decision(
                            event="pdf_converter_dispatch_mode",
                            choice="local_normal",
                            reason="local converter, normal mode",
                            attrs={
                                "converter_name": conv_name,
                                "supports_ocr": _conv_supports_ocr,
                                "force_full_page": force_full_page,
                            },
                        )
                        if state.pre_garbled and _conv_supports_ocr:
                            logger.info(
                                "D3a pre-garble probe fired for %s but OCR deferral "
                                "active; deferring to Fix-3 retry path",
                                filename,
                            )
                        md_content, state.pic_results, stages_out = _split_converter_output(
                            await asyncio.to_thread(
                                conv_fn,
                                file_path,
                                expected_script=expected_script,
                            )
                        )
                        if stages_out:
                            state.extraction_stages_captured = stages_out
                    state.used_converter = conv_name
                    state.supports_ocr = _conv_supports_ocr
                    break
                except Exception as conv_exc:
                    md_content = None
                    state.pic_results = []

                    # -------------------------------------------------------
                    # Failure-mode classification: transient vs. structural.
                    #
                    # Transient failures (network timeout, HTTP 5xx, connection
                    # reset) should NOT silently walk to an AGPL-licensed
                    # converter — an unplanned outage must not become AGPL-
                    # licensed network-served conversion (HR4).  Only structural
                    # failures (parse errors, import failures) justify walking
                    # the chain to the next converter.
                    # -------------------------------------------------------
                    _is_transient = _classify_transient_failure(conv_exc)

                    if idx == 0:
                        PDF_PRIMARY_CONVERTER_FAILURES.labels(
                            converter=conv_name, error=type(conv_exc).__name__
                        ).inc()
                        logger.error(
                            "PRIMARY PDF converter '%s' FAILED for %s (%s: %s); "
                            "failure classified as %s.",
                            conv_name,
                            filename,
                            type(conv_exc).__name__,
                            conv_exc,
                            "TRANSIENT" if _is_transient else "STRUCTURAL",
                            exc_info=True,
                        )
                    else:
                        logger.warning(
                            "%s failed for %s (%s); failure classified as %s",
                            conv_name,
                            filename,
                            conv_exc,
                            "TRANSIENT" if _is_transient else "STRUCTURAL",
                        )

                    # -------------------------------------------------------
                    # Determine the ConverterFailurePolicy for this failure.
                    # -------------------------------------------------------
                    next_idx = idx + 1
                    next_is_agpl = next_idx < len(chain) and chain[next_idx].is_agpl

                    # Ordering is load-bearing: the AGPL branches must be
                    # classified BEFORE the generic end-of-chain/WALK branches,
                    # or a structural failure into an AGPL converter would fall
                    # through to the bare `else` (its former, ungated behavior).
                    if _is_transient and _transient_attempts < CONVERTER_TRANSIENT_RETRY_COUNT:
                        _failure_policy = ConverterFailurePolicy.RETRY
                    elif _is_transient and next_is_agpl:
                        _failure_policy = ConverterFailurePolicy.BLOCK_AGPL
                    elif not _is_transient and next_is_agpl:
                        _failure_policy = ConverterFailurePolicy.GATE_AGPL_STRUCTURAL
                    elif next_idx >= len(chain):
                        _failure_policy = ConverterFailurePolicy.REJECT
                    else:
                        _failure_policy = ConverterFailurePolicy.WALK

                    _agpl_fallback_on = pipeline_config.agpl_structural_fallback_enabled
                    _cfp_choice_map = {
                        ConverterFailurePolicy.RETRY: "retry",
                        ConverterFailurePolicy.BLOCK_AGPL: "block_agpl",
                        ConverterFailurePolicy.REJECT: "reject",
                        ConverterFailurePolicy.WALK: "walk",
                        ConverterFailurePolicy.GATE_AGPL_STRUCTURAL: (
                            "gate_agpl_structural_blocked"
                            if not _agpl_fallback_on
                            else "gate_agpl_structural_walk"
                        ),
                    }
                    decision(
                        event="converter_failure_policy",
                        choice=_cfp_choice_map.get(_failure_policy, "walk"),
                        reason=f"{_failure_policy.value} for {type(conv_exc).__name__}",
                        attrs={
                            "converter_name": conv_name,
                            "exception_type": type(conv_exc).__name__,
                            "is_transient": _is_transient,
                            "transient_attempts": _transient_attempts,
                            "converter_transient_retry_count": CONVERTER_TRANSIENT_RETRY_COUNT,
                            "next_is_agpl": next_is_agpl,
                            "agpl_structural_fallback_enabled": _agpl_fallback_on,
                        },
                    )

                    if _failure_policy is ConverterFailurePolicy.RETRY:
                        # Retry the same converter: bump attempt counter,
                        # rewind idx so the for-loop re-enters this entry.
                        _transient_attempts += 1
                        logger.info(
                            "Retrying '%s' for %s (attempt %d/%d).",
                            conv_name,
                            filename,
                            _transient_attempts,
                            CONVERTER_TRANSIENT_RETRY_COUNT,
                        )
                        continue

                    if _failure_policy is ConverterFailurePolicy.BLOCK_AGPL:
                        AGPL_FALLBACK_TOTAL.labels(reason="transient_blocked").inc()
                        logger.warning(
                            "Transient failure on '%s' for %s would fall "
                            "through to AGPL converter '%s'; blocking "
                            "chain walk (HR4). Document will use legacy "
                            "page_index fallback instead.",
                            conv_name,
                            filename,
                            chain[next_idx].name,
                        )
                        break

                    if _failure_policy is ConverterFailurePolicy.REJECT:
                        logger.warning(
                            "No more converters after '%s' for %s; "
                            "falling through to legacy page_index.",
                            conv_name,
                            filename,
                        )
                        break

                    if _failure_policy is ConverterFailurePolicy.GATE_AGPL_STRUCTURAL:
                        # A structural failure whose next converter is AGPL.
                        # Previously an unnamed fall-through into WALK that only
                        # logged a warning, so the AGPL fallback was neither
                        # gateable nor counted.  Now it is both.
                        if not pipeline_config.agpl_structural_fallback_enabled:
                            AGPL_FALLBACK_TOTAL.labels(reason="structural_blocked").inc()
                            logger.warning(
                                "Structural failure on '%s' for %s would fall "
                                "through to AGPL converter '%s'; blocking chain "
                                "walk (AGPL_STRUCTURAL_FALLBACK_ENABLED=false). "
                                "Document will use legacy page_index fallback "
                                "instead.",
                                conv_name,
                                filename,
                                chain[next_idx].name,
                            )
                            break
                        AGPL_FALLBACK_TOTAL.labels(reason="structural_walk").inc()
                        logger.warning(
                            "Structural failure on '%s' for %s; falling "
                            "back to AGPL converter '%s'. To block this walk, "
                            "set AGPL_STRUCTURAL_FALLBACK_ENABLED=false "
                            "(or ALLOW_AGPL_FALLBACK=false to drop the AGPL "
                            "converter from the chain entirely).",
                            conv_name,
                            filename,
                            chain[next_idx].name,
                        )
                        # Same as WALK: reset the per-converter transient
                        # attempt counter before moving to the next entry.
                        _transient_attempts = 0
                        continue

                    # ConverterFailurePolicy.WALK — walk to next converter.
                    # Reset transient attempt counter for the next converter.
                    _transient_attempts = 0
                    if _is_transient:
                        logger.info(
                            "Transient failure on '%s'; next converter '%s' is "
                            "non-AGPL, allowing chain walk.",
                            conv_name,
                            chain[next_idx].name if next_idx < len(chain) else "<none>",
                        )
            if md_content is not None:
                _fallback_from_primary = (
                    primary_name is not None and state.used_converter != primary_name
                )
                decision(
                    event="pdf_conversion_outcome",
                    choice="converter_succeeded",
                    reason=f"extracted via {state.used_converter}",
                    attrs={
                        "used_converter": state.used_converter,
                        "primary_converter": primary_name,
                        "fallback_from_primary": _fallback_from_primary,
                        "fallback_converter_is_agpl": state.used_converter == "pymupdf4llm"
                        if _fallback_from_primary
                        else False,
                    },
                )
                # Zone-2: stamp full_page_already_applied when the initial
                # conversion itself used force_full_page OCR.  This prevents
                # downstream per-picture OCR from re-processing regions that
                # the full-page OCR already covered.
                if force_full_page:
                    state.full_page_already_applied = True
                if primary_name is not None and state.used_converter != primary_name:
                    logger.error(
                        "PDF %s extracted by FALLBACK converter '%s' because primary "
                        "'%s' failed; a flat 'depth<2' tree downstream is a CONVERTER "
                        "failure, not a low-quality source. Fix the primary converter.",
                        filename,
                        state.used_converter,
                        primary_name,
                    )
                    if state.used_converter == "pymupdf4llm":
                        AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()
                if state.pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                    _log_pic_splice_trace(filename, "primary", state.pic_results)
                    md_content = splice_picture_text_for_tree(md_content, state.pic_results)
                    decision(
                        event="pdf_picture_splice_or_strip",
                        choice="spliced_into_tree",
                        reason="picture OCR results spliced into tree markers",
                        attrs={"pic_result_count": len(state.pic_results), "splice_enabled": True},
                    )
                elif not state.pic_results and "<!-- image -->" in md_content:
                    md_content = strip_unresolved_image_markers(md_content)
                    decision(
                        event="pdf_picture_splice_or_strip",
                        choice="stripped_residual_markers",
                        reason="no pic results but residual image markers found",
                        attrs={
                            "pic_result_count": 0,
                            "splice_enabled": TREE_PATH_PICTURE_SPLICE_ENABLED,
                        },
                    )
                else:
                    decision(
                        event="pdf_picture_splice_or_strip",
                        choice="no_action",
                        reason="no splice or strip needed",
                        attrs={
                            "pic_result_count": len(state.pic_results) if state.pic_results else 0,
                            "splice_enabled": TREE_PATH_PICTURE_SPLICE_ENABLED,
                        },
                    )
                _do_renorm = state.use_remote and pipeline_config.remote_md_renormalize
                decision(
                    event="pdf_remote_bidi_renorm_gate",
                    choice="renorm_attempted" if _do_renorm else "skipped_local_or_disabled",
                    reason="remote route with renormalize enabled"
                    if _do_renorm
                    else "local route or renormalize disabled",
                    attrs={
                        "use_remote": state.use_remote,
                        "remote_md_renormalize": pipeline_config.remote_md_renormalize,
                    },
                )
                if _do_renorm:
                    md_content, state.rtl_decision = _renormalize_bidi_guarded(
                        md_content,
                        filename,
                    )
                    state.bidi_renorm_applied = True
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    state.tmp_md_path = md_tmp.name
                state.result = await self._run_md_to_tree(state.tmp_md_path)
            else:
                PDF_EXTRACT_FALLBACKS.inc()
                decision(
                    event="pdf_conversion_outcome",
                    choice="all_converters_failed_legacy_fallback",
                    reason="all markdown converters failed",
                    attrs={
                        "used_converter": None,
                        "primary_converter": primary_name,
                        "fallback_from_primary": True,
                        "fallback_converter_is_agpl": False,
                    },
                )
                state.result = await self._run_page_index_retrying(file_path)
            state.md_content = md_content

        elif ext in (".md", ".markdown", ".txt"):
            logger.info("Running md_to_tree on: %s", filename)
            state.result = await self._run_md_to_tree(file_path)

        elif ext in (".docx", ".pptx"):
            try:
                logger.info("Converting %s to PDF via LibreOffice", filename)
                pdf_path = await asyncio.to_thread(libreoffice_to_pdf, file_path)
                state.tmp_lo_dir = os.path.dirname(pdf_path)
                logger.info("Running page_index on converted PDF: %s", pdf_path)
                state.result = await self._run_page_index_retrying(pdf_path)
                decision(
                    event="docx_pptx_conversion_route",
                    choice="libreoffice_page_index_success",
                    reason="LibreOffice conversion succeeded",
                    attrs={"exception_type": None},
                )
            except Exception as lo_exc:
                decision(
                    event="docx_pptx_conversion_route",
                    choice="markdown_fallback",
                    reason="LibreOffice/page_index failed",
                    attrs={"exception_type": type(lo_exc).__name__},
                )
                if state.tmp_lo_dir:
                    shutil.rmtree(state.tmp_lo_dir, ignore_errors=True)
                    state.tmp_lo_dir = None
                converter = docx_to_markdown if ext == ".docx" else pptx_to_markdown
                md_content = await asyncio.to_thread(converter, file_path)
                state.md_content = md_content
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    state.tmp_md_path = md_tmp.name
                state.result = await self._run_md_to_tree(state.tmp_md_path)

        elif ext == ".xlsx":
            logger.info("Converting XLSX to markdown tables: %s", filename)
            md_content = await asyncio.to_thread(xlsx_to_markdown, file_path)
            state.md_content = md_content
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as md_tmp:
                md_tmp.write(md_content)
                state.tmp_md_path = md_tmp.name
            state.result = await self._run_md_to_tree(state.tmp_md_path)

        elif ext in _IMAGE_EXTS:
            logger.info("OCR image to markdown: %s", filename)
            detected = detect_ocr_langs(filename)
            _tessdata_degraded = False
            try:
                img_langs = await asyncio.to_thread(ensure_tessdata, detected)
            except TessdataUnavailableError:
                img_langs = ["deu", "eng"]
                _tessdata_degraded = True
            decision(
                event="standalone_image_tessdata_availability",
                choice="degraded_to_deu_eng" if _tessdata_degraded else "tessdata_available",
                reason="tessdata unavailable, degraded"
                if _tessdata_degraded
                else "tessdata available",
                attrs={
                    "detected_langs_count": len(detected),
                    "degraded_langs": img_langs if _tessdata_degraded else None,
                },
            )
            # RFC-046 D2: this path OCRs the image via tesseract (both
            # image_to_markdown and the _tesseract_ocr_image fallback below).
            # Found missing by the 2026-09-15 smoke test: Doc 13 ran OCR and
            # produced Latin gibberish, yet no engine reached the sidecar.
            state.ocr_engine = str(OcrEngine.TESSERACT)
            md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
            img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
            standalone_ocr_text = ""
            _md_chars = len("".join(md_content.split()))
            if _md_chars <= MIN_STANDALONE_IMAGE_MD_CHARS:
                standalone_ocr_text = await asyncio.to_thread(
                    _tesseract_ocr_image, file_path, img_langs
                )
                decision(
                    event="standalone_image_ocr_source_choice",
                    choice="full_tesseract_retry",
                    reason="image_to_markdown output too sparse",
                    attrs={
                        "md_content_chars": _md_chars,
                        "min_standalone_image_md_chars": MIN_STANDALONE_IMAGE_MD_CHARS,
                    },
                )
            else:
                standalone_ocr_text = md_content
                decision(
                    event="standalone_image_ocr_source_choice",
                    choice="reuse_image_to_markdown_text",
                    reason="image_to_markdown output sufficient",
                    attrs={
                        "md_content_chars": _md_chars,
                        "min_standalone_image_md_chars": MIN_STANDALONE_IMAGE_MD_CHARS,
                    },
                )
            # D4 (RFC-046 task 6.2): bounded detect-correct-retry.
            # Compare content-derived langs against filename-derived; if they
            # differ, re-OCR once with corrected langs and arbitrate.
            content_langs = detect_ocr_langs(standalone_ocr_text) if standalone_ocr_text else img_langs
            if sorted(content_langs) != sorted(img_langs):
                _corrective_degraded = False
                try:
                    corrective_langs = await asyncio.to_thread(ensure_tessdata, content_langs)
                except TessdataUnavailableError:
                    corrective_langs = img_langs
                    _corrective_degraded = True
                if not _corrective_degraded and sorted(corrective_langs) != sorted(img_langs):
                    corrective_md = await asyncio.to_thread(
                        image_to_markdown, file_path, corrective_langs
                    )
                    _corr_chars = len("".join(corrective_md.split()))
                    corrective_ocr_text = corrective_md
                    if _corr_chars <= MIN_STANDALONE_IMAGE_MD_CHARS:
                        corrective_ocr_text = await asyncio.to_thread(
                            _tesseract_ocr_image, file_path, corrective_langs
                        )
                    original_garbled = bool(detect_garble(
                        standalone_ocr_text,
                        script_context=script_context,
                        config=_garble_config,
                        blob_kind=BlobKind.TREE_TEXT,
                    )) if standalone_ocr_text else True
                    corrective_garbled = bool(detect_garble(
                        corrective_ocr_text,
                        script_context=script_context,
                        config=_garble_config,
                        blob_kind=BlobKind.TREE_TEXT,
                    )) if corrective_ocr_text else True
                    candidates = [
                        Candidate(
                            label="filename_derived",
                            text=standalone_ocr_text,
                            char_count=len(standalone_ocr_text),
                            garbled=original_garbled,
                            engine=str(OcrEngine.TESSERACT),
                        ),
                        Candidate(
                            label="corrective_retry",
                            text=corrective_ocr_text,
                            char_count=len(corrective_ocr_text),
                            garbled=corrective_garbled,
                            engine=str(OcrEngine.TESSERACT),
                        ),
                    ]
                    winner_idx = arbitrate(candidates)
                    if winner_idx == 1:
                        md_content = corrective_md
                        standalone_ocr_text = corrective_ocr_text
                        img_langs = corrective_langs
                    decision(
                        event="d4_corrective_retry",
                        choice=candidates[winner_idx].label,
                        reason="content-derived langs differ from filename",
                        attrs={
                            "source_langs": detected,
                            "content_langs": content_langs,
                            "corrective_langs": corrective_langs,
                            "winner_index": winner_idx,
                            "original_garbled": original_garbled,
                            "corrective_garbled": corrective_garbled,
                        },
                    )
                else:
                    decision(
                        event="d4_corrective_retry",
                        choice="skip_tessdata_unavailable" if _corrective_degraded else "skip_same_langs",
                        reason="corrective langs same as original or tessdata unavailable",
                        attrs={
                            "source_langs": detected,
                            "content_langs": content_langs,
                        },
                    )
            else:
                decision(
                    event="d4_corrective_retry",
                    choice="skip_langs_match",
                    reason="content-derived langs match filename-derived",
                    attrs={"langs": img_langs},
                )

            md_content = re.sub(r"(<!-- image -->)\s*(?=<!-- image -->)", "", md_content)
            marker_count = md_content.count("<!-- image -->")
            state.pic_results = [
                PictureResult(
                    ocr_text=standalone_ocr_text,
                    page=1,
                    bbox={"l": 0, "t": 0, "r": 0, "b": 0},
                    png_bytes=img_bytes,
                )
                for _ in range(max(1, marker_count))
            ]
            state.md_content = md_content
            # Zone-8: splice picture OCR text into tree markers for standalone
            # images, mirroring the PDF path (lines 589-591).
            if state.pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                _log_pic_splice_trace(filename, "standalone_image", state.pic_results)
                md_content = splice_picture_text_for_tree(md_content, state.pic_results)
                state.md_content = md_content
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as md_tmp:
                md_tmp.write(md_content)
                state.tmp_md_path = md_tmp.name
            state.result = await self._run_md_to_tree(state.tmp_md_path)

        else:  # .html
            logger.info("Converting HTML to markdown: %s", filename)
            md_content = await html_to_markdown_with_images(file_path, self.model)
            state.md_content = md_content
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as md_tmp:
                md_tmp.write(md_content)
                state.tmp_md_path = md_tmp.name
            state.result = await self._run_md_to_tree(state.tmp_md_path)

        # Post-conversion: split, segment, validate
        state.result["structure"] = prepare_tree(
            state.result.get("structure", []),
            orientation=_dominant_orientation(state.landscape_pages),
        )
        _image_garble_cfg = None
        if ext in _IMAGE_EXTS:
            from ..helpers.garble import GarbleConfig

            _image_garble_cfg = GarbleConfig(
                garble_nonsense_ratio=IMAGE_OCR_NONSENSE_RATIO,
            )
        _vt_raw = validate_tree(
            state.result.get("structure", []),
            expected_script=script_context if script_context is not None else expected_script,
            page_count=state.pdf_page_count if ext == ".pdf" else None,
            rtl_decision=state.rtl_decision,
            garble_config=_image_garble_cfg,
        )
        finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing)
        if state.gate_result and state.gate_result.all_defects:
            logger.info(
                "validate_tree %s: primary=%s, all_defects=%s",
                filename,
                state.gate_result.defect.value,
                sorted(d.value for d in state.gate_result.all_defects),
            )
        state.total_chars = len(_flatten_tree_text(state.result.get("structure", [])))

    async def _persist_flat_result(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        sha256: str,
        file_bytes: bytes,
        pdf_classification: dict | None,
        _effective_cfg: dict,
        _effective_config_at_job_start: dict | None,
        *,
        script_context: ScriptContext | None = None,
    ) -> str | None:
        """Persist a flat-routed document.

        Returns doc_id on success, None if unavailable/garbled.
        """
        from ..helpers.garble import GarbleConfig

        _image_garble_cfg: GarbleConfig | None = (
            GarbleConfig(garble_nonsense_ratio=IMAGE_OCR_NONSENSE_RATIO)
            if ext in _IMAGE_EXTS
            else None
        )
        flat_md = state.md_content
        if flat_md is None and state.tmp_md_path is not None:
            flat_md = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                state.tmp_md_path,
            )
            _flat_md_src = "from_tmp_md_path"
        elif flat_md is None and ext in (".md", ".markdown", ".txt"):
            flat_md = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                file_path,
            )
            _flat_md_src = "from_file_direct_read"
        elif flat_md is not None:
            _flat_md_src = "from_state_md_content"
        else:
            _flat_md_src = "unavailable"

        state.flat_garble_unrecovered = False
        decision(
            event="flat_md_source_resolution",
            choice=_flat_md_src,
            reason=f"flat markdown resolved via {_flat_md_src}",
            attrs={"md_chars": len(flat_md) if flat_md else 0},
        )
        if flat_md is None:
            return None

        _log_pic_splice_trace(filename, "flat_figure_markers", state.pic_results)
        flat_md = splice_figure_markers(flat_md, state.pic_results)

        state.flat_garble_unrecovered = False
        # Zone-1: decompose into blocks BEFORE garble gate so the check
        # runs per-block, eliminating dilution where a single garbled
        # table amid clean prose passes the whole-blob threshold.
        _garble_blocks: list[dict]
        _, _garble_blocks = await asyncio.to_thread(route_and_extract_flat, flat_md)
        _flat_garble_ctx = (
            script_context
            if script_context is not None
            else ScriptContext(
                dominant_script=expected_script,
                had_presentation_forms=_infer_presentation_forms(flat_md),  # pre-NFKC: post-normalize but safe — returns False on destroyed PF
                source="flat_garble_gate",
            )
        )
        _flat_garble_report = _garble_check_flat_blocks(
            _garble_blocks,
            script_context=_flat_garble_ctx,
            config=_image_garble_cfg if _image_garble_cfg is not None else _garble_config,
        )
        if _flat_garble_report:
            state.flat_garble_unrecovered = True
            from ..helpers.types import _guard_bypass

            _guard_bypass.active = True
            try:
                state.reason = "garbling"
            finally:
                _guard_bypass.active = False
            decision(
                event="flat_block_garble_gate",
                choice="garbled_reject",
                reason="per-block garble gate triggered",
                attrs={
                    "fired_prongs": list(_flat_garble_report.fired_prongs)
                    if _flat_garble_report.fired_prongs
                    else [],
                    "fired_prongs_count": len(_flat_garble_report.fired_prongs)
                    if _flat_garble_report.fired_prongs
                    else 0,
                },
            )
            if ext == ".pdf" and settings.vlm_fallback:
                try:
                    from ..converters import vlm_extract_markdown

                    vlm_md = await vlm_extract_markdown(file_path, settings.vlm_model)
                    _vlm_ctx = (
                        script_context
                        if script_context is not None
                        else ScriptContext(
                            dominant_script=expected_script,
                            had_presentation_forms=_infer_presentation_forms(vlm_md),  # pre-NFKC: raw VLM output
                            source="vlm_fallback_garble",
                        )
                    )
                    _, _vlm_blocks = await asyncio.to_thread(route_and_extract_flat, vlm_md)
                    if not _garble_check_flat_blocks(
                        _vlm_blocks,
                        script_context=_vlm_ctx,
                        config=_image_garble_cfg if _image_garble_cfg is not None else _garble_config,
                    ):
                        flat_md = vlm_md
                        state.pic_results = []
                        state.flat_garble_unrecovered = False
                        VLM_FALLBACK_TOTAL.labels(result="recovered").inc()
                        decision(
                            event="flat_vlm_fallback_outcome",
                            choice="vlm_recovered",
                            reason="VLM extraction passed garble check",
                            attrs={"exception_type": None},
                        )
                    else:
                        VLM_FALLBACK_TOTAL.labels(result="still_garbled").inc()
                        decision(
                            event="flat_vlm_fallback_outcome",
                            choice="vlm_still_garbled",
                            reason="VLM extraction still garbled",
                            attrs={"exception_type": None},
                        )
                except ZDRComplianceError as vlm_zdr_exc:
                    VLM_FALLBACK_TOTAL.labels(result="compliance_blocked").inc()
                    HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm").inc()
                    logger.info(
                        "VLM fallback skipped for %s: HR3 compliance block (%s)",
                        filename,
                        vlm_zdr_exc,
                    )
                    decision(
                        event="flat_vlm_fallback_outcome",
                        choice="vlm_compliance_blocked",
                        reason="HR3 compliance block",
                        attrs={"exception_type": "ZDRComplianceError"},
                    )
                except Exception as vlm_exc:
                    VLM_FALLBACK_TOTAL.labels(result="error").inc()
                    logger.error(
                        "VLM fallback failed for %s (%s)",
                        filename,
                        vlm_exc,
                        exc_info=True,
                    )
                    decision(
                        event="flat_vlm_fallback_outcome",
                        choice="vlm_error",
                        reason="VLM fallback failed",
                        attrs={"exception_type": type(vlm_exc).__name__},
                    )
            else:
                decision(
                    event="flat_vlm_fallback_outcome",
                    choice="not_attempted",
                    reason="VLM fallback not applicable",
                    attrs={"exception_type": None},
                )
        else:
            decision(
                event="flat_block_garble_gate",
                choice="not_garbled_pass",
                reason="per-block garble gate clear",
                attrs={"fired_prongs": [], "fired_prongs_count": 0},
            )
        if state.flat_garble_unrecovered:
            return None

        doc_id, content_class, blocks, image_enrichment_ratio = await _apply_picture_enrichment(
            flat_md,
            state.pic_results,
            ext,
            filename,
            splice_markers=False,
        )

        _enriched_image_blocks = [
            b for b in blocks
            if b.get("role") == "image" and b.get("ocr_text")
        ]
        if _enriched_image_blocks:
            _pe_script_ctx = (
                script_context
                if script_context is not None
                else ScriptContext(
                    dominant_script=expected_script,
                    had_presentation_forms=_infer_presentation_forms(flat_md),  # pre-NFKC
                    source="post_enrichment_garble",
                )
            )
            _pe_cfg = _image_garble_cfg if _image_garble_cfg is not None else _garble_config
            _pe_stripped = 0
            _pe_all_prongs: set[str] = set()
            for _pe_blk in _enriched_image_blocks:
                _pe_report = detect_garble(
                    _pe_blk.get("ocr_text", ""),
                    script_context=_pe_script_ctx,
                    config=_pe_cfg,
                    blob_kind=BlobKind.TREE_TEXT,
                )
                if _pe_report:
                    _pe_blk["ocr_text"] = ""
                    _pe_stripped += 1
                    _pe_all_prongs.update(_pe_report.fired_prongs or ())
            _pe_retained = len(_enriched_image_blocks) - _pe_stripped
            if _pe_stripped:
                decision(
                    event="post_enrichment_garble_check",
                    choice="blocks_stripped",
                    reason=f"cleared ocr_text on {_pe_stripped} garbled enriched image block(s)",
                    attrs={
                        "checked_count": len(_enriched_image_blocks),
                        "stripped_count": _pe_stripped,
                        "retained_count": _pe_retained,
                        "fired_prongs": sorted(_pe_all_prongs),
                    },
                )
            else:
                decision(
                    event="post_enrichment_garble_check",
                    choice="enriched_blocks_clean",
                    reason="image blocks mutated by enrichment passed garble check",
                    attrs={
                        "checked_count": len(_enriched_image_blocks),
                        "stripped_count": 0,
                        "retained_count": len(_enriched_image_blocks),
                    },
                )

        with bind_log_context(doc_id=doc_id):
            logger.info(
                "Routing %s to flat success path: reason=%s content_class=%s",
                filename,
                state.reason,
                content_class,
            )

            protocol = "https" if settings.minio_secure else "http"
            source_url = (
                f"{protocol}://{settings.minio_endpoint}"
                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
            )
            processed_at = datetime.now(UTC).isoformat()

            flat_structure = state.result.get("structure", [])
            if blocks:
                flat_structure = [
                    {"title": "", "text": _flat_block_primary_text(b)}
                    for b in blocks
                    if _flat_block_primary_text(b).strip()
                ]

            _flat_script = script_context if script_context is not None else expected_script
            _flat_th = VerdictThresholds.from_config(pipeline_config)
            _flat_sig = TreeSignals.from_tree(
                flat_structure,
                expected_script=_flat_script,
                garble_threshold=_flat_th.garble_threshold,
            )
            _vr = compute_verdict(
                flat_structure,
                content_class,
                state.gate_result,
                image_enrichment_ratio=image_enrichment_ratio,
                expected_script=_flat_script,
                flat_signals=_flat_sig,
            )
            f_verdict, f_verdict_reason = _vr.verdict, _vr.reason
            f_promotion_paths = list(_vr.promotion_paths_matched)

            _, _, f_mlr = _tree_max_leaf_ratio(flat_structure)

            flat_desc = await asyncio.to_thread(
                _generate_flat_doc_description,
                flat_md,
                doc_id=doc_id,
            )

            flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)

            # Zone-4.7: pre-aggregate row_records from table blocks so
            # flat_doc_view can read them directly instead of re-deriving
            # on every get_document / get_document_structure call.
            _row_records: list[str] = []
            for _blk in blocks:
                if _blk.get("role") == "table":
                    _row_records.extend(_blk.get("row_records", []) or [])

            _flat_verdict_computed_at = datetime.now(UTC).isoformat()

            # Zone-5: verdict fields stripped from flat artifact body; sidecar
            # (.meta.json via save_doc_meta) is the sole authoritative verdict
            # store.  save_flat_doc no longer touches the sidecar (RFC-042 D3);
            # the separate save_doc_meta call below is this child subprocess's
            # only sidecar write, merging verdict fields in directly.
            flat_meta = {
                "doc_id": doc_id,
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
                "sha256": sha256,
                "content_class": content_class,
                "blocks": blocks,
                "row_records": _row_records,
                "doc_description": flat_desc,
                "flat_char_count": flat_char_count,
                "build_sha": CLIENT_BUILD_SHA,
                "effective_config": _effective_cfg,
            }
            if _effective_config_at_job_start is not None:
                flat_meta["effective_config_at_job_start"] = _effective_config_at_job_start
            # RFC-046 D2: attribution. Without these, a verdict cannot be tied to
            # the engine that produced its text or the prong that condemned it,
            # which is what makes corpus diffs unexplainable.
            if state.ocr_engine:
                flat_meta["ocr_engine"] = state.ocr_engine
            # RFC-047 D2 (post-gate-FAIL) + HR5: source garble prongs from
            # the flat garble report (computed on flat blocks), not from the
            # tree's gate_result.signals.  Always persist — even below the
            # condemnation threshold — so sub-threshold garble is never silent.
            if _flat_garble_report is not None and _flat_garble_report.fired_prongs:
                flat_meta["garble_prongs"] = sorted(_flat_garble_report.fired_prongs)
                flat_meta["garble_char_ratio"] = round(_flat_garble_report.garble_ratio, 6)
            elif state.gate_result is not None and state.gate_result.signals is not None:
                _prongs = getattr(state.gate_result.signals, "garble_prongs", frozenset())
                if _prongs:
                    flat_meta["garble_prongs"] = sorted(_prongs)
            await asyncio.to_thread(save_flat_doc, doc_id, flat_meta)
            FLAT_DOCS_TOTAL.labels(content_class=content_class).inc()

            # Zone-5: verdict written exclusively via sidecar (authoritative path).
            await asyncio.to_thread(
                save_doc_meta,
                doc_id,
                {
                    "verdict": f_verdict,
                    "verdict_reason": f_verdict_reason,
                    "max_leaf_ratio": round(f_mlr, 4),
                    "pipeline_version": CURRENT_PIPELINE_VERSION,
                    "verdict_computed_at": _flat_verdict_computed_at,
                    **({"promotion_paths_matched": f_promotion_paths} if f_promotion_paths else {}),
                },
            )

            try:
                await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
            except Exception:
                RAW_UPLOAD_FAILURES.inc()
                logger.exception(
                    "save_raw failed after save_flat_doc succeeded for doc_id=%s",
                    doc_id,
                )

            await asyncio.to_thread(hash_cache_set, filename, sha256)

            logger.info(
                "Indexed flat doc %s → doc_id=%s (content_class=%s, %d blocks)",
                filename,
                doc_id,
                content_class,
                len(blocks),
            )
            self.last_content_class = content_class
            # Zone-7: stash verdict fields so converters_cli can surface them
            # in stdout JSON for the worker parent's _upsert_registry_row call.
            self.last_verdict_fields = {
                "verdict": f_verdict,
                "verdict_reason": f_verdict_reason,
                "pipeline_version": CURRENT_PIPELINE_VERSION,
                "max_leaf_ratio": round(f_mlr, 4),
                "verdict_computed_at": _flat_verdict_computed_at,
                **({"promotion_paths_matched": f_promotion_paths} if f_promotion_paths else {}),
            }
            # Zone-7 (dual-write consistency): stash registry fields for flat
            # docs so converters_cli can surface them in stdout JSON, eliminating
            # the MinIO re-read in _upsert_registry_row.  Keys mirror
            # _REGISTRY_FIELDS (verdict.py) plus content_class and node_count.
            self.last_registry_fields = {
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
                "sha256": sha256,
                "content_class": content_class,
                "doc_description": flat_desc,
                "product": "",
                "tier": "",
                "doc_family": "",
                "effective_date": "",
                "node_count": 0,
            }
            decision(
                event="verdict_downgrade_override_flat",
                choice="force_verdict_override_enabled"
                if VERDICT_DOWNGRADE_ENABLED
                else "normal_cas",
                reason="verdict downgrade override"
                if VERDICT_DOWNGRADE_ENABLED
                else "normal CAS verdict",
                attrs={
                    "verdict_downgrade_enabled": VERDICT_DOWNGRADE_ENABLED,
                    "pipeline_version": CURRENT_PIPELINE_VERSION,
                },
            )
            if VERDICT_DOWNGRADE_ENABLED:
                self.last_verdict_fields["force_verdict_override"] = True
            return doc_id

    async def _persist_tree_result(
        self,
        state: ExtractionState,
        filename: str,
        ext: str,
        expected_script: str | None,
        sha256: str,
        file_bytes: bytes,
        pdf_classification: dict | None,
        _effective_cfg: dict,
        _effective_config_at_job_start: dict | None,
        *,
        script_context: ScriptContext | None = None,
    ) -> str:
        """Persist a tree-routed document. Returns doc_id."""
        doc_id = str(uuid.uuid4())

        with bind_log_context(doc_id=doc_id):
            protocol = "https" if settings.minio_secure else "http"
            source_url = (
                f"{protocol}://{settings.minio_endpoint}"
                f"/{settings.minio_bucket}/uploads/{doc_id}/{filename}"
            )

            processed_at = datetime.now(UTC).isoformat()
            structure = state.result.get("structure", [])

            _vr = compute_verdict(
                structure,
                "",
                state.gate_result,
                inspector_class=(
                    pdf_classification.get("pdf_type") if pdf_classification else None
                ),
                expected_script=script_context if script_context is not None else expected_script,
            )
            verdict, verdict_reason = _vr.verdict, _vr.reason
            promotion_paths = list(_vr.promotion_paths_matched)

            _, _, mlr = _tree_max_leaf_ratio(structure)
            _verdict_computed_at = datetime.now(UTC).isoformat()

            # Zone-5: verdict fields stripped from artifact body; sidecar
            # (.meta.json via save_doc_meta) is the sole authoritative verdict
            # store.  read_registry_fields falls back to sidecar for new
            # artifacts that lack verdict in the JSON body.
            await asyncio.to_thread(
                save_doc,
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": filename,
                    "source_url": source_url,
                    "processed_at": processed_at,
                    "sha256": sha256,
                    "doc_description": state.result.get("doc_description", ""),
                    "structure": structure,
                },
            )

            # Zone-5: single save_doc_meta call carries both verdict and
            # non-verdict metadata -- no separate write_verdict path.
            meta = {
                "doc_id": doc_id,
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
                "sha256": sha256,
                "doc_description": state.result.get("doc_description", ""),
                "total_tree_chars": len(_flatten_tree_text(structure)),
                "build_sha": CLIENT_BUILD_SHA,
                "effective_config": _effective_cfg,
                "decider_version": "zone3_decide_rtl_v1",
                # Verdict fields -- authoritative via sidecar (Zone-5)
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "max_leaf_ratio": round(mlr, 4),
                "pipeline_version": CURRENT_PIPELINE_VERSION,
                "verdict_computed_at": _verdict_computed_at,
            }
            if promotion_paths:
                meta["promotion_paths_matched"] = promotion_paths
            if state.gate_result is not None and state.gate_result.all_defects:
                meta["all_defects"] = sorted(d.value for d in state.gate_result.all_defects)
            # RFC-046 D2: attribution. Without these, a verdict cannot be tied to
            # the engine that produced its text or the prong that condemned it,
            # which is what makes corpus diffs unexplainable.
            if state.ocr_engine:
                meta["ocr_engine"] = state.ocr_engine
            if state.gate_result is not None and state.gate_result.signals is not None:
                _prongs = getattr(state.gate_result.signals, "garble_prongs", frozenset())
                if _prongs:
                    meta["garble_prongs"] = sorted(_prongs)
            if _effective_config_at_job_start is not None:
                meta["effective_config_at_job_start"] = _effective_config_at_job_start
            if ext == ".pdf":
                _route_remote = bool(
                    state.use_remote and state.used_converter and state.supports_ocr
                )
                meta["extraction_route"] = "remote" if _route_remote else "local"
                if state.used_converter:
                    meta["converter_name"] = state.used_converter
                    contract = _converter_contract(state.used_converter)
                    if contract is not None:
                        meta["converter_contract"] = contract
                if state.pdf_page_count is not None:
                    meta["page_count"] = state.pdf_page_count
                if pdf_classification and pipeline_config.pdf_inspector_preclassify:
                    meta["inspector_class"] = pdf_classification.get("pdf_type")
                if state.extraction_stages_captured:
                    meta["extraction_stages"] = state.extraction_stages_captured
                if _route_remote and _remote_mod._remote_docling_version:
                    meta["remote_build_sha"] = _remote_mod._remote_docling_version.get(
                        "commit_sha", "unknown"
                    )
            await asyncio.to_thread(save_doc_meta, doc_id, meta)

            try:
                await asyncio.to_thread(save_raw, doc_id, filename, file_bytes)
            except Exception:
                RAW_UPLOAD_FAILURES.inc()
                logger.exception("save_raw failed after save_doc succeeded for doc_id=%s", doc_id)

            await asyncio.to_thread(hash_cache_set, filename, sha256)

            logger.info(
                "Indexed %s → doc_id=%s (%d sections)",
                filename,
                doc_id,
                len(state.result.get("structure", [])),
            )
            # Zone-7: stash verdict fields so converters_cli can surface them
            # in stdout JSON for the worker parent's _upsert_registry_row call.
            self.last_verdict_fields = {
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "pipeline_version": CURRENT_PIPELINE_VERSION,
                "max_leaf_ratio": round(mlr, 4),
                "verdict_computed_at": _verdict_computed_at,
                **({"promotion_paths_matched": promotion_paths} if promotion_paths else {}),
            }
            # Zone-7 (dual-write consistency): stash registry fields so
            # converters_cli can surface them in stdout JSON, eliminating the
            # MinIO re-read in _upsert_registry_row.  Keys mirror
            # _REGISTRY_FIELDS (verdict.py) plus node_count.
            self.last_registry_fields = {
                "doc_name": filename,
                "source_url": source_url,
                "processed_at": processed_at,
                "sha256": sha256,
                "doc_description": state.result.get("doc_description", ""),
                "product": "",
                "tier": "",
                "doc_family": "",
                "effective_date": "",
                "node_count": _tree_node_count(structure),
            }
            decision(
                event="verdict_downgrade_override_tree",
                choice="force_verdict_override_enabled"
                if VERDICT_DOWNGRADE_ENABLED
                else "normal_cas",
                reason="verdict downgrade override"
                if VERDICT_DOWNGRADE_ENABLED
                else "normal CAS verdict",
                attrs={
                    "verdict_downgrade_enabled": VERDICT_DOWNGRADE_ENABLED,
                    "pipeline_version": CURRENT_PIPELINE_VERSION,
                },
            )
            if VERDICT_DOWNGRADE_ENABLED:
                self.last_verdict_fields["force_verdict_override"] = True
            return doc_id

    # ------------------------------------------------------------------
    # Indexing — orchestrator
    # ------------------------------------------------------------------

    async def index(
        self,
        file_path: str,
        mode: str = "auto",
        pdf_classification: dict | None = None,
        pre_classification: dict | None = None,
        job_start_config: dict | None = None,
    ) -> str:
        """Index a document and persist it to MinIO. Returns the 8-char doc_id.

        Skips reprocessing if the file content is unchanged (SHA-256 dedup).
        Supported extensions: .pdf, .md, .markdown, .txt, .docx, .pptx, .html
        """
        self.last_content_class = None
        self.last_verdict_fields = None

        from ..config import effective_config_snapshot

        _effective_cfg = effective_config_snapshot()
        _effective_config_at_job_start = _detect_config_drift(job_start_config, _effective_cfg)
        if _effective_config_at_job_start is not None:
            logger.warning(
                "Config drift: job_start_config != effective_config at job execution time for %s",
                file_path,
            )

        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        filename = os.path.basename(file_path)
        ext = Path(filename).suffix.lower()
        logger.info("Indexing file: %s (ext=%s)", filename, ext)

        # Zone-3: compute ScriptContext once per index entry, thread through
        # all garble/gate call sites.  When pre_classification carried a
        # text-layer sample (RFC-046 D4), use it so Arabic-content PDFs with
        # Latin filenames get correct expected_script from the start — before
        # this, raw_text was unavailable until the fitz probe inside
        # _convert_to_tree, so filename-only inference was all we had.
        _pre_text_sample = (
            (pre_classification or {}).get("text_sample", "") or ""
        )
        script_context = ScriptContext.from_document(filename, raw_text=_pre_text_sample)
        expected_script = script_context.dominant_script

        if ext not in _SUPPORTED:
            raise ValueError(
                f"Unsupported format '{ext}'. Supported: {', '.join(sorted(_SUPPORTED))}"
            )

        file_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        logger.debug("File %s: size=%d bytes, sha256=%s", filename, len(file_bytes), sha256[:12])

        cached_sha256 = await asyncio.to_thread(hash_cache_get, filename)
        if cached_sha256 == sha256:
            docs = await asyncio.to_thread(list_processed_docs)
            for d in docs:
                if d.get("doc_name") == filename:
                    decision(
                        event="hash_cache_dedup_skip",
                        choice="skip_unchanged_reuse_doc_id",
                        reason="SHA-256 match, reusing existing doc_id",
                        attrs={"sha256_matched": True, "existing_doc_found": True},
                    )
                    self.last_content_class = d.get("content_class") or None
                    return d["doc_id"]
            decision(
                event="hash_cache_dedup_skip",
                choice="reprocess",
                reason="SHA-256 match but no existing doc found",
                attrs={"sha256_matched": True, "existing_doc_found": False},
            )
        else:
            decision(
                event="hash_cache_dedup_skip",
                choice="reprocess",
                reason="SHA-256 mismatch or no cache entry",
                attrs={"sha256_matched": False, "existing_doc_found": False},
            )

        state = ExtractionState(
            result={},
            ok=False,
            reason="",
            gate_result=None,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.REJECT,
            md_content=None,
            tmp_md_path=None,
            pic_results=[],
            used_converter=None,
            total_chars=0,
            extraction_stages_captured=[],
        )

        with bind_log_context(doc_sha8=sha256[:8]):
            try:
                await self._convert_to_tree(
                    state,
                    file_path,
                    filename,
                    ext,
                    expected_script,
                    pdf_classification,
                    pre_classification=pre_classification,
                    script_context=script_context,
                )

                # Zone-3: enrich ScriptContext with post-conversion content text.
                # _convert_to_tree populates state.md_content from the fitz probe /
                # Docling output.  Re-derive ScriptContext using actual content so
                # Arabic PDFs with Latin filenames get correct expected_script for
                # the recovery loop and flat-prefer guard.  Only re-derive when
                # md_content is available; preserve existing expected_script when
                # content inference returns None (no change for Latin docs).
                if state.md_content:
                    script_context = ScriptContext.from_document(
                        filename, raw_text=state.md_content
                    )
                    _pf_pre = (
                        getattr(state.rtl_decision, "had_presentation_forms", False)
                        if state.rtl_decision
                        else False
                    )
                    _pf_post = script_context.had_presentation_forms
                    _needs_carryover = _pf_pre and not _pf_post
                    if _needs_carryover:
                        script_context = dataclasses.replace(
                            script_context, had_presentation_forms=True
                        )
                    decision(
                        event="script_context_pf_carryover",
                        choice="pf_signal_carried_over"
                        if _needs_carryover
                        else "no_carryover_needed",
                        reason="NFKC destroyed PF codepoints, recovering from rtl_decision"
                        if _needs_carryover
                        else "PF signal consistent or absent",
                        attrs={
                            "had_presentation_forms_pre_nfkc": _pf_pre,
                            "had_presentation_forms_post_nfkc": _pf_post,
                        },
                    )
                    expected_script = script_context.dominant_script

                # Zone-1: GateSpec-driven recovery loop (single source of truth).
                # Each GateSpec with non-empty recovery_fns declares its own
                # recovery_eligible predicate and recovery method names.
                # Iteration follows GATES severity order; dedup by method name
                # across ALL gate tuples prevents repeated firing when multiple
                # GateSpecs share a recovery method (e.g. NODE_COUNT_LOW and
                # DEPTH_LOW both carry _recover_image_dominant_ocr).
                _fired_methods: set[str] = set()
                for _gate in GATES:
                    if not _gate.recovery_fns:
                        continue
                    if _gate.recovery_eligible is None or not _gate.recovery_eligible(state):
                        continue
                    for _fn_name in _gate.recovery_fns:
                        if _fn_name in _fired_methods:
                            continue
                        _fired_methods.add(_fn_name)
                        _method_label = _fn_name.removeprefix("_")
                        decision(
                            event="recovery_method_dispatch",
                            choice=_method_label,
                            reason=f"gate {_gate.defect.value} eligible, dispatching {_fn_name}",
                            attrs={"gate_defect": _gate.defect.value, "already_fired_skip": False},
                        )
                        await getattr(self, _fn_name)(
                            state,
                            file_path,
                            filename,
                            ext,
                            expected_script,
                            script_context=script_context,
                        )
                    # Zone-3: finalize_gate_and_route() is now called inside
                    # each recovery method (_reconvert_and_revalidate,
                    # _recover_rtl_repair) so gate_result/ok/reason/first_defect/
                    # route are always consistent after every recovery step.
                    # Recovery methods that intentionally override state.route
                    # (e.g. _recover_rtl_flat_compare, _recover_vlm_fallback)
                    # do so AFTER finalize_gate_and_route, which is correct.
                    state.total_chars = len(_flatten_tree_text(state.result.get("structure", [])))

                if not _fired_methods:
                    decision(
                        event="recovery_method_dispatch",
                        choice="no_gate_eligible",
                        reason="no gate eligible for recovery",
                        attrs={"gate_defect": None, "already_fired_skip": False},
                    )

                # Quality checks (may override route intentionally — no
                # re-derivation afterwards).
                await self._recover_flat_prefer(state, filename, expected_script)
                await self._recover_landscape_reroute(state, filename)

                # Zone-2: orthogonal garble reject guard.  flat_garble_unrecovered
                # is currently only set inside _persist_flat_result, but it is an
                # independent reject trigger that must not be lost in the route
                # dispatch below.  Pre-match guard ensures it fires regardless of
                # the (ok, route) combination.
                decision(
                    event="flat_garble_unrecovered_reject",
                    choice="reject_flat_garble_unrecovered"
                    if state.flat_garble_unrecovered
                    else "proceed_to_route_dispatch",
                    reason="flat garble unrecovered"
                    if state.flat_garble_unrecovered
                    else "no flat garble reject",
                    attrs={"flat_garble_unrecovered": state.flat_garble_unrecovered},
                )
                if state.flat_garble_unrecovered:
                    LOW_QUALITY_TREES.labels(reason="garbling").inc()
                    raise LowQualityTreeError("garbling")

                # Zone-2: exhaustive route dispatch — every (ok, route) pair has
                # an explicit case.  Cases that persist the tree fall through to
                # the _persist_tree_result call after the match block; cases that
                # reject or persist flat return/raise within the case body.
                _route_choice_map = {
                    (True, Route.TREE): "tree_success",
                    (False, Route.FLAT): "flat_persist_success",
                    (False, Route.REJECT): "reject_low_quality",
                    (False, Route.TREE): "persist_tree_with_fail_verdict",
                    (False, Route.PERSIST_FAIL): "persist_tree_with_fail_verdict",
                }
                decision(
                    event="persistence_route_dispatch",
                    choice=_route_choice_map.get(
                        (state.ok, state.route), "unexpected_fallback_persist_tree"
                    ),
                    reason=f"ok={state.ok}, route={state.route.value}",
                    attrs={
                        "ok": state.ok,
                        "route": state.route.value,
                        "first_defect": state.first_defect.value if state.first_defect else None,
                    },
                )
                match (state.ok, state.route):
                    case (True, Route.TREE):
                        pass  # success — persist tree below

                    case (False, Route.FLAT):
                        doc_id = await self._persist_flat_result(
                            state,
                            file_path,
                            filename,
                            ext,
                            expected_script,
                            sha256,
                            file_bytes,
                            pdf_classification,
                            _effective_cfg,
                            _effective_config_at_job_start,
                            script_context=script_context,
                        )
                        if doc_id is not None:
                            return doc_id
                        # Flat persist failed — re-emit as reject.
                        _reject_reason = (
                            "garbling"
                            if state.flat_garble_unrecovered
                            else state.first_defect.value
                        )
                        LOW_QUALITY_TREES.labels(reason=_reject_reason).inc()
                        logger.warning(
                            "Rejecting low-quality tree for %s: reason=%s",
                            filename,
                            _reject_reason,
                        )
                        raise LowQualityTreeError(_reject_reason)

                    case (False, Route.REJECT):
                        _reject_reason = state.first_defect.value
                        LOW_QUALITY_TREES.labels(reason=_reject_reason).inc()
                        logger.warning(
                            "Rejecting low-quality tree for %s: reason=%s",
                            filename,
                            _reject_reason,
                        )
                        raise LowQualityTreeError(_reject_reason)

                    case (False, Route.TREE) | (False, Route.PERSIST_FAIL):
                        logger.warning(
                            "Persisting low-quality tree with FAIL verdict for %s: reason=%s",
                            filename,
                            state.reason,
                        )
                        # fall through to _persist_tree_result below

                    case _:
                        # Zone-3 exhaustiveness guard: finalize_gate_and_route()
                        # keeps route consistent with ok, so (True, !TREE) should
                        # be unreachable.  Log and persist tree as a safe fallback.
                        logger.error(
                            "Unexpected (ok=%s, route=%s) for %s — persisting tree "
                            "as fallback (Zone-3 exhaustiveness guard)",
                            state.ok,
                            state.route,
                            filename,
                        )

                return await self._persist_tree_result(
                    state,
                    filename,
                    ext,
                    expected_script,
                    sha256,
                    file_bytes,
                    pdf_classification,
                    _effective_cfg,
                    _effective_config_at_job_start,
                    script_context=script_context,
                )

            finally:
                if state.tmp_lo_dir:
                    shutil.rmtree(state.tmp_lo_dir, ignore_errors=True)
                if state.tmp_md_path and os.path.exists(state.tmp_md_path):
                    os.unlink(state.tmp_md_path)

    # ------------------------------------------------------------------
    # Retrieval (lazy-load from MinIO)
    # ------------------------------------------------------------------

    async def get_document(self, doc_id: str) -> str:
        """Return document metadata as a JSON string."""
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        structure = data.get("structure", [])
        return json.dumps(
            {
                "doc_id": doc_id,
                "doc_name": data.get("doc_name", data.get("filename", "unknown")),
                "doc_description": data.get("doc_description", ""),
                "section_count": len(structure),
                "sections": [
                    {"title": n.get("title"), "node_id": n.get("node_id")} for n in structure
                ],
            },
            indent=2,
        )

    async def get_document_structure(self, doc_id: str) -> str:
        """Return document tree structure (without text fields) as a JSON string."""
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        return json.dumps(
            {
                "doc_id": doc_id,
                "structure": _strip_text(data.get("structure", [])),
            },
            indent=2,
        )

    async def get_page_content(self, doc_id: str, pages: str) -> str:
        """Return node text for the specified pages as a JSON string.

        pages: single page ('5'), range ('3-7'), or comma list ('3,5,7').
        """
        import json

        data = await asyncio.to_thread(get_doc, doc_id)
        hits = _extract_page_hits(data.get("structure", []), pages)

        if not hits:
            return json.dumps({"error": f"No content found for pages '{pages}' in doc '{doc_id}'."})
        return json.dumps({"doc_id": doc_id, "pages": pages, "content": hits}, indent=2)

    # ------------------------------------------------------------------
    # Private indexing helpers
    # ------------------------------------------------------------------

    def _run_page_index(self, pdf_path: str) -> dict:
        from pageindex import page_index

        return page_index(
            doc=pdf_path,
            model=self.model,
            if_add_node_id="yes",
            if_add_node_summary="yes",
            if_add_node_text="yes",
            if_add_doc_description="yes",
        )

    async def _run_page_index_retrying(self, pdf_path: str) -> dict:
        """D4: bounded retry/backoff wrapper around the blocking page_index() LLM call."""

        async def call_fn(base_url: str | None = None):
            prev_base = None
            if base_url:
                import litellm

                prev_base = litellm.api_base
                litellm.api_base = base_url
            try:
                return await asyncio.to_thread(self._run_page_index, pdf_path)
            finally:
                if base_url:
                    litellm.api_base = prev_base

        return await _llm_with_retry(call_fn)

    async def _run_md_to_tree(self, md_path: str) -> dict:
        from pageindex.page_index_md import md_to_tree

        # D4: bounded retry/backoff around the tree-generation LLM call.
        async def call_fn(base_url: str | None = None):
            prev_base = None
            if base_url:
                import litellm

                prev_base = litellm.api_base
                litellm.api_base = base_url
            try:
                coro = md_to_tree(
                    md_path=md_path,
                    if_thinning=False,
                    if_add_node_summary="yes",
                    summary_token_threshold=200,
                    model=self.model,
                    if_add_doc_description="yes",
                    if_add_node_text="yes",
                    if_add_node_id="yes",
                )
                # md_to_tree is a coroutine; if we're already in an event loop, await
                # directly. If called from a thread (asyncio.to_thread), spin a new loop.
                try:
                    asyncio.get_running_loop()
                    return await coro
                except RuntimeError:
                    return asyncio.run(coro)
            finally:
                if base_url:
                    litellm.api_base = prev_base

        result = await _llm_with_retry(call_fn)

        # RFC-015 D10: splice in any preamble content the fork's tree-builder
        # silently drops (content before the first heading in the source md).
        try:
            md_text = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                md_path,
            )
            result = _synthesize_preamble_node(md_text, result)
        except OSError:
            logger.warning("D10: could not read %s to check for preamble content", md_path)

        # RFC-034 D11: strip ToC-heading nodes before oversized-leaf splitting.
        # RFC-034 D16: guarded against over-stripping long legal statutes --
        # see _strip_toc_heading_nodes_guarded.
        # Zone-6 Step A: char_loss_ratio observability + abort wired inside
        # the guarded function — logs INFO always, fires TOC_STRIP_HIGH_CHAR_LOSS
        # counter when ratio > 0.10, aborts (returns original) when > 0.15.
        result["structure"] = _strip_toc_heading_nodes_guarded(
            result.get("structure", []), doc_name=str(md_path)
        )

        return result
