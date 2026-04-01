FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for asyncpg / cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Layer-cache optimisation ──────────────────────────────────────────────────
# Copy manifests only; install third-party deps without the project package.
# This layer is rebuilt only when pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Copy source and install the project itself ────────────────────────────────
COPY . .
RUN uv sync --frozen --no-dev

# Write entrypoint inline so it's always present regardless of source delivery
RUN printf '#!/bin/sh\nset -e\necho "==> Running Alembic migrations..."\nuv run alembic upgrade head\necho "==> Starting uvicorn on port ${PORT:-8000} (log-level: ${LOG_LEVEL:-info})..."\nexec uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level "${LOG_LEVEL:-info}"\n' > /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

# Expose configurable port (default 8000)
EXPOSE ${PORT:-8000}

ENTRYPOINT ["/app/docker-entrypoint.sh"]
