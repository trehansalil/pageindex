"""CustomPageIndexClient — multi-format document indexing with MinIO persistence."""

from __future__ import annotations

import asyncio
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
    CURRENT_PIPELINE_VERSION,
    PDF_INSPECTOR_PRECLASSIFY,
    REMOTE_MD_RENORMALIZE,
    settings,
)
from ..config import (
    OCR_ESCALATION_PER_PICTURE as _OCR_ESCALATION_PER_PICTURE,
)
from ..converters import (
    PictureResult,
    _tesseract_ocr_image,
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
    FLAT_MARKDOWN_PROFILE,
    GATES,
    ExtractionState,
    GarbleReport,
    LowQualityTreeError,
    Route,
    TreeDefect,
    TreeGateResult,
    _extract_page_hits,
    _flat_block_primary_text,
    _flatten_tree_text,
    _garble_check_flat_blocks,
    _garble_config,
    route_and_extract_flat,
    _strip_text,
    _strip_toc_heading_nodes_guarded,
    _synthesize_preamble_node,
    _tree_max_leaf_ratio,
    compute_verdict,
    detect_garble,
    finalize_gate_and_route,
    prepare_tree,
    validate_tree,
)
from ..metrics import (
    AGPL_FALLBACK_TOTAL,
    BIDI_RENORM_SKIPPED,
    FLAT_DOCS_TOTAL,
    LOW_QUALITY_TREES,
    PDF_EXTRACT_FALLBACKS,
    PDF_INSPECTOR_FORCED_OCR,
    PDF_PRIMARY_CONVERTER_FAILURES,
    RAW_UPLOAD_FAILURES,
    REMOTE_MD_RENORMALIZED,
    VLM_FALLBACK_TOTAL,
)
from ..picture_plane import (
    decide_ocr_strategy,
)
from ..script import BlobKind, RtlDecision, ScriptContext
from ..storage import (
    hash_cache_get,
    hash_cache_set,
    list_processed_docs,
    save_doc,
    save_doc_meta,
    save_flat_doc,
    save_raw,
)

logger = logging.getLogger(__name__)

_MAX_DESC_CHARS = 4000

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
    """
    latin_frac = _latin_fraction(md_content)
    if latin_frac > _BIDI_RENORM_LATIN_GUARD:
        BIDI_RENORM_SKIPPED.inc()
        logger.info(
            "bidi_renorm_skipped: %s latin_frac=%.2f -- bilingual guard",
            filename,
            latin_frac,
        )
        return md_content, RtlDecision(
            reversed=False,
            repair_effective=False,
            sampled=0,
            method="bilingual_guard_skip",
        )
    renorm, rtl_decision = reconstruct_bidi_order(md_content)
    if renorm != md_content:
        REMOTE_MD_RENORMALIZED.inc()
        logger.debug(
            "D3 re-normalization changed %d chars for %s",
            len(md_content) - len(renorm),
            filename,
        )
    return renorm, rtl_decision


def _detect_config_drift(job_start_config: dict | None, effective_cfg: dict) -> dict | None:
    """Zone-7: return job_start_config only when it diverges from the config
    freshly snapshotted at job execution time, else None. A standalone
    function (rather than inline in index()) so the comparison is unit
    testable without invoking the full indexing pipeline.
    """
    if job_start_config is not None and job_start_config != effective_cfg:
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


# Image inputs route through OCR (Fix 4); .xlsx routes through openpyxl -> flat tables.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
_SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".html", ".xlsx"} | _IMAGE_EXTS
# Zone-4: legacy _OCR_ESCALATION removed; split flags _OCR_ESCALATION_GARBLE /
# _OCR_ESCALATION_PER_PICTURE imported from config.py (canonical source).
# RFC-027 D2: PDFs rejected as node_count<3 with fewer than this many chars (zero or
# near-zero/garbled scanned content) also earn the force_full_page_ocr retry, not just
# the garbling reasons above -- calibrated to the Run-10 corpus (highest affected doc
# القرار التنظيمي at 230 garbled chars; legitimate sparse docs all exceed 400 chars).
LOW_CONTENT_OCR_CHAR_FLOOR = int(os.getenv("LOW_CONTENT_OCR_CHAR_FLOOR", "300"))
# Task 6.1: dedicated image-standalone pipeline for PDFs whose content is all images.
# When disabled, falls back to the existing QF2a image-enrichment promotion path.
_IMAGE_STANDALONE_PIPELINE_ENABLED = os.getenv(
    "IMAGE_STANDALONE_PIPELINE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")


# RFC-023 D8a: skip the standalone-image Tesseract recovery below when Docling's
# md_content already carries this many non-whitespace chars (avoids double-counting).
MIN_STANDALONE_IMAGE_MD_CHARS = int(os.getenv("MIN_STANDALONE_IMAGE_MD_CHARS", "100"))
# RFC-023 D11: _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED now imported from config.py
# (Zone-2 flag decoupling — eliminates local env-var read duplication).
# RFC-023 D7 kill-switch (default on): Tesseract-on-raster last resort when the
# VLM fallback itself crashes (rate limit / content-policy / token overflow).
_VLM_TESSERACT_FALLBACK_ENABLED = os.getenv(
    "VLM_TESSERACT_FALLBACK_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")
# RFC-024 D5 kill-switch (default on): also attempt the D7 Tesseract-on-raster
# recovery when the VLM *succeeds* but validate_tree still reports 'garbling'
# (as opposed to only when the VLM call itself raises). Set to false to
# restore the RFC-023 D7 behavior where this path falls through to
# LowQualityTreeError unchanged.
_D7_GARBLE_RECOVERY_ENABLED = os.getenv("D7_GARBLE_RECOVERY_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
# RFC-029 D1 (Task 3.1): flat-prefer multiplier — when flat char count exceeds
# tree char count by this factor, prefer flat over tree result post-validation.
_RFC029_FLAT_PREFER_MULTIPLIER = float(os.getenv("RFC029_FLAT_PREFER_MULTIPLIER", "3.0"))
# RFC-029 D1 (Task 3.1): minimum chars-per-node floor (mirrors helpers.py constant;
# client module holds the flat-prefer logic while helpers.py holds the validate gate).
_RFC029_MIN_CHARS_PER_NODE = float(os.getenv("RFC029_MIN_CHARS_PER_NODE", "500"))


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


# Zone-7: BUILD_SHA is the convention services/docling-service's CI/Dockerfile
# already use; CLIENT_BUILD_SHA was a never-wired legacy name that left this
# permanently "unknown". Prefer BUILD_SHA, fall back to the legacy name.
_CLIENT_BUILD_SHA = os.environ.get("BUILD_SHA") or os.environ.get("CLIENT_BUILD_SHA", "unknown")

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
            expected_script=expected_script,
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
    ) -> None:
        """Conversion front-end: dispatch by extension, run initial validate_tree. Mutates state."""
        if ext == ".pdf":
            md_content = None

            inspector_force_ocr = False
            if (
                PDF_INSPECTOR_PRECLASSIFY
                and pdf_classification is not None
                and pdf_classification.get("pdf_type") in ("scanned", "image_based")
                and pdf_classification.get("confidence", 0) >= 0.90
            ):
                inspector_force_ocr = True
                PDF_INSPECTOR_FORCED_OCR.inc()
                logger.info(
                    "RFC-032: pdf-inspector classified %s as %s (confidence=%.2f), "
                    "forcing full-page OCR upfront",
                    filename,
                    pdf_classification.get("pdf_type"),
                    pdf_classification.get("confidence", 0),
                )

            state.pre_garbled = False
            state.pdf_page_count = None
            from ..config import ALLOW_AGPL_FALLBACK

            if not ALLOW_AGPL_FALLBACK:
                logger.warning(
                    "D3a pre-conversion probe skipped for %s: ALLOW_AGPL_FALLBACK=false "
                    "blocks fitz (PyMuPDF, AGPL-3.0)",
                    filename,
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
                            _probe_ctx = ScriptContext(dominant_script=expected_script, had_presentation_forms=False, source="pre_garble_probe")
                            if raw_text.strip() and detect_garble(
                                raw_text,
                                script_context=_probe_ctx,
                                config=_garble_config,
                                blob_kind=BlobKind.RAW_MARKDOWN,
                            ):
                                state.pre_garbled = True
                                logger.info(
                                    "D3a: raw text layer garbled for %s, forcing full-page "
                                    "OCR upfront",
                                    filename,
                                )
                except Exception:
                    pass

            PRE_GARBLE_FORCE_OCR_ENABLED = (
                os.environ.get("PRE_GARBLE_FORCE_OCR_ENABLED", "false").lower() == "true"
            )

            # Zone-2: force_full_page is a pre-conversion decision independent
            # of has_image_markers (which is unknown until the converter returns).
            # The PER_PICTURE decision is deferred to the converter chain
            # (_recover_picture_results) where has_image_markers reflects actual
            # content.  decide_ocr_strategy is called post-conversion to produce
            # the unified OcrDecision with real document state.
            force_full_page = inspector_force_ocr or (
                state.pre_garbled and PRE_GARBLE_FORCE_OCR_ENABLED
            )

            chain = pdf_markdown_converters()
            primary_name = chain[0][0] if chain else None
            state.used_converter = None
            state.extraction_stages_captured = []
            state.use_remote = bool(
                getattr(settings, "docling_service_url", None) and self._staging_key
            )
            for idx, (conv_name, conv_fn, _conv_supports_ocr) in enumerate(chain):
                try:
                    logger.info("Extracting PDF to markdown via %s: %s", conv_name, filename)
                    if state.use_remote and _conv_supports_ocr:
                        # NOTE (Zone-1 known gap): _remote_pdf_to_markdown does
                        # NOT forward expected_script to the external Docling
                        # microservice — the /convert/pdf payload has no script
                        # field, so server-side garble checks (if any) fall back
                        # to infer_script(text).  Closing this gap requires a
                        # contract change in the sibling hetzner-deployment-service
                        # repo and is out of scope for client-side threading.
                        # Post-conversion garble detection in the retry/escalation
                        # paths already receives expected_script from this method.
                        logger.info(
                            "Routing %s to external Docling service at %s",
                            filename,
                            settings.docling_service_url,
                        )
                        if force_full_page:
                            md_content, state.pic_results = await _remote_pdf_to_markdown(
                                self._staging_key,
                                force_full_page_ocr=True,
                                ocr_lang_override=detect_ocr_langs(filename),
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
                            )
                    elif force_full_page and _conv_supports_ocr:
                        md_content, state.pic_results, stages_out = _split_converter_output(
                            await asyncio.to_thread(
                                conv_fn,
                                file_path,
                                True,
                                ocr_lang_override=detect_ocr_langs(filename),
                                expected_script=expected_script,
                            )
                        )
                        if stages_out:
                            state.extraction_stages_captured = stages_out
                    else:
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
                    if idx == 0:
                        PDF_PRIMARY_CONVERTER_FAILURES.labels(
                            converter=conv_name, error=type(conv_exc).__name__
                        ).inc()
                        logger.error(
                            "PRIMARY PDF converter '%s' FAILED for %s (%s: %s); falling "
                            "back to the next converter — output quality will likely "
                            "degrade. If this is docling, verify model artifacts are "
                            "present (DOCLING_ARTIFACTS_PATH or network egress) and the "
                            "docling-hierarchical-pdf add-on is installed in THIS image.",
                            conv_name,
                            filename,
                            type(conv_exc).__name__,
                            conv_exc,
                            exc_info=True,
                        )
                    else:
                        logger.warning(
                            "%s failed for %s (%s); trying next converter",
                            conv_name,
                            filename,
                            conv_exc,
                        )
            if md_content is not None:
                # Zone-2: stamp full_page_already_applied when the initial
                # conversion itself used force_full_page OCR.  This prevents
                # downstream per-picture OCR from re-processing regions that
                # the full-page OCR already covered.
                if force_full_page:
                    state.full_page_already_applied = True
                # Zone-2: post-conversion OcrDecision with actual has_image_markers
                # (was hardcoded False pre-conversion; now reflects real content).
                _ocr_decision = decide_ocr_strategy(
                    ocr_escalation_enabled=_OCR_ESCALATION_PER_PICTURE,
                    has_image_markers=bool(md_content and "<!-- image -->" in md_content),
                    force_full_page=force_full_page,
                    garble_status=state.pre_garbled,
                    full_page_already_applied=state.full_page_already_applied,
                )
                logger.debug(
                    "Zone-2: post-conversion OcrDecision for %s: mode=%s, "
                    "has_image_markers=%s, full_page_already_applied=%s",
                    filename,
                    _ocr_decision.mode.value,
                    _ocr_decision.has_image_markers,
                    _ocr_decision.full_page_already_applied,
                )
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
                if state.use_remote and REMOTE_MD_RENORMALIZE:
                    md_content, state.rtl_decision = _renormalize_bidi_guarded(
                        md_content,
                        filename,
                    )
                    # Zone-7: mark bidi renorm as applied so downstream
                    # _recover_rtl_repair skips per-node double-correction.
                    state.bidi_renorm_applied = True
                with tempfile.NamedTemporaryFile(
                    suffix=".md", delete=False, mode="w", encoding="utf-8"
                ) as md_tmp:
                    md_tmp.write(md_content)
                    state.tmp_md_path = md_tmp.name
                state.result = await self._run_md_to_tree(state.tmp_md_path)
            else:
                PDF_EXTRACT_FALLBACKS.inc()
                logger.error(
                    "ALL markdown converters failed for %s; falling back to legacy "
                    "page_index. Investigate converter availability in this image.",
                    filename,
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
            except Exception as lo_exc:
                logger.warning(
                    "LibreOffice/page_index failed for %s (%s), falling back to "
                    "markdown conversion",
                    filename,
                    lo_exc,
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
            img_langs = await asyncio.to_thread(ensure_tessdata, ["ara", "deu", "eng"])
            md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)
            img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
            standalone_ocr_text = ""
            if len("".join(md_content.split())) <= MIN_STANDALONE_IMAGE_MD_CHARS:
                standalone_ocr_text = await asyncio.to_thread(
                    _tesseract_ocr_image, file_path, img_langs
                )
            else:
                standalone_ocr_text = md_content
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
        _vt_raw = validate_tree(
            state.result.get("structure", []),
            expected_script=expected_script,
            page_count=state.pdf_page_count if ext == ".pdf" else None,
            rtl_decision=state.rtl_decision,
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
        flat_md = state.md_content
        if flat_md is None and state.tmp_md_path is not None:
            flat_md = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                state.tmp_md_path,
            )
        if flat_md is None and ext in (".md", ".markdown", ".txt"):
            flat_md = await asyncio.to_thread(
                lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                file_path,
            )

        state.flat_garble_unrecovered = False
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
        _flat_garble_ctx = script_context if script_context is not None else ScriptContext(
            dominant_script=expected_script,
            had_presentation_forms=False,
            source="flat_garble_gate",
        )
        _flat_garble_report = _garble_check_flat_blocks(
            _garble_blocks,
            script_context=_flat_garble_ctx,
            config=_garble_config,
        )
        if _flat_garble_report:
            state.flat_garble_unrecovered = True
            state.reason = "garbling"
            logger.warning(
                "Flat-path per-block garble gate triggered for %s; overriding reason to garbling (prongs=%s)",
                filename,
                _flat_garble_report.fired_prongs,
            )
            if ext == ".pdf" and settings.vlm_fallback:
                try:
                    from ..converters import vlm_extract_markdown

                    logger.warning(
                        "Flat-path garbling on %s; attempting VLM fallback (model=%s)",
                        filename,
                        settings.vlm_model,
                    )
                    vlm_md = await vlm_extract_markdown(file_path, settings.vlm_model)
                    _vlm_ctx = script_context if script_context is not None else ScriptContext(dominant_script=expected_script, had_presentation_forms=False, source="vlm_fallback_garble")
                    _, _vlm_blocks = await asyncio.to_thread(route_and_extract_flat, vlm_md)
                    if not _garble_check_flat_blocks(
                        _vlm_blocks,
                        script_context=_vlm_ctx,
                        config=_garble_config,
                    ):
                        flat_md = vlm_md
                        state.pic_results = []
                        state.flat_garble_unrecovered = False
                        VLM_FALLBACK_TOTAL.labels(result="recovered").inc()
                    else:
                        VLM_FALLBACK_TOTAL.labels(result="still_garbled").inc()
                except Exception as vlm_exc:
                    VLM_FALLBACK_TOTAL.labels(result="error").inc()
                    logger.error(
                        "VLM fallback failed for %s (%s)",
                        filename,
                        vlm_exc,
                        exc_info=True,
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

        _vr = compute_verdict(
            flat_structure,
            content_class,
            state.gate_result,
            image_enrichment_ratio=image_enrichment_ratio,
            expected_script=expected_script,
        )
        f_verdict, f_verdict_reason = _vr.verdict, _vr.reason

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
        # store.  save_flat_doc internally calls save_doc_meta with this dict
        # (non-verdict fields), then a separate save_doc_meta call below
        # merges verdict fields into the sidecar.
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
            "build_sha": _CLIENT_BUILD_SHA,
            "effective_config": _effective_cfg,
        }
        if _effective_config_at_job_start is not None:
            flat_meta["effective_config_at_job_start"] = _effective_config_at_job_start
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
        }
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
    ) -> str:
        """Persist a tree-routed document. Returns doc_id."""
        doc_id = str(uuid.uuid4())

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
            inspector_class=(pdf_classification.get("pdf_type") if pdf_classification else None),
            expected_script=expected_script,
        )
        verdict, verdict_reason = _vr.verdict, _vr.reason

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
            "build_sha": _CLIENT_BUILD_SHA,
            "effective_config": _effective_cfg,
            "decider_version": "zone3_decide_rtl_v1",
            # Verdict fields -- authoritative via sidecar (Zone-5)
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "max_leaf_ratio": round(mlr, 4),
            "pipeline_version": CURRENT_PIPELINE_VERSION,
            "verdict_computed_at": _verdict_computed_at,
        }
        if state.gate_result is not None and state.gate_result.all_defects:
            meta["all_defects"] = sorted(d.value for d in state.gate_result.all_defects)
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
            if pdf_classification and PDF_INSPECTOR_PRECLASSIFY:
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
        }
        return doc_id

    # ------------------------------------------------------------------
    # Indexing — orchestrator
    # ------------------------------------------------------------------

    async def index(
        self,
        file_path: str,
        mode: str = "auto",
        pdf_classification: dict | None = None,
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
        # all garble/gate call sites.  Filename-based inference runs here;
        # raw_text is not available yet (PDF text layer comes from the fitz
        # probe inside _convert_to_tree).  ScriptContext.from_document
        # handles empty raw_text gracefully (filename-only inference).
        script_context = ScriptContext.from_document(filename)
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
                    logger.info(
                        "Skipping %s (unchanged, existing doc_id=%s)", filename, d["doc_id"]
                    )
                    self.last_content_class = d.get("content_class") or None
                    return d["doc_id"]

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

        try:
            await self._convert_to_tree(
                state, file_path, filename, ext, expected_script, pdf_classification
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
                expected_script = script_context.dominant_script

            # Zone-1: GateSpec-driven recovery loop (single source of truth).
            # Each GateSpec with non-empty recovery_fns declares its own
            # recovery_eligible predicate and recovery method names.
            # Iteration follows GATES severity order; dedup by recovery_fns
            # tuple prevents repeated firing when multiple GateSpecs share
            # the same recovery pipeline (e.g. GARBLING and NODE_GARBLING
            # both carry _eligible_garble + the same recovery_fns).
            _fired_recovery: set[tuple[str, ...]] = set()
            for _gate in GATES:
                if not _gate.recovery_fns or _gate.recovery_fns in _fired_recovery:
                    continue
                if _gate.recovery_eligible is None or not _gate.recovery_eligible(state):
                    continue
                _fired_recovery.add(_gate.recovery_fns)
                for _fn_name in _gate.recovery_fns:
                    await getattr(self, _fn_name)(state, file_path, filename, ext, expected_script, script_context=script_context)
                # Zone-3: finalize_gate_and_route() is now called inside
                # each recovery method (_reconvert_and_revalidate,
                # _recover_rtl_repair) so gate_result/ok/reason/first_defect/
                # route are always consistent after every recovery step.
                # Recovery methods that intentionally override state.route
                # (e.g. _recover_rtl_flat_compare, _recover_vlm_fallback)
                # do so AFTER finalize_gate_and_route, which is correct.
                state.total_chars = len(_flatten_tree_text(state.result.get("structure", [])))

            # Quality checks (may override route intentionally — no
            # re-derivation afterwards).
            await self._recover_flat_prefer(state, filename, ext, expected_script)
            await self._recover_landscape_reroute(state, filename)

            # Zone-2: orthogonal garble reject guard.  flat_garble_unrecovered
            # is currently only set inside _persist_flat_result, but it is an
            # independent reject trigger that must not be lost in the route
            # dispatch below.  Pre-match guard ensures it fires regardless of
            # the (ok, route) combination.
            if state.flat_garble_unrecovered:
                LOW_QUALITY_TREES.labels(reason="garbling").inc()
                logger.warning(
                    "Rejecting low-quality tree for %s: reason=garbling",
                    filename,
                )
                raise LowQualityTreeError("garbling")

            # Zone-2: exhaustive route dispatch — every (ok, route) pair has
            # an explicit case.  Cases that persist the tree fall through to
            # the _persist_tree_result call after the match block; cases that
            # reject or persist flat return/raise within the case body.
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
                    # Flat persist failed (garble or unavailable) — reject.
                    _reject_reason = (
                        "garbling" if state.flat_garble_unrecovered else state.first_defect.value
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
