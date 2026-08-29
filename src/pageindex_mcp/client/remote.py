"""CustomPageIndexClient — remote conversion (Docling service)."""

from __future__ import annotations

import importlib
import logging
import os

from ..config import (
    CURRENT_PIPELINE_VERSION,
    RemoteVersionSkewError,
    ZDRComplianceError,
    pipeline_config,
    require_zdr_compliance,
    settings,
)
from ..metrics import (
    DOCLING_VERSION_SKEW,
    HR3_EGRESS_BLOCKED_TOTAL,
)

logger = logging.getLogger(__name__)

# RFC-034 D1: cached remote Docling /version response, fetched once per process.
_remote_docling_version: dict | None = None
# Zone (converter-chain fallback): set to the remote ``pipeline_version`` when it
# was observed to be BEHIND the local ``CURRENT_PIPELINE_VERSION``.  Sticky for
# the process lifetime because ``_remote_docling_version`` itself is fetched only
# once — this lets the enforce-mode block re-evaluate on every conversion instead
# of only on the call that happened to perform the fetch.  ``None`` means "no
# skew observed" (including "/version was unreachable", which stays warn-only).
_remote_pipeline_version_behind: int | None = None
# Zone-7: BUILD_SHA is the convention services/docling-service's CI/Dockerfile
# already use; CLIENT_BUILD_SHA was a never-wired legacy name that left this
# permanently "unknown". Prefer BUILD_SHA, fall back to the legacy name.
_CLIENT_BUILD_SHA = os.environ.get("BUILD_SHA") or os.environ.get("CLIENT_BUILD_SHA", "unknown")


async def _check_remote_docling_version(httpx_client) -> None:
    """RFC-034 D1: cache the remote Docling ``/version`` response and warn on skew.

    Fetched once per process. commit_sha is the primary skew signal (catches every
    converter-behaviour change); pipeline_version is a secondary, coarser signal.

    When ``REMOTE_VERSION_ENFORCE`` is set, an observed ``pipeline_version``
    skew stops being advisory and raises :class:`RemoteVersionSkewError`, so a
    stale remote converter cannot silently produce trees stamped with the local
    pipeline version.  Default (``false``) is byte-identical warn-only behavior.
    """
    global _remote_docling_version, _remote_pipeline_version_behind
    if _remote_docling_version is None:
        try:
            ver_resp = await httpx_client.get(f"{settings.docling_service_url}/version", timeout=5.0)
            _remote_docling_version = ver_resp.json()
            remote_sha = _remote_docling_version.get("commit_sha", "unknown")
            remote_pv = _remote_docling_version.get("pipeline_version", 0)
            if remote_sha != _CLIENT_BUILD_SHA:
                logger.warning(
                    "Remote Docling SHA %s != client SHA %s", remote_sha, _CLIENT_BUILD_SHA
                )
                DOCLING_VERSION_SKEW.labels(signal="commit_sha").inc()
            if remote_pv < CURRENT_PIPELINE_VERSION:
                logger.error(
                    "Remote pipeline_version %d < local %d",
                    remote_pv,
                    CURRENT_PIPELINE_VERSION,
                )
                DOCLING_VERSION_SKEW.labels(signal="pipeline_version").inc()
                _remote_pipeline_version_behind = remote_pv
        except Exception as e:
            logger.warning("Could not fetch remote /version: %s; skew detection disabled", e)
            _remote_docling_version = {"commit_sha": "unavailable"}

    # Enforcement is re-evaluated on EVERY call — the /version response is
    # fetched once and cached, so gating inside the fetch block would block
    # only the first conversion of the process and let every later one through.
    # An unreachable /version never sets the flag and therefore stays warn-only.
    if _remote_pipeline_version_behind is not None and pipeline_config.remote_version_enforce:
        raise RemoteVersionSkewError(
            f"Remote Docling pipeline_version {_remote_pipeline_version_behind} < local "
            f"{CURRENT_PIPELINE_VERSION}; blocked by REMOTE_VERSION_ENFORCE. Redeploy the "
            f"Docling service or unset REMOTE_VERSION_ENFORCE to fall back to warn-only."
        )


def _converter_contract(converter_name: str | None) -> str | None:
    """RFC-034 D5: resolve the winning converter's module ``__version__``."""
    if not converter_name:
        return None
    try:
        module = importlib.import_module(converter_name)
        return getattr(module, "__version__", None)
    except Exception:
        return None


async def _remote_pdf_to_markdown(
    staging_key: str,
    *,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    expected_script: str | None = None,
) -> tuple[str, list]:
    """Call the external Docling service to convert a PDF.

    Returns ``(markdown, pic_results)`` with the same shape as the local
    ``pdf_to_markdown_docling()`` — callers are oblivious to the transport.
    ``png_bytes`` in each PictureResult is decoded from base64 back to bytes.

    ``expected_script`` is the caller's script expectation for the document
    (e.g. ``"latin"``, ``"arabic"``), matching the local converter's parameter
    of the same name.  It is forwarded as the ``expected_script`` payload key
    so a server-side garble check can use it instead of re-inferring the script
    from the extracted text.  A remote build that does not know the key ignores
    it, so sending it is safe against both old and new Docling services.
    """
    import base64

    import httpx

    from ..storage import presigned_get_url

    if settings.pii_corpus:
        try:
            require_zdr_compliance(settings.docling_service_url, "Docling remote PDF conversion")
        except ZDRComplianceError:
            HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_pdf").inc()
            raise

    url = presigned_get_url(staging_key)
    payload = {
        "presigned_url": url,
        "force_full_page_ocr": force_full_page_ocr,
        "ocr_lang_override": ocr_lang_override,
        "expected_script": expected_script,
    }
    headers: dict[str, str] = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=settings.docling_service_timeout_s) as client:
        await _check_remote_docling_version(client)
        resp = await client.post(
            f"{settings.docling_service_url}/convert/pdf",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    pic_results: list[dict] = []
    for pr in data.get("picture_results", []):
        raw_b64 = pr.get("png_bytes", "")
        if raw_b64:
            pr["png_bytes"] = base64.b64decode(raw_b64)
        else:
            pr["png_bytes"] = b""
        pic_results.append(pr)
    return data["markdown"], pic_results


async def _remote_image_to_markdown(
    staging_key: str,
    *,
    ocr_lang_override: list[str] | None = None,
) -> str:
    """Call the external Docling service to convert an image to markdown."""
    import httpx

    from ..storage import presigned_get_url

    if settings.pii_corpus:
        try:
            require_zdr_compliance(settings.docling_service_url, "Docling remote image conversion")
        except ZDRComplianceError:
            HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_image").inc()
            raise

    url = presigned_get_url(staging_key)
    payload = {
        "presigned_url": url,
        "ocr_lang_override": ocr_lang_override,
    }
    headers: dict[str, str] = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=settings.docling_service_timeout_s) as client:
        resp = await client.post(
            f"{settings.docling_service_url}/convert/image",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["markdown"]
