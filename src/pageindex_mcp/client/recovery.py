"""Recovery mixin — OCR escalation, RTL repair, VLM fallback, flat-prefer."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING

from ..config import (
    ZDRComplianceError,
    pipeline_config,
    settings,
)
from ..helpers.heuristic_registry import registry as _heuristic_registry
from ..converters import (
    TessdataUnavailableError,
    detect_ocr_langs,
    ensure_tessdata,
    pdf_to_markdown_docling,
    reconstruct_bidi_order,
    splice_picture_text_for_tree,
)
from ..helpers import (
    ExtractionState,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    _flat_block_primary_text,
    _flatten_tree_text,
    _garble_config,
    detect_garble,
    finalize_gate_and_route,
    route_and_extract_flat,
    validate_tree,
)
from ..helpers.garble import _infer_presentation_forms as _infer_pf
from ..metrics import (
    HR3_EGRESS_BLOCKED_TOTAL,
    OCR_ESCALATION_TOTAL,
    VLM_FALLBACK_TOTAL,
)
from ..picture_plane import SkipReason, skip_reason_from_str
from ..script import BlobKind, ScriptContext, decide_rtl

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---- Module-level constants re-exported from client.py scope ----
# These are read from env in the original client.py at module level.

# Backward-compat module-level aliases derived from the pipeline_config
# singleton.  Function bodies below read ``pipeline_config.<attr>`` directly
# so they pick up fresh values after ``reset_pipeline_config()``.
# These aliases are kept ONLY for re-export via ``client/__init__.py`` and for
# test code that patches them via ``monkeypatch.setattr`` -- new code must
# read ``pipeline_config.<attr>`` directly.
LOW_CONTENT_OCR_CHAR_FLOOR = pipeline_config.low_content_ocr_char_floor
_VLM_TESSERACT_FALLBACK_ENABLED = pipeline_config.vlm_tesseract_fallback_enabled
_D7_GARBLE_RECOVERY_ENABLED = pipeline_config.d7_garble_recovery_enabled
_RFC029_FLAT_PREFER_MULTIPLIER = pipeline_config.rfc029_flat_prefer_multiplier
_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED = pipeline_config.image_dominant_ocr_escalation_enabled
_OCR_ESCALATION_GARBLE = pipeline_config.ocr_escalation_garble
REMOTE_MD_RENORMALIZE = pipeline_config.remote_md_renormalize

# Zone-3: Arabic documents use a lower flat-prefer multiplier because
# heading injection can inflate tree structure, making a content-poor tree
# appear competitive with a content-rich flat extraction.  The 1.5x default
# lets flat win when it carries meaningfully more content (e.g. marsoom-13:
# flat=5972 vs tree=1225 chars).
_ARABIC_FLAT_PREFER_MULTIPLIER = float(os.getenv("ARABIC_FLAT_PREFER_MULTIPLIER", "1.5"))


def _repeating_token_density(text: str) -> float:
    """Repeating-token density (0.0=none, 1.0=maximally degenerate).

    Zone-2 fix: returns 1.0 for <20 tokens so the RFC-029 D4
    density comparison always runs for no-text-layer PDFs.
    """
    from collections import Counter

    tokens = [t for t in text.split() if any(c.isalnum() for c in t)]
    if len(tokens) < 20:
        return 1.0
    return Counter(tokens).most_common(1)[0][1] / len(tokens)


def _keep_best_wins(
    *,
    pre_result: dict,
    pre_total_chars: int,
    post_result: dict,
    post_ok: bool,
    expected_script: str | None,
    script_context: ScriptContext | None,
    filename: str,
) -> bool:
    """Pure-function keep-best decision for OCR retry vs pre-retry snapshot.

    Extracted from ``_execute_ocr_retry`` to eliminate the ``_Unset``-union
    type hazard: callers narrow ``RecoveryOutcome`` fields to concrete types
    before invoking this function, so every comparison is statically safe.

    Returns ``True`` when the OCR-retry result should be kept (retry wins),
    ``False`` when the pre-retry snapshot should be restored.

    Decision cascade (order matters):
    1. Zero-char shortcut: pre had no text, post has text -> retry wins.
    2. Char-count regression: post has fewer chars -> revert.
    3. Equal-count garble tiebreak: retry wins if pre was garbled and post is not.
    4. Char-count increase + garble check: if pre was garbled, apply RFC-029 D4
       density comparison; if pre was not garbled, retry wins (more chars, no garble).
    """
    post_retry_chars = len(_flatten_tree_text(post_result.get("structure", [])))

    # Zone-7 fix: scan pre-retry text for presentation forms instead of
    # hardcoding False -- closes the ScriptContext threading gap so the
    # garble-detection fallback at detect_garble:543-554 receives accurate
    # had_presentation_forms when comparing pre vs post OCR results.
    _kb_ctx = script_context if script_context is not None else ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=_infer_pf(
            _flatten_tree_text(pre_result.get("structure", []))
        ),
        source="ocr_retry_keep_best",
    )

    # Stage 1: zero-char shortcut (Zone-8 fix).
    if pre_total_chars == 0 and post_retry_chars > 0:
        return True

    # Stage 2: char-count regression check.
    if post_retry_chars < pre_total_chars:
        return False

    # Stage 3: equal-count garble tiebreak.
    if post_retry_chars == pre_total_chars:
        _pre_text = _flatten_tree_text(pre_result.get("structure", []))
        _post_text = _flatten_tree_text(post_result.get("structure", []))
        return post_ok or (
            bool(detect_garble(
                _pre_text,
                script_context=_kb_ctx,
                config=_garble_config,
                blob_kind=BlobKind.TREE_TEXT,
            ))
            and not detect_garble(
                _post_text,
                script_context=_kb_ctx,
                config=_garble_config,
                blob_kind=BlobKind.TREE_TEXT,
            )
        )

    # Stage 4: char-count increase — check pre-garble + RFC-029 D4 density.
    _pre_text_cmp = _flatten_tree_text(pre_result.get("structure", []))
    _pre_garble_flag = bool(detect_garble(
        _pre_text_cmp,
        script_context=_kb_ctx,
        config=_garble_config,
        blob_kind=BlobKind.TREE_TEXT,
    ))
    if not _pre_garble_flag:
        return True

    _pre_density = _repeating_token_density(
        _flatten_tree_text(pre_result.get("structure", []))
    )
    _post_density = _repeating_token_density(
        _flatten_tree_text(post_result.get("structure", []))
    )
    _density_improved = _post_density < _pre_density * 0.80
    if not _density_improved:
        logger.warning(
            "RFC-029 D4: post-retry repeating-token density (%.3f)"
            " not substantially better than pre-retry (%.3f) for %s"
            " — reverting to pre-retry result",
            _post_density,
            _pre_density,
            filename,
        )
    return _density_improved


class RecoveryMixin:
    """Mixin providing recovery methods for CustomPageIndexClient.

    All methods reference ``self.*`` attributes that are defined on
    ``CustomPageIndexClient`` — this class is intended to be used only
    as a base class for that client.
    """

    # -- TYPE_CHECKING-only stubs for attributes defined on the concrete
    # CustomPageIndexClient.  These let Pyright verify attribute access on
    # ``self`` without requiring a runtime Protocol or ABC.
    if TYPE_CHECKING:
        _staging_key: str | None

        async def _reconvert_and_revalidate(
            self,
            state: ExtractionState,
            md_content: str,
            *,
            expected_script: str | None,
            script_context: ScriptContext | None = None,
        ) -> None: ...

        async def _run_md_to_tree(self, md_path: str) -> dict: ...

    async def _execute_ocr_retry(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
        *,
        reason_label: str,
        splice_label: str,
        use_keep_best: bool,
        metric_fail_label: str,
    ) -> bool:
        """Zone-1: shared OCR retry execution (language derivation, OCR
        dispatch, picture splice, reconvert + revalidate, keep-best, metrics).

        Called by ``_recover_garble_ocr``, ``_recover_low_content_ocr``, and
        ``_recover_image_dominant_ocr`` after their per-method eligibility
        checks.  Factoring the ~150-line shared tail into one helper avoids
        the duplication that motivated the former ``_recover_ocr_retry``
        unification while keeping per-method eligibility decoupled.

        Returns True when a successful full-page OCR re-extraction ran
        (callers should set ``state.full_page_already_applied = True``).
        Returns False on error or when keep-best reverted to pre-retry.
        """
        # Lazy imports for cross-submodule deps
        from .images import TREE_PATH_PICTURE_SPLICE_ENABLED, _log_pic_splice_trace
        from .indexer import _renormalize_bidi_guarded, _split_converter_output
        from .remote import _remote_pdf_to_markdown

        # ---- Pre-retry snapshot (GARBLE/LOW_CONTENT only) ----
        pre_retry: RecoveryOutcome | None = None
        if use_keep_best:
            pre_retry = RecoveryOutcome(
                result=state.result,
                ok=state.ok,
                reason=state.reason,
                gate_result=state.gate_result,
                total_chars=state.total_chars,
                md_content=state.md_content,
                pic_results=state.pic_results,
                used_converter=state.used_converter,
                # Zone-7: capture full recovery-relevant state so
                # keep-best revert restores a consistent snapshot.
                route=state.route,
                rtl_decision=state.rtl_decision,
                tmp_md_path=state.tmp_md_path,
                bidi_renorm_applied=state.bidi_renorm_applied,
            )

        try:
            # ---- Language derivation ----
            escalation_langs: list[str] = []
            for src in (
                detect_ocr_langs(filename),
                detect_ocr_langs(state.md_content or ""),
            ):
                for lg in src:
                    if lg not in escalation_langs:
                        escalation_langs.append(lg)
            try:
                langs = await asyncio.to_thread(ensure_tessdata, escalation_langs)
            except TessdataUnavailableError:
                langs = ["deu", "eng"]
                logger.warning(
                    "tessdata unavailable for %s (detected %s); "
                    "degrading to %s — pre-bake traineddata in worker image",
                    filename, escalation_langs, langs,
                )

            logger.warning(
                "%s on %s; escalating to force_full_page_ocr (lang=%s)",
                reason_label,
                filename,
                langs,
            )

            # ---- OCR dispatch ----
            # Zone-7: OCR re-extraction produces new md_content; reset
            # bidi_renorm_applied so the flag reflects the new content.
            state.bidi_renorm_applied = False
            if state.use_remote:
                assert self._staging_key is not None, (
                    "use_remote=True but _staging_key is None"
                )
                state.md_content, state.pic_results = await _remote_pdf_to_markdown(
                    self._staging_key,
                    force_full_page_ocr=True,
                    ocr_lang_override=langs,
                    expected_script=expected_script,
                )
            else:
                state.md_content, state.pic_results, stages_out = _split_converter_output(
                    await asyncio.to_thread(
                        pdf_to_markdown_docling,
                        file_path,
                        True,
                        langs,
                        expected_script=expected_script,
                    )
                )
                if stages_out:
                    state.extraction_stages_captured = stages_out
            state.used_converter = "docling"
            # Zone-2: full-page OCR successfully applied.  The return value
            # signals callers to set state.full_page_already_applied = True
            # explicitly, replacing the former direct mutation that made the
            # cross-module data flow implicit.
            _ocr_applied = True

            # ---- Picture splice ----
            if state.pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:
                _log_pic_splice_trace(filename, splice_label, state.pic_results)
                state.md_content = splice_picture_text_for_tree(state.md_content, state.pic_results)

            # Zone-6: ALWAYS clear stale rtl_decision before revalidation so
            # validate_tree recomputes on new text.  Unifies the inconsistent
            # semantics where Recovery 1 cleared only on remote+renormalize
            # and Recovery 5 cleared unconditionally.
            if state.use_remote and pipeline_config.remote_md_renormalize:
                state.md_content, state.rtl_decision = _renormalize_bidi_guarded(
                    state.md_content,
                    filename,
                )
                # Zone-7: mark bidi renorm as applied on new OCR content.
                state.bidi_renorm_applied = True
            else:
                state.rtl_decision = None

            await self._reconvert_and_revalidate(
                state, state.md_content, expected_script=expected_script,
                script_context=script_context,
            )

            # ---- Keep-best heuristic (GARBLE/LOW_CONTENT only) ----
            if use_keep_best and pre_retry is not None:
                # Narrow _Unset union types: pre_retry was constructed with
                # all fields set from state, so these are always concrete.
                assert isinstance(pre_retry.total_chars, int)
                assert isinstance(pre_retry.result, dict)

                retry_wins = _keep_best_wins(
                    pre_result=pre_retry.result,
                    pre_total_chars=pre_retry.total_chars,
                    post_result=state.result,
                    post_ok=state.ok,
                    expected_script=expected_script,
                    script_context=script_context,
                    filename=filename,
                )
                if not retry_wins:
                    pre_retry.apply(state)
                    _ocr_applied = False
                    # Zone-7: apply() restores state.tmp_md_path to the
                    # pre-retry path string, but _reconvert_and_revalidate
                    # may have unlinked the file at that path during the
                    # OCR retry.  Re-materialise the tempfile from the
                    # (now-restored) state.md_content so downstream
                    # consumers find valid content on disk.
                    if state.tmp_md_path and os.path.exists(state.tmp_md_path):
                        os.unlink(state.tmp_md_path)
                    with tempfile.NamedTemporaryFile(
                        suffix=".md", delete=False, mode="w", encoding="utf-8"
                    ) as md_tmp:
                        md_tmp.write(state.md_content)
                        state.tmp_md_path = md_tmp.name

            # ---- Metric ----
            _metric_result = "recovered" if state.ok else metric_fail_label
            OCR_ESCALATION_TOTAL.labels(result=_metric_result).inc()
            return _ocr_applied
        except Exception as ocr_exc:
            OCR_ESCALATION_TOTAL.labels(result="error").inc()
            logger.error(
                "%s OCR escalation failed for %s (%s)",
                reason_label,
                filename,
                ocr_exc,
                exc_info=True,
            )
            return False

    # -- Zone-1: per-defect OCR recovery methods (split from _recover_ocr_retry) --

    async def _recover_garble_ocr(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 1: garble OCR escalation. Mutates state.

        Defect-type eligibility (GARBLING / NODE_GARBLING) is enforced by
        ``GateSpec.recovery_eligible`` — this method checks only the flag
        gate and basic preconditions.
        """
        if state.ok or ext != ".pdf":
            return
        if not pipeline_config.ocr_escalation_garble:
            return
        applied = await self._execute_ocr_retry(
            state,
            file_path,
            filename,
            ext,
            expected_script,
            script_context,
            reason_label="Garbling",
            splice_label="garble_escalation",
            use_keep_best=True,
            metric_fail_label="still_garbled",
        )
        if applied:
            state.full_page_already_applied = True

    async def _recover_low_content_ocr(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 1b: low-content OCR escalation. Mutates state.

        Defect-type eligibility (NODE_COUNT_LOW) is enforced by
        ``GateSpec.recovery_eligible`` — this method checks the flag gate
        and the character-count floor.
        """
        if state.ok or ext != ".pdf":
            return
        if not pipeline_config.ocr_escalation_low_content:
            return
        if state.total_chars >= pipeline_config.low_content_ocr_char_floor:
            return
        applied = await self._execute_ocr_retry(
            state,
            file_path,
            filename,
            ext,
            expected_script,
            script_context,
            reason_label="Low content",
            splice_label="garble_escalation",
            use_keep_best=True,
            metric_fail_label="still_garbled",
        )
        if applied:
            state.full_page_already_applied = True

    async def _recover_image_dominant_ocr(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 5: image-dominant OCR escalation. Mutates state.

        Defect-type eligibility (NODE_COUNT_LOW / DEPTH_LOW) is enforced by
        ``GateSpec.recovery_eligible`` — this method checks the flag gate,
        flat routing availability, and image-line ratio.
        """
        if state.ok or ext != ".pdf":
            return
        if state.full_page_already_applied:
            return
        if not pipeline_config.image_dominant_ocr_escalation_enabled:
            return
        if not (settings.flat_doc_routing and state.md_content):
            return
        # Image-line ratio gate (>50% non-empty lines must be image markers).
        total_lines = state.md_content.splitlines()
        non_empty_lines = [ln for ln in total_lines if ln.strip()]
        image_lines = sum(1 for ln in non_empty_lines if "<!-- image -->" in ln)
        if not non_empty_lines or (image_lines / len(non_empty_lines)) <= 0.50:
            return
        applied = await self._execute_ocr_retry(
            state,
            file_path,
            filename,
            ext,
            expected_script,
            script_context,
            reason_label=f"Image-dominant ({image_lines}/{len(non_empty_lines)} non-empty lines)",
            splice_label="image_dominant_escalation",
            use_keep_best=True,
            metric_fail_label="still_image_only",
        )
        if applied:
            state.full_page_already_applied = True

    async def _recover_rtl_repair(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 2: RTL bidi repair in-place. Mutates state.

        Bidi-RTL-split fix: logs ``BIDI_NORM_VERSION`` so version drift
        between the per-node repair path and the whole-document
        normalization paths is observable rather than silent.
        """
        if not (not state.ok and state.first_defect == TreeDefect.RTL_REVERSAL and ext == ".pdf"):
            return
        # Zone-7: guard against double bidi correction.
        # _renormalize_bidi_guarded (whole-markdown-level) already ran on
        # this md_content during _convert_to_tree or _recover_ocr_retry.
        # Running per-node reconstruct_bidi_order again would over-correct
        # already-fixed RTL text, collapsing bilingual content structure.
        if state.bidi_renorm_applied:
            logger.info(
                "RTL_REVERSAL on %s but bidi_renorm already applied "
                "— skipping per-node reconstruct_bidi_order to avoid "
                "double-correction",
                filename,
            )
            return
        try:
            from ..converters.normalize import BIDI_NORM_VERSION

            def _repair_rtl_nodes(nodes: list) -> None:
                for n in nodes:
                    for key in ("title", "text"):
                        val = n.get(key)
                        if isinstance(val, str) and val:
                            repaired, _node_decision = reconstruct_bidi_order(val)
                            n[key] = repaired
                    _repair_rtl_nodes(n.get("nodes") or [])

            _repair_rtl_nodes(state.result.get("structure", []))
            # Zone-6: clear stale rtl_decision — the tree nodes have been
            # mutated by per-node reconstruct_bidi_order; force validate_tree
            # to recompute on the repaired tree text.
            state.rtl_decision = None
            _vt_raw = validate_tree(
                state.result.get("structure", []),
                expected_script=expected_script,
                page_count=state.pdf_page_count if ext == ".pdf" else None,
            )
            finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing)
            logger.warning(
                "RTL reversal on %s; reconstruct_bidi_order repair %s (bidi_norm_v%d)",
                filename,
                "converged" if state.ok else "did not converge",
                BIDI_NORM_VERSION,
            )
        except Exception as bidi_exc:
            logger.error("RTL bidi repair failed for %s (%s)", filename, bidi_exc, exc_info=True)

    async def _recover_rtl_flat_compare(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 3: RTL flat-vs-tree reversal comparison. Mutates state."""
        if not (
            not state.ok
            and state.first_defect == TreeDefect.RTL_REVERSAL
            and ext == ".pdf"
            and settings.flat_doc_routing
            and state.md_content
        ):
            return
        try:
            _flat_cmp_cc, _flat_cmp_blocks = await asyncio.to_thread(
                route_and_extract_flat, state.md_content
            )
            _flat_cmp_text = "\n".join(_flat_block_primary_text(b) for b in _flat_cmp_blocks)
            _tree_cmp_text = _flatten_tree_text(state.result.get("structure", []))
            if not decide_rtl(_flat_cmp_text).reversed and decide_rtl(_tree_cmp_text).reversed:
                logger.warning(
                    "RFC-033 D8: tree-path text still mirror-reversed after "
                    "bidi repair for %s; flat-path source not reversed — "
                    "preferring flat result",
                    filename,
                )
                _vt_existing = state.gate_result if state.gate_result is not None else (state.ok, state.reason)
                finalize_gate_and_route(
                    state, _vt_existing, settings.flat_doc_routing,
                    recovery_method="rtl_comparison",
                    recovery_succeeded=True,
                    force_route=Route.FLAT,
                )
        except Exception as _flat_cmp_exc:
            logger.warning(
                "RFC-033 D8: flat-path reversal comparison failed for %s (%s); keeping tree",
                filename,
                _flat_cmp_exc,
            )

    async def _recover_vlm_fallback(
        self,
        state: ExtractionState,
        file_path: str,
        filename: str,
        ext: str,
        expected_script: str | None,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 4: VLM last-resort fallback for garble-rejected PDFs. Mutates state.

        Zone-1: defect-type eligibility (GARBLING / NODE_GARBLING) is enforced
        by ``GateSpec.recovery_eligible`` — only GateSpecs for garble-type
        defects list this method in ``recovery_fns``.
        """
        if not (not state.ok and ext == ".pdf" and settings.vlm_fallback):
            return
        try:
            from ..converters import vlm_extract_markdown

            logger.warning(
                "Garbling persists after OCR escalation for %s; attempting VLM fallback (model=%s)",
                filename,
                settings.vlm_model,
            )
            state.md_content = await vlm_extract_markdown(file_path, settings.vlm_model)
            state.pic_results = []
            # Zone-6: VLM re-extraction produces entirely new text;
            # clear stale rtl_decision so validate_tree recomputes.
            state.rtl_decision = None
            await self._reconvert_and_revalidate(
                state, state.md_content, expected_script=expected_script,
                script_context=script_context,
            )
            VLM_FALLBACK_TOTAL.labels(result="recovered" if state.ok else "still_garbled").inc()

            _try_tesseract_raster = (
                not state.ok
                and state.first_defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)
                and pipeline_config.d7_garble_recovery_enabled
            )
        except ZDRComplianceError as vlm_zdr_exc:
            VLM_FALLBACK_TOTAL.labels(result="compliance_blocked").inc()
            HR3_EGRESS_BLOCKED_TOTAL.labels(path="vlm").inc()
            logger.info(
                "VLM fallback skipped for %s: HR3 compliance block (%s)",
                filename,
                vlm_zdr_exc,
            )
            _try_tesseract_raster = pipeline_config.vlm_tesseract_fallback_enabled
        except Exception as vlm_exc:
            VLM_FALLBACK_TOTAL.labels(result="error").inc()
            logger.error(
                "VLM fallback failed for %s (%s)",
                filename,
                vlm_exc,
                exc_info=True,
            )
            _try_tesseract_raster = pipeline_config.vlm_tesseract_fallback_enabled

        if _try_tesseract_raster:
            from .images import _attempt_tesseract_raster_recovery

            recovered_md = await _attempt_tesseract_raster_recovery(
                file_path, expected_script, filename,
                script_context=script_context,
            )
            if recovered_md:
                state.md_content = recovered_md
                state.pic_results = []
                _vt_existing = state.gate_result if state.gate_result is not None else (state.ok, state.reason)
                finalize_gate_and_route(
                    state, _vt_existing, settings.flat_doc_routing,
                    recovery_method="vlm_tesseract_raster",
                    recovery_succeeded=True,
                    force_route=Route.FLAT,
                )

    async def _recover_flat_prefer(
        self,
        state: ExtractionState,
        filename: str,
        ext: str,
        expected_script: str | None = None,
        *,
        script_context: ScriptContext | None = None,
    ) -> None:
        """Recovery 6: content-density flat-prefer guard. Mutates state.

        Zone-3: when *expected_script* is ``"Arab"``, a lower multiplier
        (``ARABIC_FLAT_PREFER_MULTIPLIER``, default 1.5) is used to
        compensate for heading-injection inflation in Arabic trees.
        """
        if not (state.ok and state.md_content and settings.flat_doc_routing):
            return
        _tree_char_count = len(_flatten_tree_text(state.result.get("structure", [])))
        if _tree_char_count <= 0:
            return
        # Zone-3: select script-aware multiplier
        if expected_script == "Arab":
            _heuristic_registry.fire("_ARABIC_FLAT_PREFER_MULTIPLIER")
        _multiplier = (
            _ARABIC_FLAT_PREFER_MULTIPLIER
            if expected_script == "Arab"
            else pipeline_config.rfc029_flat_prefer_multiplier
        )
        try:
            _flat_cc, _flat_blocks = await asyncio.to_thread(
                route_and_extract_flat, state.md_content
            )
            _flat_char_count = sum(len(_flat_block_primary_text(b)) for b in _flat_blocks)
            if _flat_char_count > _multiplier * _tree_char_count:
                logger.warning(
                    "RFC-029 D1: flat char count (%d) > %.1f× tree char count"
                    " (%d) for %s (script=%s) — preferring flat result",
                    _flat_char_count,
                    _multiplier,
                    _tree_char_count,
                    filename,
                    expected_script,
                )
                _vt_existing = state.gate_result if state.gate_result is not None else (state.ok, state.reason)
                finalize_gate_and_route(
                    state, _vt_existing, settings.flat_doc_routing,
                    recovery_method="flat_prefer_density",
                    recovery_succeeded=True,
                    force_route=Route.FLAT,
                    force_ok=False,
                )
        except Exception as _flat_exc:
            logger.warning(
                "RFC-029 D1: flat-prefer check failed for %s (%s); keeping tree",
                filename,
                _flat_exc,
            )

    async def _recover_landscape_reroute(
        self,
        state: ExtractionState,
        filename: str,
    ) -> None:
        """Recovery 7: landscape fallback re-routing. Mutates state."""
        if not (state.ok and settings.flat_doc_routing):
            return
        if any(
            isinstance(
                skip_reason_from_str(pr.get("skipped_reason")),
                SkipReason,
            )
            and skip_reason_from_str(pr.get("skipped_reason")) is SkipReason.LANDSCAPE_FALLBACK
            for pr in state.pic_results
        ):
            logger.warning(
                "RFC-035 D2: landscape fallback re-extraction triggered picture "
                "detection for %s — re-routing tree pass to flat-mixed",
                filename,
            )
            _vt_existing = state.gate_result if state.gate_result is not None else (state.ok, state.reason)
            finalize_gate_and_route(
                state, _vt_existing, settings.flat_doc_routing,
                recovery_method="landscape_reroute",
                recovery_succeeded=True,
                force_route=Route.FLAT,
                force_ok=False,
            )
