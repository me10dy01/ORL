# Ticket CS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag the old demo, replace the repo with a uv-managed FastAPI service plus Postgres/pgvector Compose, and ship login, messages, ticket state machine, handoff, and query rewrite with entity slots.

**Architecture:** One FastAPI app under `apps/api` talks to PostgreSQL. Conversation text stays in `messages`. Retrieval and tools (later plans) consume a rewritten query plus `conversation_slots`. This plan does not build Next.js or the embedding pipeline.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2, PostgreSQL 16 + pgvector, pytest, JWT (PyJWT), passlib/bcrypt.

**Out of scope (later plans):** Next.js UI, RAG ingest/search, business order tools, SSE token streaming, MCP, Multi-Agent.

## Global Constraints

- Python dependencies are managed with uv (`pyproject.toml` + `uv.lock`).
- Database is PostgreSQL in Docker, not a host MySQL install.
- Embedding model name is `qwen3-embedding:0.6b` (configured now, used in the RAG plan).
- LLM default is Ollama at `http://host.docker.internal:11434` from containers; host tests may use `http://127.0.0.1:11434`.
- Ticket statuses: `open`, `in_progress`, `resolved`, `closed`, `cancelled`. No `pending_customer`.
- Priorities: `low`, `normal`, `high`.
- Query rewrite runs only when history or slots exist; the stored timeline is the original user text.
- Do not delete the git history. Tag `legacy-qa-demo` before removing old code. Keep `docs/superpowers/`.

---

### Task 1: Preserve the old demo

**Files:**
- Modify: none (git tag only)

**Interfaces:**
- Consumes: current `main` tree
- Produces: tag `legacy-qa-demo` pointing at the commit before the wipe

- [ ] **Step 1: Tag and verify**

```powershell
git tag legacy-qa-demo
git show-ref --tags legacy-qa-demo
```

Expected: one line containing `refs/tags/legacy-qa-demo`.

- [ ] **Step 2: Commit**

No file commit. The tag is the checkpoint. Do not push unless asked.

### Task 2: Wipe application code and scaffold the API with uv

**Files:**
- Delete: `app/`, `tests/`, `data/` (old demo), root `pyproject.toml`, `uv.lock`, `AI开发规范.md`, old `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/METRICS.md`, `docs/INTERVIEW.md`, `docs/TROUBLESHOOTING.md`, `README.md`
- Keep: `.git/`, `docs/superpowers/`, `.gitignore`
- Create: `apps/api/pyproject.toml`, `apps/api/app/main.py`, `apps/api/app/config.py`, `README.md`, `docker-compose.yml`, `.env.example`

**Interfaces:**
- Consumes: tag from Task 1
- Produces: `GET /health` → `{"status":"ok"}` via `uv run uvicorn app.main:app`

- [ ] **Step 1: Remove old trees after the tag exists**

```powershell
git rm -r app tests data
git rm pyproject.toml uv.lock README.md "AI开发规范.md"
git rm docs/ARCHITECTURE.md docs/API.md docs/METRICS.md docs/INTERVIEW.md docs/TROUBLESHOOTING.md
```

Do not remove `docs/superpowers/`.

- [ ] **Step 2: Create `apps/api/pyproject.toml`**

```toml
[project]
name = "orl-api"
version = "0.1.0"
description = "Xingchen ticket customer-service API"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic-settings>=2.6",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.2",
  "pyjwt>=2.9",
  "pwdlib[bcrypt]>=0.2",
]

[dependency-groups]
dev = ["pytest>=8.3", "httpx>=0.27"]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `apps/api/app/config.py` and `apps/api/app/main.py`**

```python
# apps/api/app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:8b"
    embedding_model: str = "qwen3-embedding:0.6b"


def get_settings() -> Settings:
    return Settings()
```

```python
# apps/api/app/main.py
from fastapi import FastAPI

app = FastAPI(title="ORL Ticket CS")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Create `docker-compose.yml` and `.env.example`**

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: orl
      POSTGRES_PASSWORD: orl
      POSTGRES_DB: orl
    ports:
      - "5432:5432"
    volumes:
      - orl_pg:/var/lib/postgresql/data
volumes:
  orl_pg:
```

```text
DATABASE_URL=postgresql+psycopg://orl:orl@127.0.0.1:5432/orl
JWT_SECRET=dev-only-change-me
OLLAMA_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:8b
EMBEDDING_MODEL=qwen3-embedding:0.6b
```

- [ ] **Step 5: Install and hit health**

```powershell
cd apps/api
uv sync
uv run uvicorn app.main:app --port 8000
```

In another shell: `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "Scaffold uv FastAPI service and Postgres compose."
```

### Task 3: Ticket state machine

**Files:**
- Create: `apps/api/app/domain/tickets.py`
- Test: `apps/api/tests/test_tickets.py`

**Interfaces:**
- Consumes: nothing
- Produces: `transition(status, action) -> str` raising `InvalidTransition` on illegal moves

Actions: `claim`, `resolve`, `close`, `cancel`.

| from | claim | resolve | close | cancel |
| --- | --- | --- | --- | --- |
| open | in_progress | — | — | cancelled |
| in_progress | — | resolved | — | cancelled |
| resolved | — | — | closed | — |
| closed | — | — | — | — |
| cancelled | — | — | — | — |

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.domain.tickets import InvalidTransition, transition


def test_claim_open_ticket():
    assert transition("open", "claim") == "in_progress"


def test_close_requires_resolved():
    with pytest.raises(InvalidTransition):
        transition("open", "close")
```

- [ ] **Step 2: Run and confirm fail**

```powershell
cd apps/api
uv run pytest tests/test_tickets.py -v
```

Expected: FAIL, import error.

- [ ] **Step 3: Implement `apps/api/app/domain/tickets.py`**

```python
class InvalidTransition(Exception):
    pass


_EDGES: dict[tuple[str, str], str] = {
    ("open", "claim"): "in_progress",
    ("open", "cancel"): "cancelled",
    ("in_progress", "resolve"): "resolved",
    ("in_progress", "cancel"): "cancelled",
    ("resolved", "close"): "closed",
}


def transition(status: str, action: str) -> str:
    nxt = _EDGES.get((status, action))
    if nxt is None:
        raise InvalidTransition(f"{status} cannot {action}")
    return nxt
```

- [ ] **Step 4: Re-run pytest**

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/app/domain/tickets.py apps/api/tests/test_tickets.py
git commit -m "Add ticket status transitions."
```

### Task 4: Query rewrite and slots

**Files:**
- Create: `apps/api/app/services/rewrite.py`
- Test: `apps/api/tests/test_rewrite.py`

**Interfaces:**
- Consumes: a callable `complete(prompt: str) -> str` injected by the caller (tests pass a fake; production passes the LLM client in a later task)
- Produces:
  - `rewrite_query(current, history, slots, complete) -> str`
  - `extract_slots(text) -> dict` with optional `last_order_id`, `last_product_id`
  - `merge_slots(current, found) -> dict`

Rules:

- If `history` is empty and both slot fields are missing, return `current` unchanged and do not call `complete`.
- Otherwise call `complete` once. If it returns blank, return `current`.
- Order ids match `ORD` + 8 digits. Product ids match `P` + 3 digits.
- `merge_slots` overwrites a key only when the new value is non-empty.

- [ ] **Step 1: Write the failing tests**

```python
from app.services.rewrite import extract_slots, merge_slots, rewrite_query


def test_skips_llm_without_context():
    def boom(_: str) -> str:
        raise AssertionError("llm should not run")

    assert rewrite_query("退货运费谁承担？", [], {}, boom) == "退货运费谁承担？"


def test_rewrites_pronoun_with_history():
    def fake(_: str) -> str:
        return "K87 退货的运费由谁承担？"

    history = [{"role": "user", "content": "K87 支持退货吗？"}]
    assert rewrite_query("这个运费谁承担？", history, {}, fake).startswith("K87")


def test_extract_and_merge_slots():
    found = extract_slots("订单 ORD12345678 里的 P001")
    assert found["last_order_id"] == "ORD12345678"
    assert found["last_product_id"] == "P001"
    assert merge_slots({"last_product_id": "P002"}, {"last_order_id": "ORD12345678"}) == {
        "last_product_id": "P002",
        "last_order_id": "ORD12345678",
    }
```

- [ ] **Step 2: Run pytest and confirm fail**

- [ ] **Step 3: Implement `rewrite.py`**

```python
import re

ORDER_RE = re.compile(r"ORD\d{8}")
PRODUCT_RE = re.compile(r"P\d{3}")


def extract_slots(text: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    if m := ORDER_RE.search(text):
        slots["last_order_id"] = m.group(0)
    if m := PRODUCT_RE.search(text):
        slots["last_product_id"] = m.group(0)
    return slots


def merge_slots(current: dict, found: dict) -> dict:
    merged = dict(current)
    for key in ("last_order_id", "last_product_id"):
        value = found.get(key)
        if value:
            merged[key] = value
    return merged


def rewrite_query(current: str, history: list[dict], slots: dict, complete) -> str:
    has_slots = bool(slots.get("last_order_id") or slots.get("last_product_id"))
    if not history and not has_slots:
        return current
    lines = [f"{m.get('role')}: {m.get('content')}" for m in history]
    prompt = (
        "把用户当前问题改写成独立问句，消解「这个」「它」等指代。"
        "不要回答。若已完整则原样输出。\n"
        f"槽位：{slots}\n历史：\n" + "\n".join(lines) + f"\n当前：{current}\n改写："
    )
    rewritten = (complete(prompt) or "").strip()
    return rewritten or current
```

- [ ] **Step 4: pytest pass, then commit**

```powershell
git commit -m "Rewrite follow-up questions and track order or product slots."
```

### Task 5: Persistence and handoff API

**Files:**
- Create: `apps/api/app/db.py`, `apps/api/app/models.py`, `apps/api/app/api/routes.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_handoff.py` using SQLite in-memory **only if** models stay portable; otherwise spin `docker compose up -d db` and point `DATABASE_URL` at it. Prefer Postgres so `JSONB` matches production. Skip pgvector columns in this task.

**Interfaces:**
- Consumes: `transition`, `extract_slots`, `merge_slots`, `rewrite_query`
- Produces:
  - tables `users`, `conversations`, `messages`, `conversation_slots`, `tickets`, `ticket_events`
  - `POST /api/auth/login`
  - `POST /api/conversations`
  - `POST /api/chat` body `{conversation_id, message}` — stores the original message; if mode is `bot` and the text asks for a human (`转人工` or `人工客服`), creates an `open` ticket, sets mode `human`, returns `{ticket_id, rewritten_query: null}`
  - if mode is `bot` and no handoff, returns `{rewritten_query}` from `rewrite_query` (fake complete returns the current text in tests)
  - `POST /api/tickets/{id}/claim` with agent JWT → `in_progress` or 409

- [ ] **Step 1: Write the failing API test**

```python
def test_handoff_creates_open_ticket(client):
    login = client.post("/api/auth/login", json={"username": "user1", "password": "demo1234"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    conv = client.post("/api/conversations", headers=headers)
    cid = conv.json()["conversation_id"]
    res = client.post("/api/chat", headers=headers, json={"conversation_id": cid, "message": "转人工"})
    body = res.json()
    assert body["ticket_id"]
    assert body["mode"] == "human"
```

`client` is a pytest fixture: create tables on the test database, seed users, override `get_settings`, yield `TestClient(app)`.

- [ ] **Step 2: Implement models, seed `user1` / `agent1` (password `demo1234`), and the routes above. A second request claiming an `open` ticket as `user1` returns 403. Claiming as `agent1` moves it to `in_progress`. Claiming again returns 409.**

- [ ] **Step 3: `uv run pytest` passes.**

- [ ] **Step 4: Commit** `Persist conversations and hand off to an open ticket.`

---

## Plan self-check

- Spec §3 memory and §3.2 rewrite: Tasks 4–5.
- Spec ticket machine and handoff: Tasks 3 and 5.
- Spec Docker Postgres and uv: Task 2.
- Spec RAG, Next.js, AI field fill, SSE: later plans, not this file.
- Query rewrite is not left as an optional phase.

## Next plans

1. `2026-10-12-ticket-cs-rag.md` — ingest, pgvector, tools, LLM provider, refusal counter.
2. `2026-10-12-ticket-cs-web.md` — Next.js customer chat and agent desk.
