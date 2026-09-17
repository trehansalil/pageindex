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

# app.py proxies to an Ollama server — it never reads LLAMA_SERVER_URL/
# VL_MODEL_NAME/VL_TASK (those named the dead llama.cpp path and are gone;
# see README.md). host.docker.internal requires the container to be run
# with `--add-host=host.docker.internal:host-gateway` on Linux Docker.
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
ENV PADDLEOCR_VL_MODEL=hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF
ENV VL_TIMEOUT=120
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8204

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8204", "--workers", "1"]
