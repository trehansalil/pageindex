FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_HTTP_TIMEOUT=600

COPY pyproject.toml ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

COPY app.py .

# No PADDLEOCR_LANG env — the service normalises per-request lang to "en" or "ar"
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
ENV PADDLEOCR_USE_GPU=false
ENV OCR_PAGE_WORKERS=3
ENV PADDLEOCR_VERSION=PP-OCRv6
ENV PADDLEOCR_ARABIC_VERSION=PP-OCRv5
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8202

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8202", "--workers", "1"]
