from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess

from ..script import AR_CHAR_RE as _AR_SCRIPT_RE
from .types import TessdataUnavailableError

logger = logging.getLogger(__name__)

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
    if not text.strip():
        return ["deu", "eng"]
    ar = len(_AR_SCRIPT_RE.findall(text))
    latin = len(_LATIN_LETTER_RE.findall(text))
    total = ar + latin
    if total == 0:
        return ["deu", "eng"]
    ar_ratio = ar / total
    if ar_ratio >= _AR_SCRIPT_MIN_RATIO:
        # Arabic-dominant: add 'eng' only when Latin is also materially present.
        return ["ara", "eng"] if latin / total >= _MIXED_SCRIPT_MIN_RATIO else ["ara"]
    if ar_ratio >= _AR_PRESENT_MIN_RATIO:
        # Latin-dominant but Arabic materially present (bilingual gazette) -> OCR both.
        return ["ara", "eng"]
    if _DE_HINT_RE.search(text):
        return ["deu", "eng"]
    return ["eng"]


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
            # No prefix configured -> trust the system tesseract install for
            # Latin languages.  For non-Latin languages, verify the traineddata
            # actually exists via a cached subprocess check — silently trusting
            # the system install for non-Latin scripts risks gibberish/empty
            # OCR output (Zone-3: closes the silent Latin fallback gap).
            if lang in _LATIN_LANGS:
                available.append(lang)
                continue
            # Non-Latin: verify system tessdata is actually present
            if lang in _system_tessdata_cache:
                if _system_tessdata_cache[lang]:
                    available.append(lang)
                else:
                    raise TessdataUnavailableError(
                        f"non-Latin tessdata missing: {lang} "
                        f"(no TESSDATA_PREFIX, system check failed)"
                    )
                continue
            # Probe: ask tesseract for its tessdata-prefix and check the file
            _found = False
            tess_bin = shutil.which("tesseract")
            if tess_bin:
                try:
                    result = subprocess.run(
                        [tess_bin, "--print-parameters"],
                        capture_output=True, text=True, timeout=5,
                    )
                    for _line in result.stdout.splitlines():
                        if _line.strip().startswith("tessdata_prefix"):
                            _sys_prefix = _line.split(maxsplit=1)[-1].strip()
                            if os.path.exists(
                                os.path.join(_sys_prefix, f"{lang}.traineddata")
                            ):
                                _found = True
                            break
                except (subprocess.TimeoutExpired, OSError):
                    pass
            _system_tessdata_cache[lang] = _found
            # lazy import to avoid circular dependency
            from ..metrics import TESSDATA_SYSTEM_CHECK_TOTAL
            TESSDATA_SYSTEM_CHECK_TOTAL.labels(
                lang=lang, result="found" if _found else "missing"
            ).inc()
            if _found:
                available.append(lang)
            else:
                logger.warning(
                    "non-Latin tessdata '%s' not found via system tesseract "
                    "(no TESSDATA_PREFIX configured); raising",
                    lang,
                )
                raise TessdataUnavailableError(
                    f"non-Latin tessdata missing: {lang} "
                    f"(no TESSDATA_PREFIX, system check failed)"
                )
            continue
        path = os.path.join(prefix, f"{lang}.traineddata")
        if os.path.exists(path):
            available.append(lang)
            continue
        if allow_dl and _try_download_tessdata(lang, prefix):
            available.append(lang)
        else:
            if lang not in _LATIN_LANGS:
                raise TessdataUnavailableError(
                    f"non-Latin tessdata missing: {lang} (prefix={prefix}, download={allow_dl})"
                )
            logger.warning(
                "tessdata for '%s' missing under %s (download=%s); dropping language",
                lang,
                prefix,
                allow_dl,
            )
    if not available:
        _had_non_latin = any(lang not in _LATIN_LANGS for lang in langs)
        if _had_non_latin:
            raise TessdataUnavailableError(
                f"no OCR languages available and request included non-Latin "
                f"scripts {[l for l in langs if l not in _LATIN_LANGS]}; "
                f"refusing Latin-only fallback"
            )
        logger.warning("no requested OCR languages available; falling back to deu,eng")
        from ..metrics import TESSDATA_LATIN_FALLBACK_TOTAL  # lazy to avoid circular import

        TESSDATA_LATIN_FALLBACK_TOTAL.inc()
        return ["deu", "eng"]
    return available


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
        return True
    except Exception as exc:
        logger.warning("tessdata fetch failed for '%s' (%s)", lang, exc)
        if os.path.exists(dest):
            with contextlib.suppress(OSError):
                os.unlink(dest)
        return False
