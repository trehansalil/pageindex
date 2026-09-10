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

ENV SURYA_DPI=150
ENV SURYA_TIMEOUT=600
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8207

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8207", "--workers", "1"]
