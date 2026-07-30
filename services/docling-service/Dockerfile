# ── Builder ────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_HTTP_TIMEOUT=600

# Install dependencies from the main project lockfile (same deps as the
# worker image — Docling, torch-cpu, fitz, tesseract bindings, etc.).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy full source — the service imports from pageindex_mcp.converters.
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Install service-specific deps (FastAPI + uvicorn + httpx).
RUN uv pip install "fastapi>=0.115.0" "uvicorn[standard]>=0.30.0" "httpx>=0.27.0"

# Pre-download Docling model artifacts so the runtime image is offline-ready.
RUN --mount=type=secret,id=hf_token,required=false \
    HF_TOKEN="$([ -f /run/secrets/hf_token ] && cat /run/secrets/hf_token || echo '')" \
    uv run python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'), progress=False, with_layout=True, with_tableformer=True, with_code_formula=False, with_picture_classifier=False, with_smolvlm=False, with_rapidocr=False, with_easyocr=False)"


# ── Runtime ───────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System libraries required by Docling (OpenCV) and Tesseract OCR.
# No libreoffice — DOCX/PPTX conversion stays in the main worker image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pre-bake tessdata (deu, eng, ara) — same as the main Dockerfile.
RUN mkdir -p /opt/tessdata && \
    curl -fsSL -o /opt/tessdata/deu.traineddata \
        https://github.com/tesseract-ocr/tessdata/raw/ced78752cc61322fb554c280d13360b35b8684e4/deu.traineddata && \
    echo "896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4  /opt/tessdata/deu.traineddata" | sha256sum -c - && \
    curl -fsSL -o /opt/tessdata/eng.traineddata \
        https://github.com/tesseract-ocr/tessdata/raw/ced78752cc61322fb554c280d13360b35b8684e4/eng.traineddata && \
    echo "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047  /opt/tessdata/eng.traineddata" | sha256sum -c - && \
    curl -fsSL -o /opt/tessdata/ara.traineddata \
        https://github.com/tesseract-ocr/tessdata/raw/ced78752cc61322fb554c280d13360b35b8684e4/ara.traineddata && \
    echo "2005976778bbc14fc56a4ea8d43c6080847aeee72fcc2201488f240daca15c5b  /opt/tessdata/ara.traineddata" | sha256sum -c -

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src/ ./src/
COPY --from=builder /opt/docling-models /opt/docling-models

# Copy the service application.
COPY services/docling-service/app.py ./app.py

ENV PATH="/app/.venv/bin:$PATH"
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
ENV TESSDATA_PREFIX=/opt/tessdata

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
