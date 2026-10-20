FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /srv/orl
COPY docker-compose.yml ./docker-compose.yml
COPY data/documents ./data/documents
COPY apps/api ./apps/api
WORKDIR /srv/orl/apps/api
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
