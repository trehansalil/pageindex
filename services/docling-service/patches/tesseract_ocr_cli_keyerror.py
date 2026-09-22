"""Monkey-patch docling's TesseractOcrCliModel._run_tesseract to guard
against Tesseract returning TSV output without a 'text' column (happens on
blank / purely graphical / heavily degraded pages).

Applied at container startup via app.py import.
Upstream: https://github.com/docling-project/docling — unfixed as of v2.96.0.
"""

import logging

import pandas as pd

_log = logging.getLogger(__name__)


def apply() -> None:
    from docling.models.stages.ocr.tesseract_ocr_cli_model import (
        TesseractOcrCliModel,
    )

    _original_run = TesseractOcrCliModel._run_tesseract

    def _safe_run_tesseract(self, fname, df_osd):  # type: ignore[no-untyped-def]
        df = _original_run(self, fname, df_osd)
        if "text" not in df.columns:
            _log.warning(
                "Tesseract returned no 'text' column for %s — "
                "returning empty DataFrame (page likely blank/graphical)",
                fname,
            )
            return pd.DataFrame(columns=df.columns.tolist() + ["text"])
        return df

    TesseractOcrCliModel._run_tesseract = _safe_run_tesseract  # type: ignore[assignment]
    _log.info("Patched TesseractOcrCliModel._run_tesseract (KeyError guard)")
