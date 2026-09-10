FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

COPY app.py .

ENV LLAMA_SERVER_URL=http://host.docker.internal:8203
ENV VL_MODEL_NAME=PaddleOCR-VL-1.6
ENV VL_TASK=OCR:
ENV VL_TIMEOUT=120
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8204

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8204", "--workers", "1"]
