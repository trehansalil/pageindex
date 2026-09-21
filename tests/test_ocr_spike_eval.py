"""RED-phase tests for RFC-046 Wave 2 / D1 (tasks 2.1-2.4).

Covers scripts/ocr_spike_eval.py, which is a standalone script (not a
package module) invoked directly via `python scripts/ocr_spike_eval.py`.
These tests import it as `scripts.ocr_spike_eval` (namespace package,
repo root on sys.path via tests/__init__.py) and mock at the httpx
boundary — no real network calls are made anywhere in this file.

Scope: 2.1 (hard-fail on unreachable OCR engines), 2.2 (production
language-detection path), 2.3 (comparison key labelling), 2.4 (report
header). Task 2.5 (live engine services) is explicitly out of scope and
nothing here starts a service or touches the network.

This is the RED step of TDD: no production code (scripts/ocr_spike_eval.py
or anything under src/) is modified here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import json

import httpx
import pytest

import scripts.ocr_spike_eval as ocr_eval
from pageindex_mcp.converters.ocr_langs import detect_ocr_langs


# ---------------------------------------------------------------------------
# 2.1 — unreachable engine endpoints must be a hard error
# ---------------------------------------------------------------------------

# (runner_name, kwargs, endpoint_attr) — each of the six httpx.post call
# sites named in the task (lines 346, 386, 422, 453, 491, 522).
_PDF_RUNNERS = [
    ("run_paddleocr_on_pdf", {"max_pages": 3}, "PADDLEOCR_URL"),
    ("run_paddleocr_vl_on_pdf", {"max_pages": 3}, "PADDLEOCR_VL_URL"),
    ("run_surya_on_pdf", {"max_pages": 3}, "SURYA_URL"),
]
_IMAGE_RUNNERS = [
    ("run_paddleocr_on_image", {}, "PADDLEOCR_URL"),
    ("run_paddleocr_vl_on_image", {}, "PADDLEOCR_VL_URL"),
    ("run_surya_on_image", {}, "SURYA_URL"),
]


@pytest.mark.parametrize("fn_name, extra_kwargs, url_attr", _PDF_RUNNERS)
def test_pdf_runner_raises_when_engine_connection_is_refused(
    tmp_path, fn_name, extra_kwargs, url_attr
):
    """A connection failure to a PDF-OCR engine must propagate, not become an empty result."""
    # Arrange
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes for open()")
    fn = getattr(ocr_eval, fn_name)
    refused = httpx.ConnectError("Connection refused")

    # Act / Assert
    with patch("httpx.post", side_effect=refused):
        with pytest.raises(Exception) as exc_info:
            fn(str(pdf_path), **extra_kwargs)

    # The old (defective) behaviour returned [{"error": "..."}] instead of
    # raising — guard against a test that "passes" only because some other,
    # unrelated exception (e.g. a local file/type error) was raised instead.
    assert not isinstance(exc_info.value, (TypeError, FileNotFoundError))


@pytest.mark.parametrize("fn_name, extra_kwargs, url_attr", _IMAGE_RUNNERS)
def test_image_runner_raises_when_engine_connection_is_refused(
    tmp_path, fn_name, extra_kwargs, url_attr
):
    """A connection failure to an image-OCR engine must propagate, not become an empty result."""
    # Arrange
    img_path = tmp_path / "doc.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg bytes")
    fn = getattr(ocr_eval, fn_name)
    refused = httpx.ConnectError("Connection refused")

    # Act / Assert
    with patch("httpx.post", side_effect=refused):
        with pytest.raises(Exception) as exc_info:
            fn(str(img_path), **extra_kwargs)

    assert not isinstance(exc_info.value, (TypeError, FileNotFoundError))


@pytest.mark.parametrize(
    "fn_name, extra_kwargs",
    [
        ("run_paddleocr_on_pdf", {"max_pages": 3}),
        ("run_paddleocr_on_image", {}),
        ("run_paddleocr_vl_on_pdf", {"max_pages": 3}),
        ("run_paddleocr_vl_on_image", {}),
        ("run_surya_on_pdf", {"max_pages": 3}),
        ("run_surya_on_image", {}),
    ],
)
def test_runner_no_longer_returns_swallowed_error_dict_on_connection_failure(
    tmp_path, fn_name, extra_kwargs
):
    """The defective shape — a normal-looking [{"error": ...}] result — must be gone.

    This is the exact shape that voided the RFC-036 D7 negative result: a
    connection failure that LOOKS like a valid (if empty) OCR result, so
    downstream aggregation silently treats "engine down" as "zero chars".
    """
    # Arrange
    is_pdf = "pdf" in fn_name
    path = tmp_path / ("doc.pdf" if is_pdf else "doc.jpg")
    path.write_bytes(b"fake bytes")
    fn = getattr(ocr_eval, fn_name)
    refused = httpx.ConnectError("Connection refused")

    # Act
    with patch("httpx.post", side_effect=refused):
        try:
            result = fn(str(path), **extra_kwargs)
        except Exception:
            result = None  # raising is the desired behaviour; nothing more to check

    # Assert: if it didn't raise, it must not be the old swallowed-error shape either.
    if result is not None:
        assert not (
            isinstance(result, list)
            and len(result) == 1
            and isinstance(result[0], dict)
            and "error" in result[0]
        ), f"{fn_name} swallowed the connection failure into {result!r} instead of raising"


def test_main_exits_nonzero_when_paddleocr_endpoint_is_unreachable(tmp_path, capsys):
    """main() must hard-fail, naming the engine and the endpoint, when an engine is down."""
    # Arrange
    doc_store = tmp_path / "doc_store"
    doc_store.mkdir()
    out_dir = tmp_path / "out"
    argv = [
        "ocr_spike_eval.py",
        "--doc-store",
        str(doc_store),
        "--out-dir",
        str(out_dir),
        # deliberately do NOT pass --skip-paddleocr: the endpoint is simply down
    ]
    refused = httpx.ConnectError("Connection refused")

    # Act / Assert
    with patch.object(sys, "argv", argv), patch("httpx.get", side_effect=refused):
        with pytest.raises(SystemExit) as exc_info:
            ocr_eval.main()

    assert exc_info.value.code not in (0, None)
    output = capsys.readouterr()
    combined = (output.out + output.err).lower()
    assert "paddleocr" in combined
    assert ocr_eval.PADDLEOCR_URL.lower() in combined


def test_main_exits_nonzero_when_surya_endpoint_is_unreachable(tmp_path, capsys):
    """Same hard-fail contract for the Surya health check (line 811)."""
    # Arrange
    doc_store = tmp_path / "doc_store"
    doc_store.mkdir()
    out_dir = tmp_path / "out"
    argv = [
        "ocr_spike_eval.py",
        "--doc-store",
        str(doc_store),
        "--out-dir",
        str(out_dir),
        "--skip-paddleocr",
        "--skip-paddleocr-vl",
        # deliberately do NOT pass --skip-surya
    ]
    refused = httpx.ConnectError("Connection refused")

    # Act / Assert
    with patch.object(sys, "argv", argv), patch("httpx.get", side_effect=refused):
        with pytest.raises(SystemExit) as exc_info:
            ocr_eval.main()

    assert exc_info.value.code not in (0, None)
    output = capsys.readouterr()
    combined = (output.out + output.err).lower()
    assert "surya" in combined
    assert ocr_eval.SURYA_URL.lower() in combined


def test_main_does_not_hard_fail_when_engine_is_explicitly_skipped(tmp_path):
    """--skip-paddleocr means the user chose to skip it — that is NOT an unreachable-engine error.

    This is the boundary the recon flagged: coercing "unreachable" and "user
    skipped" into the same flag is exactly the defect. Passing --skip-paddleocr
    explicitly must never be treated as if the engine failed to connect, so an
    unreachable-but-skipped engine must not, by itself, cause a non-zero exit.
    """
    # Arrange
    doc_store = tmp_path / "doc_store"
    doc_store.mkdir()
    out_dir = tmp_path / "out"
    argv = [
        "ocr_spike_eval.py",
        "--doc-store",
        str(doc_store),
        "--out-dir",
        str(out_dir),
        "--skip-paddleocr",
        "--skip-paddleocr-vl",
        "--skip-surya",
    ]

    # Act — httpx.get should not even be needed since every engine is skipped,
    # but patch it to raise anyway so this fails loudly if skip is ignored.
    with (
        patch.object(sys, "argv", argv),
        patch("httpx.get", side_effect=httpx.ConnectError("refused")),
    ):
        try:
            ocr_eval.main()
        except SystemExit as exc:
            pytest.fail(
                f"main() exited with code {exc.code} even though every remote "
                "engine was explicitly skipped via --skip-*"
            )


def test_per_document_local_failure_does_not_abort_the_whole_run(tmp_path):
    """A single corrupt/unreadable document must not abort the 25-document run.

    This is the boundary case recon identified: Tesseract's own local,
    per-document failures (e.g. a corrupt PDF that PyMuPDF cannot open) are
    NOT the "unreachable engine" defect 2.1 targets, and must keep being
    caught so the rest of the corpus still gets processed.
    """
    # Arrange
    doc_store = tmp_path / "doc_store"
    doc_store.mkdir()
    (doc_store / "corrupt.pdf").write_bytes(b"not actually a pdf")
    out_dir = tmp_path / "out"
    argv = [
        "ocr_spike_eval.py",
        "--doc-store",
        str(doc_store),
        "--out-dir",
        str(out_dir),
        "--skip-paddleocr",
        "--skip-paddleocr-vl",
        "--skip-surya",
    ]

    # Act / Assert — must complete (not raise, not sys.exit) despite the
    # corrupt document; a local per-document failure is not an engine-down
    # condition and must be caught, not propagated.
    with patch.object(sys, "argv", argv):
        try:
            ocr_eval.main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                pytest.fail(
                    f"a single corrupt document aborted the whole run with exit code {exc.code}"
                )


# ---------------------------------------------------------------------------
# 2.2 — harness must derive Tesseract languages via production's
#        detect_ocr_langs, not the private _tess_langs_from_detected map
# ---------------------------------------------------------------------------

# An Arabic filename long enough that the Latin letters contributed by the
# ".jpg" extension fall below detect_ocr_langs' _MIXED_SCRIPT_MIN_RATIO
# (0.10), so production classifies it as Arabic-only ['ara'], while the
# harness's private map always appends 'eng' regardless. This is a real,
# verified disagreement (not merely a hypothetical one):
#   classify_document_langs(name)        -> ['ar', 'en']
#   _tess_langs_from_detected(['ar','en']) -> ['ara', 'eng']   (harness, today)
#   detect_ocr_langs(name)                 -> ['ara']           (production)
_ARABIC_DOMINANT_FILENAME = "شهادة_ميلاد_وثيقة_رسمية_صادرة_من_وزارة_الداخلية_للمواطن.jpg"


def _old_tess_langs_from_detected(detected_langs: list[str]) -> list[str]:
    """Reimplementation of the now-deleted private map, for this fixture check only.

    Task 2.2 requires deleting scripts.ocr_spike_eval._tess_langs_from_detected
    (replaced by detect_ocr_langs + ensure_tessdata), so it is no longer
    importable from the module. This inlines its old logic verbatim so this
    sanity check can still pin the historical disagreement it documents,
    without keeping the deleted function around as a zombie import target.
    """
    tess_map = {"ar": "ara", "de": "deu", "en": "eng"}
    tess_langs = list(dict.fromkeys(tess_map.get(l, "eng") for l in detected_langs))
    if "eng" not in tess_langs:
        tess_langs.append("eng")
    return tess_langs


def test_production_and_harness_language_selection_disagree_on_arabic_filename():
    """Pins the actual disagreement this task exists to fix (sanity check on the fixture)."""
    # Arrange
    detected = ocr_eval.classify_document_langs(_ARABIC_DOMINANT_FILENAME)

    # Act
    harness_langs = _old_tess_langs_from_detected(detected)
    production_langs = detect_ocr_langs(_ARABIC_DOMINANT_FILENAME)

    # Assert — if this ever fails, the two paths have converged and the
    # tests below must be revisited (see deviations note).
    assert harness_langs == ["ara", "eng"]
    assert production_langs == ["ara"]
    assert harness_langs != production_langs


def test_run_tesseract_on_image_uses_production_language_detection_not_the_private_map(
    tmp_path,
):
    """run_tesseract_on_image must select languages the way production does.

    For an Arabic-dominant filename, production's detect_ocr_langs (fed the
    same sample the harness already has available — the filename) returns
    ['ara'] alone; the private map (_tess_langs_from_detected) always
    appends 'eng'. The langs actually handed to _tesseract_ocr_image must
    match production, not the private map's always-append-eng behaviour.
    """
    # Arrange
    img_path = tmp_path / _ARABIC_DOMINANT_FILENAME
    img_path.write_bytes(b"fake image bytes")
    captured_langs = {}

    def fake_tesseract_ocr_image(path, langs):
        captured_langs["langs"] = list(langs)
        return "بيانات نصية"

    # Act
    with (
        patch(
            "pageindex_mcp.converters.pictures._tesseract_ocr_image",
            side_effect=fake_tesseract_ocr_image,
        ),
        # ensure_tessdata shells out to `tesseract --list-langs` when
        # TESSDATA_PREFIX is unset, which makes this assertion depend on which
        # language packs the host happens to carry. Stub it so the test pins
        # detect_ocr_langs' contribution and nothing else.
        patch.object(ocr_eval, "ensure_tessdata", side_effect=lambda langs: list(langs)),
    ):
        ocr_eval.run_tesseract_on_image(str(img_path), detected_langs=None)

    # Assert
    assert "langs" in captured_langs, "the mocked _tesseract_ocr_image was never called"
    assert captured_langs["langs"] == ["ara"], (
        f"expected production's detect_ocr_langs result ['ara'], got "
        f"{captured_langs['langs']!r} — looks like the private "
        "_tess_langs_from_detected map (which always appends 'eng') is still in use"
    )


# ---------------------------------------------------------------------------
# 2.3 — compare_results must label the second engine honestly, and the
#        report consumer must read whatever label it actually uses
# ---------------------------------------------------------------------------

_TESS_PAGES = [{"page_index": 0, "text": "hello", "char_count": 5, "elapsed_s": 1.0}]
_SURYA_PAGES = [
    {
        "page_index": 0,
        "text": "hello world",
        "char_count": 11,
        "elapsed_s": 2.0,
        "confidence": 0.9,
    }
]


def test_compare_results_does_not_label_surya_data_as_paddleocr():
    """comparison_surya's keys must not claim the data is PaddleOCR's.

    Today compare_results hardcodes 'paddleocr_*' key names and a literal
    'paddleocr' winner string regardless of which second engine was actually
    passed in, so doc['comparison_surya']['paddleocr_total_chars'] silently
    reports Surya's char count under PaddleOCR's name. The fix must stop
    naming the (surya) data after paddleocr.
    """
    # Arrange / Act
    result = ocr_eval.compare_results(_TESS_PAGES, _SURYA_PAGES)

    # Assert — no key or value anywhere in the comparison should say
    # "paddleocr" when the second engine being compared is surya.
    offending_keys = [k for k in result if "paddleocr" in k]
    offending_values = [v for v in result.values() if isinstance(v, str) and "paddleocr" in v]
    assert not offending_keys, (
        f"compare_results labelled surya's data with paddleocr-named keys: {offending_keys!r}"
    )
    assert not offending_values, (
        f"compare_results reported a 'paddleocr' winner for a surya comparison: {offending_values!r}"
    )


def test_write_human_report_reads_surya_comparison_under_its_own_labels(tmp_path):
    """The line-691 consumer must read whatever honest key names compare_results now emits.

    This simulates the CORRECTED producer output (generic, engine-neutral
    key names, since compare_results has no engine-name parameter to draw
    a 'surya_*' prefix from) and checks that write_human_report's Surya
    columns actually surface the real numbers rather than falling back to
    the '-'/0 placeholders it uses when a key is missing.
    """
    # Arrange
    summary = {
        "total_documents": 1,
        "overall": {"paddleocr_wins": 0, "tesseract_wins": 0, "recommendation": "n/a"},
        "pdf_inspector": {},
        "by_language": {},
    }
    comparison_surya_honest = {
        "tesseract_total_chars": 5,
        "other_total_chars": 4242,
        "char_diff": 4237,
        "char_ratio": 848.4,
        "tesseract_total_time_s": 1.0,
        "other_total_time_s": 2.0,
        "other_avg_confidence": 0.9,
        "other_low_conf_pages": 0,
        "winner_by_chars": "other",
        "winner_by_speed": "tesseract",
    }
    doc = {
        "filename": "doc.pdf",
        "detected_langs": ["en"],
        "file_type": "pdf",
        "pdf_inspector": {},
        "comparison": {
            "tesseract_total_chars": 5,
            "paddleocr_total_chars": 5,
            "paddleocr_avg_confidence": 0.9,
            "winner_by_chars": "tie",
        },
        "comparison_vl": {"note": "one or both engines skipped/errored"},
        "comparison_surya": comparison_surya_honest,
    }
    out_path = tmp_path / "eval_report.md"

    # Act
    ocr_eval.write_human_report(summary, [doc], out_path)
    report_text = out_path.read_text(encoding="utf-8")

    # Assert — the real surya char count (4242) must show up somewhere in
    # the per-document detail row; today's consumer reads the now-removed
    # 'paddleocr_total_chars' key and would instead render the '-' fallback.
    assert "4242" in report_text, (
        "write_human_report did not surface Surya's char count from the "
        "corrected (non-paddleocr-labelled) comparison_surya keys — the "
        "consumer at line 691 still reads the old paddleocr_* names"
    )


# ---------------------------------------------------------------------------
# 2.4 — the existing eval_report.md must carry an unverified-numbers header
# ---------------------------------------------------------------------------

_REPORT_PATH = Path("agents/spikes/ocr_eval_rfc046/eval_report.md")


def test_eval_report_has_an_unverified_numbers_header():
    """eval_report.md must state measurement caveats (yield != accuracy)."""
    text = _REPORT_PATH.read_text(encoding="utf-8")
    head = text[:2000].lower()

    assert "character yield" in head or "not accuracy" in head, (
        "eval_report.md must caveat that it measures character yield, not accuracy"
    )


# ---------------------------------------------------------------------------
# 2.1 (second half) — a REACHABLE but broken engine must also fail loudly.
#
# The first round of 2.1 hardened only *unreachable* engines: every test above
# drives the failure with httpx.ConnectError. But `resp.json()` sits inside the
# same try block, and json.JSONDecodeError is a ValueError, not an httpx error.
# So a crashed engine returning 200 with a truncated body, or a proxy answering
# 200 for a dead upstream, escapes EngineUnreachableError and is recorded as
# [{"error": ...}] — the exact shape task 2.1 exists to delete. The run then
# exits 0 and the artifact looks like a quality result.
#
# _check_engine_health already catches (httpx.HTTPError, ValueError); these
# tests pin the six data-producing runners to the same contract.
# ---------------------------------------------------------------------------


class _UnparseableResponse:
    """A 200 whose body is not JSON — a crashed or truncated engine response."""

    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        raise json.JSONDecodeError("Expecting value", "", 0)


@pytest.mark.parametrize("fn_name, extra_kwargs, url_attr", _PDF_RUNNERS)
def test_pdf_runner_raises_when_a_reachable_engine_returns_an_unparseable_body(
    tmp_path, fn_name, extra_kwargs, url_attr
):
    """A 200 with a non-JSON body must raise EngineUnreachableError, not leak a ValueError."""
    # Arrange
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes for open()")
    fn = getattr(ocr_eval, fn_name)

    # Act / Assert
    with patch("httpx.post", return_value=_UnparseableResponse()):
        with pytest.raises(ocr_eval.EngineUnreachableError) as excinfo:
            fn(str(pdf_path), **extra_kwargs)

    assert getattr(ocr_eval, url_attr) in str(excinfo.value)


@pytest.mark.parametrize("fn_name, extra_kwargs, url_attr", _IMAGE_RUNNERS)
def test_image_runner_raises_when_a_reachable_engine_returns_an_unparseable_body(
    tmp_path, fn_name, extra_kwargs, url_attr
):
    """The image runners carry the same contract as the PDF runners."""
    # Arrange
    img_path = tmp_path / "scan.png"
    img_path.write_bytes(b"fake image bytes")
    fn = getattr(ocr_eval, fn_name)

    # Act / Assert
    with patch("httpx.post", return_value=_UnparseableResponse()):
        with pytest.raises(ocr_eval.EngineUnreachableError) as excinfo:
            fn(str(img_path), **extra_kwargs)

    assert getattr(ocr_eval, url_attr) in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2.2 (second half) — production's language path is the SAMPLE as well as the
# function. pictures.py:1089 unions detect_ocr_langs(filename) with
# detect_ocr_langs(extracted_text); feeding the filename alone reproduces only
# half of it, and loses 'deu' on German documents whose filenames are Latin.
# ---------------------------------------------------------------------------

_GERMAN_TEXT_SAMPLE = (
    "Allgemeine Versicherungsbedingungen für die Haftpflichtversicherung. "
    "Der Versicherungsschutz erstreckt sich auf gesetzliche Schadenersatzansprüche "
    "und schließt Ansprüche aus Tätigkeiten für Dritte ausdrücklich ein."
)


def test_select_tesseract_langs_unions_the_filename_with_the_text_sample():
    """German content behind an English filename must still select 'deu'.

    This is production's contract at pictures.py:1088-1094 — the union of the
    filename's languages and the extracted text's, not the filename alone.
    """
    # Arrange
    filename = "Policy-Terms-2025.pdf"
    english_sample = "This policy sets out the terms of cover and the claims process."
    with patch.object(ocr_eval, "ensure_tessdata", side_effect=lambda langs: list(langs)):
        # Act
        with_german = ocr_eval._select_tesseract_langs(filename, _GERMAN_TEXT_SAMPLE)
        with_english = ocr_eval._select_tesseract_langs(filename, english_sample)

    # Assert — the same filename, two samples: only the sample can explain the
    # difference, so this pins the union rather than the filename's own result.
    assert "deu" not in with_english, (
        f"precondition: an English sample must not yield German, got {with_english!r}"
    )
    assert "deu" in with_german, (
        f"expected the German text sample to contribute 'deu', got {with_german!r} — "
        "the harness is still deriving languages from the filename alone and so "
        "does not measure production's language path"
    )


def test_select_tesseract_langs_uses_the_filename_alone_when_there_is_no_text_sample():
    """An image input has no text to union, and production uses the filename alone.

    images.py:134 and indexer.py:893 call detect_ocr_langs(filename) with no
    union. Unioning an absent sample would be worse than useless here:
    detect_ocr_langs("") returns ['deu','eng'] as an empty-input fallback, so
    every Arabic-only document would silently acquire German.
    """
    # Arrange
    arabic_filename = _ARABIC_DOMINANT_FILENAME

    # Act
    with patch.object(ocr_eval, "ensure_tessdata", side_effect=lambda langs: list(langs)):
        langs = ocr_eval._select_tesseract_langs(arabic_filename)

    # Assert
    assert langs == detect_ocr_langs(arabic_filename)
    assert "deu" not in langs, (
        f"got {langs!r} — an empty text sample is being unioned in, and its "
        "['deu','eng'] fallback has polluted an Arabic-only selection"
    )


def test_text_layer_detection_returns_the_sample_it_analysed(tmp_path):
    """detect_lang_from_text_layer must hand back the text, not only ISO codes.

    Phase 0 already extracts this text; discarding it is why the language
    union above had nothing to union with.
    """
    # Arrange
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "german.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), _GERMAN_TEXT_SAMPLE[:200])
    doc.save(str(pdf_path))
    doc.close()

    # Act
    info = ocr_eval.detect_lang_from_text_layer(pdf_path)

    # Assert
    assert info.get("text_sample"), (
        f"no text_sample in {sorted(info)} — the extracted text is still being "
        "discarded after language classification"
    )
