# paddleocr-vl-service

FastAPI wrapper around **PaddleOCR-VL 1.6** (a vision-language OCR model)
for the RFC-046 OCR engine evaluation. It is a *thin proxy*: all inference
happens in an external **Ollama** server; this service just base64-encodes
page images, sends them to Ollama's `/api/chat` with the prompt `"OCR:"`,
and reshapes the response into the same JSON contract used by the other
two OCR services under evaluation (PP-OCRv5 on 8202, `surya-ocr-service`
on 8207), so the eval script can compare all three side by side.

`app.py` is the only supported code path and the source of truth for how
this service behaves — see "Not supported" below for a path that is
documented elsewhere in this repo's history but not what the code does.

## Licensing — AGPL-3.0 (read before deploying)

Inference is proxied to Ollama, but **PDF rasterization is local and uses
PyMuPDF (`pymupdf`), which is AGPL-3.0**:

- `app.py:123` — `_page_to_png`, renders a page to PNG bytes from a PyMuPDF
  pixmap (`pix.tobytes("png")`) which `_ocr_image_bytes` then base64-encodes
- `app.py:223` — `/ocr/pdf`, opens the stream to read `page_count`

The main application does **not** use PyMuPDF for this. It uses
**pypdfium2** (BSD-3/Apache-2) deliberately, because of CLAUDE.md **Hard
Rule 4** — see `src/pageindex_mcp/converters/headings.py:632`. This service
diverges from that rule.

That divergence is a recorded, accepted decision, **not a legal clearance**:
see **ADR-006** in `ARCHITECTURE.md`. It holds only while this service stays
what it is today — an internal-only evaluation sidecar behind no public
ingress, built and deployed by no workflow in `.github/workflows/`. It is
gated behind `profiles: ["ocr-spike"]` in `docker-compose.yml`, so it starts
only when that profile is selected, and it is published as
`127.0.0.1:8204:8204` — loopback only. The container itself listens on
`0.0.0.0`, so that mapping is the only thing bounding who can reach it;
widening it to all interfaces would put an AGPL-3.0 rasterizer on the LAN and
voids ADR-006.

**If this service is ever promoted to a deployed, externally reachable
service, ADR-006 is void.** Swap the two call sites above to `pypdfium2`
(already a direct dependency of the main app) and drop `pymupdf` from
`pyproject.toml` *before* it ships. AGPL §13's network-source obligation is
tracked as risk **R10** in `ARCHITECTURE.md` and is still open.

## Requires

- A running **Ollama** server (not bundled, not started by this service)
  with the PaddleOCR-VL model pulled:
  ```bash
  ollama pull hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF
  ```
- **No Ollama is installed on the current development host** (the one
  this README was written on). Do not assume `/health` will succeed here;
  this service needs to run somewhere with Ollama installed, a pulled
  model, and (for acceptable latency) a GPU — this host has neither
  (recon: 7.6 Gi RAM / 310 Mi free, no GPU).

## Environment variables

| Variable | Default | Read by |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` (bare metal) / `http://host.docker.internal:11434` (container, see Dockerfile) | `app.py` — base URL for Ollama's API |
| `PADDLEOCR_VL_MODEL` | `hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF` | `app.py` — Ollama model tag to invoke |
| `VL_TIMEOUT` | `300` (`120` in the container default) | `app.py` — HTTP timeout (seconds) per Ollama call |
| `VL_DPI` | `150` | `app.py` — rasterization DPI when rendering PDF pages before OCR |

Running the container on Linux Docker requires
`--add-host=host.docker.internal:host-gateway` for `host.docker.internal` to
resolve to the host running Ollama. The compose entry already carries the
equivalent `extra_hosts` line.

This service has a `docker-compose.yml` entry under the `ocr-spike` profile,
alongside `paddleocr-service` (8202), `docling-ocr-service` (8203) and
`surya-ocr-service` (8207). The profile is opt-in: nothing in it starts
unless you ask for it by name. Note that the compose entry does **not**
provide Ollama — that is an external dependency of this service and is
deliberately not modelled here.

## Port

**8204** (`EXPOSE 8204` in the Dockerfile; matches the eval script's
expectation and does not collide with the other OCR services: 8202, 8203,
8207).

## Health check

```bash
curl http://localhost:8204/health
```

Returns `{"status": "ok"}` once Ollama is reachable at `OLLAMA_BASE_URL`
and the configured model is present in `ollama list`. Returns
`{"status": "error", "detail": "..."}` if Ollama is unreachable or the
model isn't pulled yet — check both before assuming the service itself is
broken.

`GET /version` reports the resolved model name, backend (`"ollama"`), and
`OLLAMA_BASE_URL`, which is useful for confirming which Ollama instance a
given run actually talked to.

## Not supported: llama.cpp / Metal

This directory used to also ship a macOS-only setup path
(`setup.sh`/`start.sh`, now `attic/*.superseded`) that built `llama.cpp`
from source with Metal acceleration and served the model directly via
`llama-server` on port 8203. **`app.py` never reads that path's
`LLAMA_SERVER_URL` variable and never talks to a local `llama-server`** —
it only ever calls Ollama's `/api/chat`. That path also can't run on this
(Linux) host, since it shells out to `sysctl -n hw.ncpu` and requires
Metal, and its default port (8203) collides with `docling-ocr-service`.

The scripts are kept under `attic/` for reference (they document real
model-download URLs and llama.cpp build flags, in case a llama.cpp-direct
backend is revisited later) but are not wired to anything and must not be
run as-is. If you want PaddleOCR-VL without Ollama, treat that as a new
implementation task, not a matter of running these scripts.
