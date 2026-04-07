FROM python:3.12-slim AS builder

# Install uv and git (needed for git+https:// dependencies)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cache-friendly layer ordering)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY mcp_server.py ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# ─── Runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy the entire virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/mcp_server.py ./
COPY --from=builder /app/src/ ./src/

# Put the venv's Python on PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8201

CMD ["python", "mcp_server.py"]
