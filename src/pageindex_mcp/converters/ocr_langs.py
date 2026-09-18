from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
from contextvars import ContextVar

from ..obs import decision
from ..script import AR_CHAR_RE as _AR_SCRIPT_RE
from .types import TessdataUnavailableError

logger = logging.getLogger(__name__)

# RFC-046 D12 / task 12.5: per-document cap on the two repeating (per-lang)
# DEBUG... no -- these two are INFO but still per-lang within one call, so
# they are capped (registry: cap=8) to bound record volume on a
# many-language request. Same ContextVar-per-task pattern as helpers/garble.py
# (each asyncio task -- one per document -- gets its own copy at creation).
_DECISION_EMIT_COUNTS: ContextVar[dict] = ContextVar("ocr_langs_decision_emit_counts")


def _under_emit_cap(event: str, cap: int) -> bool:
    """True while *event* has fired fewer than *cap* times for this document.

    Never raises -- degrades to "skip this record" rather than break the
    document (R12.8 posture).
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


_TESSDATA_NO_PREFIX_CAP = 8
_TESSDATA_WITH_PREFIX_CAP = 8

# Zone-3: cache per-language system tessdata availability checks so the
# subprocess probe runs at most once per lang per process lifetime.
_system_tessdata_cache: dict[str, bool] = {}

_LATIN_LANGS = frozenset(
    {
        "afr",
        "cat",
        "ces",
        "dan",
        "deu",
        "eng",
        "est",
        "fin",
        "fra",
        "hrv",
        "hun",
        "ind",
        "isl",
        "ita",
        "lav",
        "lit",
        "msa",
        "nld",
        "nor",
        "pol",
        "por",
        "ron",
        "slk",
        "slv",
        "spa",
        "swe",
        "tur",
        "vie",
    }
)


# --- Fix 5: OCR language auto-detection + on-demand tessdata (RFC fizzy-forging-pearl) ---
# Deterministic, no model, no network for detection: classify by Unicode-script ratio.
_LATIN_LETTER_RE = re.compile(r"[A-Za-zÀ-ɏ]")
_DE_HINT_RE = re.compile(r"[äöüÄÖÜß]")
_AR_SCRIPT_MIN_RATIO = 0.15  # Arabic letters / all letters above which the doc is Arabic
_MIXED_SCRIPT_MIN_RATIO = 0.10  # Latin letters / all letters above which to add 'eng'
_AR_PRESENT_MIN_RATIO = 0.03  # any material Arabic -> include 'ara' (false-negative is costly)


def detect_ocr_langs(sample: str) -> list[str]:
    """Pick a Tesseract ``lang`` list from a text sample by Unicode-script ratio (Fix 5).

    Pure-Python, no dependency, no network (HR4). Returns Tesseract codes:
      * Arabic-dominant -> ['ara'] (or ['ara','eng'] when Latin is also materially
        present -- bilingual UAE gazettes);
      * German diacritics/ß present -> ['deu','eng'];
      * otherwise -> ['eng'].
    Empty / letterless input falls back to ['deu','eng'] to preserve the prior default.
    """
    text = sample or ""

    def _emit(choice: str, *, ar_count: int, latin_count: int, chosen: list[str]) -> None:
        _total = ar_count + latin_count
        decision(
            event="ocr_lang_selection",
            choice=choice,
            reason=choice,
            attrs={
                "sample_len": len(text),
                "ar_count": ar_count,
                "latin_count": latin_count,
                "ar_ratio": ar_count / _total if _total else 0.0,
                "latin_ratio": latin_count / _total if _total else 0.0,
                "chosen_langs": chosen,
            },
            logger=logger,
        )

    if not text.strip():
        _emit("empty_sample_default", ar_count=0, latin_count=0, chosen=["deu", "eng"])
        return ["deu", "eng"]
    ar = len(_AR_SCRIPT_RE.findall(text))
    latin = len(_LATIN_LETTER_RE.findall(text))
    total = ar + latin
    if total == 0:
        _emit("no_script_letters_default", ar_count=0, latin_count=0, chosen=["deu", "eng"])
        return ["deu", "eng"]
    ar_ratio = ar / total
    if ar_ratio >= _AR_SCRIPT_MIN_RATIO:
        # Arabic-dominant: add 'eng' only when Latin is also materially present.
        if latin / total >= _MIXED_SCRIPT_MIN_RATIO:
            _emit("arabic_with_latin", ar_count=ar, latin_count=latin, chosen=["ara", "eng"])
            return ["ara", "eng"]
        _emit("arabic_only", ar_count=ar, latin_count=latin, chosen=["ara"])
        return ["ara"]
    if ar_ratio >= _AR_PRESENT_MIN_RATIO:
        # Latin-dominant but Arabic materially present (bilingual gazette) -> OCR both.
        _emit("bilingual_gazette", ar_count=ar, latin_count=latin, chosen=["ara", "eng"])
        return ["ara", "eng"]
    if _DE_HINT_RE.search(text):
        _emit("german_diacritic_hint", ar_count=ar, latin_count=latin, chosen=["deu", "eng"])
        return ["deu", "eng"]
    _emit("english_fallthrough", ar_count=ar, latin_count=latin, chosen=["eng"])
    return ["eng"]


def _emit_no_prefix_decision(
    choice: str, reason: str, *, lang: str, cache_hit, probe_found
) -> None:
    """One INFO record for an `ensure_tessdata` no-prefix-branch outcome.

    Extracted out of ``ensure_tessdata`` so each of its 5 call sites is a
    single statement -- the function was already over the repo's
    max-statements/complexity gates before instrumentation; this keeps the
    R12.5/R12.9 guard-then-emit logic (cap check, isEnabledFor, decision())
    out of its statement count instead of adding ~5 lines per site.
    """
    if not (
        logger.isEnabledFor(logging.INFO)
        and _under_emit_cap("ocr_lang_availability_no_prefix", _TESSDATA_NO_PREFIX_CAP)
    ):
        return
    decision(
        event="ocr_lang_availability_no_prefix",
        choice=choice,
        reason=reason,
        attrs={"lang": lang, "cache_hit": cache_hit, "probe_found": probe_found},
        logger=logger,
    )


def _emit_with_prefix_decision(
    choice: str, reason: str, *, lang: str, allow_dl: bool, traineddata_present: bool
) -> None:
    """One INFO record for an `ensure_tessdata` with-prefix-branch outcome (see
    :func:`_emit_no_prefix_decision` for why this is split out)."""
    if not (
        logger.isEnabledFor(logging.INFO)
        and _under_emit_cap("ocr_lang_availability_with_prefix", _TESSDATA_WITH_PREFIX_CAP)
    ):
        return
    decision(
        event="ocr_lang_availability_with_prefix",
        choice=choice,
        reason=reason,
        attrs={"lang": lang, "allow_dl": allow_dl, "traineddata_present": traineddata_present},
        logger=logger,
    )


def _emit_final_fallback_decision(
    choice: str, reason: str, *, langs: list[str], had_non_latin: bool
) -> None:
    """One INFO record for `ensure_tessdata`'s end-of-request fallback (see
    :func:`_emit_no_prefix_decision` for why this is split out)."""
    decision(
        event="ocr_lang_final_fallback",
        choice=choice,
        reason=reason,
        attrs={"requested_langs": list(langs), "had_non_latin": had_non_latin},
        logger=logger,
    )


def _probe_system_tessdata(lang: str) -> bool:
    """Ask the system ``tesseract`` binary whether ``<lang>.traineddata`` is
    installed. Extracted from ``ensure_tessdata`` (behaviour-neutral) so the
    no-prefix branch's own statement count leaves room for its decision
    records; identical subprocess/parsing logic, just out-of-line."""
    tess_bin = shutil.which("tesseract")
    if not tess_bin:
        return False
    try:
        result = subprocess.run(
            [tess_bin, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _probe_output = result.stdout + result.stderr
        _m = re.search(r'in "([^"]+)"', _probe_output)
        if _m:
            _sys_prefix = _m.group(1)
            return os.path.exists(os.path.join(_sys_prefix, f"{lang}.traineddata"))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def ensure_tessdata(langs: list[str]) -> list[str]:
    """Ensure ``<lang>.traineddata`` is available; return the usable subset (Fix 5).

    For each requested language, check ``TESSDATA_PREFIX`` for the traineddata file.
    Missing files are fetched from the official tessdata repo ONLY when
    ``TESSDATA_ALLOW_DOWNLOAD=1`` (egress-limited workers instead rely on PRE-BAKED
    traineddata in the image, mirroring the DOCLING_ARTIFACTS_PATH pre-bake). A
    missing Latin-script language is dropped (silent degrade is safe); a missing
    non-Latin-script language raises ``TessdataUnavailableError`` instead of being
    silently dropped, since that would silently degrade OCR to gibberish/empty
    output for scripts Latin OCR cannot read. If nothing remains after dropping
    Latin languages we fall back to ['deu','eng'] so OCR still runs. tessdata is
    data, not AGPL code (HR4)."""
    prefix = os.getenv("TESSDATA_PREFIX", "").strip()
    allow_dl = os.getenv("TESSDATA_ALLOW_DOWNLOAD", "0").strip().lower() in ("1", "true", "yes")
    available: list[str] = []
    for lang in langs:
        if not prefix:
            _ensure_lang_no_prefix(lang, available)
            continue
        _ensure_lang_with_prefix(lang, prefix, allow_dl, available)
    if not available:
        return _ensure_tessdata_empty_fallback(langs)
    return available


def _ensure_lang_no_prefix(lang: str, available: list[str]) -> None:
    """The ``not prefix`` branch of :func:`ensure_tessdata`'s per-lang loop,
    extracted (behaviour-neutral) so the decision records added for R12.5
    don't push the parent over the repo's statement/complexity gates.

    No prefix configured -> trust the system tesseract install for Latin
    languages. For non-Latin languages, verify the traineddata actually
    exists via a cached subprocess check — silently trusting the system
    install for non-Latin scripts risks gibberish/empty OCR output
    (Zone-3: closes the silent Latin fallback gap).
    """
    if lang in _LATIN_LANGS:
        _emit_no_prefix_decision(
            "latin_trusted",
            "latin_script_trusts_system_install",
            lang=lang,
            cache_hit=None,
            probe_found=None,
        )
        available.append(lang)
        return
    if lang in _system_tessdata_cache:
        if _system_tessdata_cache[lang]:
            _emit_no_prefix_decision(
                "non_latin_cached_available",
                "cache_hit_available",
                lang=lang,
                cache_hit=True,
                probe_found=True,
            )
            available.append(lang)
            return
        _emit_no_prefix_decision(
            "non_latin_cached_raise",
            "cache_hit_unavailable",
            lang=lang,
            cache_hit=True,
            probe_found=False,
        )
        raise TessdataUnavailableError(
            f"non-Latin tessdata missing: {lang} (no TESSDATA_PREFIX, system check failed)"
        )
    # Probe: ask tesseract for its tessdata dir and check the file.
    _found = _probe_system_tessdata(lang)
    _system_tessdata_cache[lang] = _found
    # lazy import to avoid circular dependency
    from ..metrics import TESSDATA_SYSTEM_CHECK_TOTAL

    TESSDATA_SYSTEM_CHECK_TOTAL.labels(lang=lang, result="found" if _found else "missing").inc()
    if _found:
        _emit_no_prefix_decision(
            "non_latin_probed_found",
            "subprocess_probe_found",
            lang=lang,
            cache_hit=False,
            probe_found=True,
        )
        available.append(lang)
        return
    _emit_no_prefix_decision(
        "non_latin_probed_missing_raise",
        "subprocess_probe_missing",
        lang=lang,
        cache_hit=False,
        probe_found=False,
    )
    logger.warning(
        "non-Latin tessdata '%s' not found via system tesseract "
        "(no TESSDATA_PREFIX configured); raising",
        lang,
    )
    raise TessdataUnavailableError(
        f"non-Latin tessdata missing: {lang} (no TESSDATA_PREFIX, system check failed)"
    )


def _ensure_lang_with_prefix(lang: str, prefix: str, allow_dl: bool, available: list[str]) -> None:
    """The ``prefix``-configured branch of :func:`ensure_tessdata`'s per-lang
    loop (see :func:`_ensure_lang_no_prefix` for why this is split out)."""
    path = os.path.join(prefix, f"{lang}.traineddata")
    if os.path.exists(path):
        _emit_with_prefix_decision(
            "prefix_path_exists",
            "traineddata_present_under_prefix",
            lang=lang,
            allow_dl=allow_dl,
            traineddata_present=True,
        )
        available.append(lang)
        return
    if allow_dl and _try_download_tessdata(lang, prefix):
        _emit_with_prefix_decision(
            "prefix_download_success",
            "download_succeeded",
            lang=lang,
            allow_dl=allow_dl,
            traineddata_present=True,
        )
        available.append(lang)
        return
    if lang not in _LATIN_LANGS:
        _emit_with_prefix_decision(
            "prefix_missing_non_latin_raise",
            "non_latin_unavailable_under_prefix",
            lang=lang,
            allow_dl=allow_dl,
            traineddata_present=False,
        )
        raise TessdataUnavailableError(
            f"non-Latin tessdata missing: {lang} (prefix={prefix}, download={allow_dl})"
        )
    _emit_with_prefix_decision(
        "prefix_missing_latin_dropped",
        "latin_unavailable_dropped",
        lang=lang,
        allow_dl=allow_dl,
        traineddata_present=False,
    )
    logger.warning(
        "tessdata for '%s' missing under %s (download=%s); dropping language",
        lang,
        prefix,
        allow_dl,
    )


def _ensure_tessdata_empty_fallback(langs: list[str]) -> list[str]:
    """``ensure_tessdata``'s end-of-request fallback when nothing survived
    the per-lang loop (see :func:`_ensure_lang_no_prefix` for why this is
    split out)."""
    _had_non_latin = any(lang not in _LATIN_LANGS for lang in langs)
    if _had_non_latin:
        _non_latin_langs = [lang for lang in langs if lang not in _LATIN_LANGS]
        _emit_final_fallback_decision(
            "raise_non_latin_requested",
            "no_available_langs_non_latin_requested",
            langs=langs,
            had_non_latin=True,
        )
        raise TessdataUnavailableError(
            f"no OCR languages available and request included non-Latin "
            f"scripts {_non_latin_langs}; refusing Latin-only fallback"
        )
    _emit_final_fallback_decision(
        "fallback_deu_eng",
        "no_available_langs_latin_only",
        langs=langs,
        had_non_latin=False,
    )
    logger.warning("no requested OCR languages available; falling back to deu,eng")
    from ..metrics import TESSDATA_LATIN_FALLBACK_TOTAL  # lazy to avoid circular import

    TESSDATA_LATIN_FALLBACK_TOTAL.inc()
    return ["deu", "eng"]


_TESSDATA_MAX_BYTES = 100 * 1024 * 1024  # 100 MB cap (RFC-009 D5 / Property 5)
_TESSDATA_CHUNK_BYTES = 1024 * 1024  # 1 MB chunked read
_TESSDATA_TIMEOUT_S = 30


def _try_download_tessdata(lang: str, prefix: str) -> bool:
    """Best-effort fetch of one traineddata file from the official repo. Never raises.

    Hardened per RFC-009 D5 (ISS-14): bounded by a 30s connection timeout and a
    100 MB total-size cap, both enforced via a chunked read loop. Any failure
    (timeout, oversize, network/HTTP error) cleans up the partial file at
    ``dest`` before returning False (Design Property 5: Tessdata download bounded).
    """
    import urllib.request

    url = f"https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"
    dest = os.path.join(prefix, f"{lang}.traineddata")
    try:
        os.makedirs(prefix, exist_ok=True)
        total = 0
        with (
            urllib.request.urlopen(url, timeout=_TESSDATA_TIMEOUT_S) as resp,
            open(dest, "wb") as f,
        ):
            while True:
                chunk = resp.read(_TESSDATA_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > _TESSDATA_MAX_BYTES:
                    raise RuntimeError(
                        f"tessdata download for '{lang}' exceeded {_TESSDATA_MAX_BYTES} byte cap"
                    )
                f.write(chunk)
        logger.info("fetched tessdata for '%s' into %s (%d bytes)", lang, prefix, total)
        decision(
            event="tessdata_download_result",
            choice="success",
            reason="download_completed",
            attrs={"lang": lang, "bytes_downloaded": total, "failure_exc_type": None},
            logger=logger,
        )
        return True
    except Exception as exc:
        logger.warning("tessdata fetch failed for '%s' (%s)", lang, exc)
        decision(
            event="tessdata_download_result",
            choice="failed",
            reason="download_exception",
            attrs={
                "lang": lang,
                # R12.7: type name ONLY, never str(exc) -- the message may
                # embed a path or URL fragment.
                "failure_exc_type": type(exc).__name__,
            },
            logger=logger,
        )
        if os.path.exists(dest):
            with contextlib.suppress(OSError):
                os.unlink(dest)
        return False
