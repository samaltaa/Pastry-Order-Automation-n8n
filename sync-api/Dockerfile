FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/sync-api
COPY sync-api/pyproject.toml sync-api/uv.lock ./
RUN uv sync --locked --no-dev
COPY sync-api/app ./app

FROM python:3.12-slim
ENV PATH="/app/sync-api/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app/sync-api
COPY --from=build /app/sync-api ./
USER nobody
EXPOSE 3002
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3002/health', timeout=4)" || exit 1
# Only Caddy can reach this port in production, so its X-Forwarded-For header gives the visitor's real IP.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "3002", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
