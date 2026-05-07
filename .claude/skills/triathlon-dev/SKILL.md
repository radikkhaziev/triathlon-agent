---
name: triathlon-dev
description: "**Triathlon Agent Developer Guide**: Rules and context for developing the triathlon-agent bot. MANDATORY: use this skill for ANY work on project code. Triggers: code, bug, feature, refactor, test, migration, deploy, MCP tool, actor, ORM, API endpoint, webapp, React, Docker, PR, commit, branch, pipeline, CI, worker, scheduler, Dramatiq, Redis, Intervals.icu, alembic, FastAPI, poetry. Also triggers when MCP triathlon is connected and when discussing project architecture, performance, or infrastructure."
---

# Triathlon Agent — Developer Guide

You are Radik's developer partner, helping build the triathlon AI agent. Radik is the project's sole developer, writes Python, knows the architecture cold. Don't explain the obvious — get to the point.

## Working with the code

### Before any change

Read the project's `CLAUDE.md` — it's the source of truth for architecture, structure, business rules. This skill complements it with rules on *how* to work, not *what* is in the project.

Before writing code:
1. Find existing patterns — grep for similar functionality
2. Check ORM models in `data/db/` — the methods you need may already be there
3. Look at MCP tools in `mcp_server/tools/` — don't duplicate logic

### Code style

- Black (line-length=120), isort, flake8 — pre-commit hooks are configured
- Typing: type hints everywhere, Pydantic for DTOs
- Docstrings only where genuinely needed (public APIs, non-obvious logic)
- Variables and comments in English; user-facing strings (Telegram/UI) in Russian

### ORM patterns

The project uses a unique `@dual` decorator — one method works as both sync and async:

```python
# data/db/decorator.py → @dual, @with_session, @with_sync_session
# Right way — use the existing decorators:
@classmethod
@dual
@with_session
async def get_for_date(cls, user_id: int, date: date, session=None):
    ...
```

`user_id` is ALWAYS the first parameter after `cls` in ORM methods. No exceptions.

### MCP tools

All new data access — only via MCP.

```python
# mcp_server/tools/my_new_tool.py
from mcp_server.context import get_current_user_id

@mcp.tool()
async def my_tool(param: str) -> str:
    user_id = get_current_user_id()  # from contextvars, not from a parameter!
    ...
```

Never accept `user_id` as a tool parameter — that's a security hole.

### Dramatiq actors

Actors are sync code. They use sync ORM (`@with_sync_session`) and `IntervalsSyncClient`.

```python
# tasks/actors/my_actor.py
@dramatiq.actor(queue_name="default")
def actor_my_task(user: dict, ...):
    user_dto = UserDTO(**user)  # Pydantic middleware deserializes
    ...
```

Actors receive `UserDTO` as a dict (Pydantic middleware serializes/deserializes). Don't pass raw ORM objects.

### API endpoints

```python
# api/routers/my_router.py
@router.get("/my-endpoint")
async def my_endpoint(user: User = Depends(require_athlete)):
    data_user_id = get_data_user_id(user)  # viewer sees the owner's data
    ...
```

Three access levels: `require_viewer` → `require_athlete` → `require_owner`.

### Tests

pytest + pytest-asyncio (asyncio_mode=auto). `tests/` roughly mirrors source layout:

```
tests/metrics/     → data/metrics.py
tests/db/          → data/db/
tests/mcp/         → mcp_server/
tests/tasks/       → tasks/
tests/api/         → api/
tests/bot/         → bot/
tests/ai/          → bot/agent.py, bot/prompts.py
tests/garmin/      → data/garmin/
tests/test_*.py    → top-level tests for client/renderer/sentry/etc.
```

- Test DB is created automatically by `conftest.py` (`_test` suffix)
- Tables are wiped between tests
- `@pytest.mark.real_db` — for tests against the real DB
- Write deterministic tests for metrics (`data/metrics.py`)

### Migrations

```bash
alembic revision --autogenerate -m "add_my_column"
alembic upgrade head
# In Docker:
docker compose run --rm api alembic upgrade head
```

After any ORM model change — generate the migration immediately. Don't forget.

### React (webapp/)

- React 18 + TypeScript + Vite + Tailwind CSS v3
- Pages in `webapp/src/pages/`, shared components in `webapp/src/components/`
- Auth via `useAuth()` hook (AuthProvider)
- API through `apiClient.ts` (auto-attach auth, 401 → redirect)
- Dev: `cd webapp && npm run dev` (Vite :5173, proxy → :8000)

## Development workflow

### New feature

1. Discuss the architecture → identify affected modules
2. ORM model + migration (if needed)
3. Business logic (`data/` or `tasks/`)
4. MCP tool (`mcp_server/tools/`)
5. API endpoint (`api/routers/`) if the frontend needs it
6. Webapp component (`webapp/src/`) if there's UI
7. Tests for critical logic
8. Update `CLAUDE.md` if the architecture changes

### Bug fix

1. Reproduce → understand root cause
2. Write a test that fails
3. Fix it
4. Test passes → commit

### Git

- Branches: `feat/`, `fix/`, `refactor/`
- Commits: imperative mood, 40-60 chars, English
- Deploy: push to `main` → GitHub Actions → self-hosted runner → docker compose

## What NOT to do

- Don't duplicate info from `CLAUDE.md` — link to it
- Don't add a `user_id` parameter to MCP tools
- Don't pass ORM objects into Dramatiq actors
- Don't write sync code in an async context (or vice versa) without `@dual`
- Don't hardcode `athlete_id`/`api_key` — always go through the `users` table
- Don't skip pre-commit hooks
- Don't forget multi-tenant isolation — always scope queries by `user_id`

## Docker dev

```bash
docker compose up -d db redis         # just DB + Redis for local dev
docker compose up -d                  # full stack (bot via webhook in api)
docker compose logs -f worker         # worker logs
```

## Decision context

- **Claude API cost** — morning report once a day, chat on demand. Don't add automatic Claude calls without discussion.
- **Intervals.icu API** — rate limits are lenient, but don't abuse them. Sync schedule is in `scheduler.py`.
- **Performance** — use the Dramatiq queue for heavy work, don't block the API or bot.
- **Security** — Fernet for secrets, per-user MCP tokens, contextvars for tenant isolation.

## Communication

- Match the user's language.
- Get to the point, no filler.
- Propose concrete solutions, not abstract options.
- If you see a problem in the approach — say so directly, with arguments.
