# surya-ocr-service

FastAPI wrapper around **Surya OCR** for the RFC-046 OCR engine evaluation.
It exposes `/ocr/image` and `/ocr/pdf`, runs layout + recognition locally in
the container, and returns the same JSON contract as the other two OCR
services under evaluation (PP-OCRv5 on 8202, `paddleocr-vl-service` on
8204), so the eval script can compare all three side by side.

`app.py` is the only supported code path and the source of truth for how
this service behaves.

## Licensing — AGPL-3.0 (read before deploying)

This service depends on **PyMuPDF (`pymupdf`), which is AGPL-3.0**. It uses
it in exactly two places, both to turn PDF bytes into page images for OCR:

- `app.py:145` — `_page_to_pil`, renders one page at `SURYA_DPI`
- `app.py:245` — `/ocr/pdf`, opens the stream to read `page_count`

The main application does **not** use PyMuPDF for this. It uses
**pypdfium2** (BSD-3/Apache-2) deliberately, because of CLAUDE.md **Hard
Rule 4** — see `src/pageindex_mcp/converters/headings.py:632`. This service
diverges from that rule.

That divergence is a recorded, accepted decision, **not a legal clearance**:
see **ADR-006** in `ARCHITECTURE.md`. It holds only while this service stays
what it is today — an internal-only evaluation sidecar on the
`docker-compose` network, behind no public ingress, built and deployed by no
workflow in `.github/workflows/`.

**If this service is ever promoted to a deployed, externally reachable
service, ADR-006 is void.** Swap the two call sites above to `pypdfium2`
(already a direct dependency of the main app) and drop `pymupdf` from
`pyproject.toml` *before* it ships. AGPL §13's network-source obligation is
tracked as risk **R10** in `ARCHITECTURE.md` and is still open.

## Run

```bash
docker compose up surya-ocr-service      # port 8207
curl -F file=@page.png http://localhost:8207/ocr/image
curl -F file=@doc.pdf  http://localhost:8207/ocr/pdf
```
