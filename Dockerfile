# OptionsCoach: one image, two entrypoints (web = uvicorn, worker = python -m app.worker).
# Locked installs only: npm ci from package-lock.json, uv sync --frozen from uv.lock.

FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=frontend /frontend/dist ./frontend/dist
RUN useradd --system --uid 10001 --home /app optionscoach && chown -R optionscoach /app
USER optionscoach
# Render injects PORT (default 10000) and requires binding 0.0.0.0.
ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app.main:app_factory --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
