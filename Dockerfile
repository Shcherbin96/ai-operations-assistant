FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    OPS_HOST=0.0.0.0

# Install runtime dependencies first for a cached layer (no dev, from the lockfile).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application source and the bundled knowledge base.
COPY ops_assistant ./ops_assistant
COPY knowledge_base ./knowledge_base

# Run as a non-root user.
RUN useradd --create-home app && chown -R app:app /app
USER app

EXPOSE 8000
# Serves the API. Set OPS_DATABASE_URL to persist in Postgres; unset -> in-memory.
CMD ["uv", "run", "--no-sync", "python", "-m", "ops_assistant"]
