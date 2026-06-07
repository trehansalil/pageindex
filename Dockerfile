FROM python:3.12-slim AS builder

# Install uv and git (needed for git+https:// dependencies)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Docling's torch/torchvision CPU wheels (and the pre-baked model weights below)
# are still 100s of MB each; uv's default 30s HTTP timeout aborts mid-download on
# slower links (the failure uv itself flags with "Try increasing UV_HTTP_TIMEOUT").
# 10 min is ample. (Routing torch to the CPU index in pyproject.toml already
# stripped the ~2 GB nvidia-*/cuda-* stack the default Linux torch would pull.)
ENV UV_HTTP_TIMEOUT=600

# Install dependencies first (cache-friendly layer ordering)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY mcp_server.py gunicorn.conf.py ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Pre-download docling model artifacts (layout + TableFormer — the only models the
# PDF pipeline loads) INTO THE IMAGE so runtime workers never fetch weights over the
# network. An egress-limited k8s pod that tried to download at runtime would raise
# inside pdf_to_markdown_docling and silently degrade to pymupdf4llm -> flat tree ->
# low_quality_tree(depth<2). This step needs network AT BUILD TIME only.
#
# HF_TOKEN: optional build-time secret. Without it, huggingface_hub prints
#   "You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN
#    to enable higher rate limits and faster downloads."
# and is subject to the lower anonymous rate limit. Pass via BuildKit secret in CI
# (e.g. `--secret id=hf_token,env=HF_TOKEN` or the `gh actions secrets` equivalent)
# so the token is never baked into an image layer. The download itself works
# without it for public models (layout + TableFormer are public), so this is
# warning-suppression + faster CI, not a hard requirement.
#
# NOTE: `RUN --mount=type=secret` is BuildKit-only syntax. Build this image with
# `docker buildx build ...` (what CI does) or `DOCKER_BUILDKIT=1 docker build ...`.
# The legacy builder will fail to parse this Dockerfile.
RUN --mount=type=secret,id=hf_token,required=false \
    HF_TOKEN="$([ -f /run/secrets/hf_token ] && cat /run/secrets/hf_token || echo '')" \
    uv run python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'), progress=False, with_layout=True, with_tableformer=True, with_code_formula=False, with_picture_classifier=False, with_smolvlm=False, with_rapidocr=False, with_easyocr=False)"

# ─── Runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System libraries:
#  - libreoffice: DOCX/PPTX → PDF conversion (invoked with --headless flag)
#  - libgl1 (libGL.so.1) + libglib2.0-0 (libgthread/libglib): Docling's layout
#    model loads OpenCV, which dynamically links these. python:3.12-slim ships
#    NEITHER. Without them Docling raises `ImportError: libGL.so.1` at pipeline
#    init, the converter chain silently falls back to pymupdf4llm (flat tree),
#    and the job dies downstream as low_quality_tree(depth<2) — the real cause
#    masked as a quality failure. They are mandatory for the default PDF route.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the entire virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/mcp_server.py ./
COPY --from=builder /app/gunicorn.conf.py ./
COPY --from=builder /app/src/ ./src/
# Pre-baked docling weights (from the builder) -> offline, no runtime download.
COPY --from=builder /opt/docling-models /opt/docling-models

# Put the venv's Python on PATH
ENV PATH="/app/.venv/bin:$PATH"
# Point docling at the baked-in artifacts so workers never need network egress.
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models

EXPOSE 8201

# Default: gunicorn with uvicorn workers.
# Override to "arq pageindex_mcp.worker.WorkerSettings" for worker instances.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "pageindex_mcp.server:app"]
