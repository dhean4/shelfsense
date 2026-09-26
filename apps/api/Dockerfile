# ShelfSense API / worker / ingester image. One image, three process groups (see fly.toml).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependency layer: only the workspace manifests, so code changes do not reinstall.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/README.md apps/api/
COPY apps/simulator/pyproject.toml apps/simulator/README.md apps/simulator/
COPY evals/pyproject.toml evals/README.md evals/
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p apps/api/shelfsense_api apps/simulator/shelfsense_simulator evals/shelfsense_evals \
    && touch apps/api/shelfsense_api/__init__.py apps/simulator/shelfsense_simulator/__init__.py evals/shelfsense_evals/__init__.py \
    && uv sync --frozen --no-dev --package shelfsense-api

COPY apps/api apps/api
COPY apps/simulator apps/simulator
COPY evals evals
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --package shelfsense-api

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"
# fly.toml overrides the command per process group; this is the API.
CMD ["uvicorn", "shelfsense_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "apps/api"]
