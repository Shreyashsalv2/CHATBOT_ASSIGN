# ---- build stage: resolve and install dependencies into a virtualenv ----
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime stage: only the virtualenv and the app code ----
FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
