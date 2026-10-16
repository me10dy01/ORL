# ORL Ticket Customer-Service API

This repository is the ticket customer-service rebuild.

## Quick start

Postgres starts with:

```powershell
docker compose up -d db
```

The API runs from `apps/api`:

```powershell
cd apps/api
uv sync
uv run uvicorn app.main:app --port 8000
```

Health check:

```powershell
curl http://127.0.0.1:8000/health
```
