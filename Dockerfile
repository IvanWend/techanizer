# Stage 1: compile the React UI into static files (frontend/dist).
# Throwaway stage — Node tooling never reaches the final image.
FROM node:26-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Cap Node's heap below the 2G VM ceiling; swap covers the rest.
RUN NODE_OPTIONS=--max-old-space-size=1536 npm run build

# Stage 2: the runtime — Python + uv only.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
WORKDIR /app
ENV UV_LINK_MODE=copy

# Dependencies as their own layer: rebuilt only when uv.lock changes.
# --no-install-project skips our package; --no-install-package drops
# jupyterlab, the one heavy dev-only dependency the server never imports.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package jupyterlab

# Our code + the built UI. The project installs editable, so
# FRONTEND_DIST (parents[2] of app.py) resolves to /app/frontend/dist.
COPY techanizer/ ./techanizer/
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN uv sync --frozen --no-dev --no-install-package jupyterlab

# Put the venv on PATH so startup needs no `uv run` (which would re-sync).
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Single worker on purpose: the per-IP rate limiter is in-process, and a
# second worker would double the effective limit.
CMD ["uvicorn", "techanizer.serving.app:app", "--host", "0.0.0.0", "--port", "8000"]
