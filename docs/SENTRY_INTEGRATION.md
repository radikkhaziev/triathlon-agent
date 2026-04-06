# Sentry Integration Spec

> Error monitoring, performance tracing, data scrubbing, and GitHub auto-issues.

**Related issues:** [#40](https://github.com/radikkhaziev/triathlon-agent/issues/40), [#67](https://github.com/radikkhaziev/triathlon-agent/issues/67)

---

## Scope

| Feature | Included | Notes |
|---------|----------|-------|
| Error tracking | Yes | Все 4 компонента: bot, api, mcp, tasks |
| Performance monitoring | Yes | Traces + spans, 10% sample rate |
| User context | Yes | user_id + athlete_id на каждом event |
| Data scrubbing | Yes | API keys, tokens, Fernet key, JWT |
| Cron monitoring | No (Phase 2) | APScheduler jobs — отдельный этап |
| GitHub auto-issues | Yes | Нативная интеграция Sentry → GitHub |
| Frontend (React) | No | Только бэкенд в этой спеке |

---

## Dependencies

```toml
[tool.poetry.dependencies]
sentry-sdk = {version = "^2.19", extras = ["fastapi", "dramatiq"]}
```

Extras автоматически подтягивают `StarletteIntegration`, `FastApiIntegration`, `DramatiqIntegration`.

---

## Environment Variables

```env
# Sentry
SENTRY_DSN=https://...@o0.ingest.sentry.io/0   # пустой = Sentry отключён
SENTRY_ENVIRONMENT=production                    # production / development / staging
SENTRY_TRACES_SAMPLE_RATE=0.1                    # 10% транзакций
SENTRY_RELEASE=                                  # опционально, auto-detect из git
```

Добавить в `config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Sentry
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.1
    sentry_release: str = ""
```

---

## Architecture

### Единая точка инициализации

Файл: `sentry_config.py` (корень проекта, рядом с `config.py`).

Вызывается из трёх entry points: `api/server.py`, `bot/main.py`, `tasks/worker.py`.

```python
# sentry_config.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.dramatiq import DramatiqIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from config import settings

SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "mcp_token",
                  "encryption_key", "fernet", "jwt", "authorization", "cookie"}


def _before_send(event, hint):
    """Scrub sensitive data from Sentry events."""
    # Scrub extra context
    for section in ("extra", "contexts"):
        data = event.get(section, {})
        if isinstance(data, dict):
            _scrub_dict(data)

    # Scrub request headers & body
    request = event.get("request", {})
    _scrub_dict(request.get("headers", {}))
    if isinstance(request.get("data"), dict):
        _scrub_dict(request["data"])

    # Scrub breadcrumb data
    for crumb in event.get("breadcrumbs", {}).get("values", []):
        _scrub_dict(crumb.get("data", {}))

    return event


def _scrub_dict(d: dict):
    """Redact values whose keys match sensitive patterns."""
    for key in list(d.keys()):
        if any(s in key.lower() for s in SENSITIVE_KEYS):
            d[key] = "[REDACTED]"
        elif isinstance(d[key], dict):
            _scrub_dict(d[key])


def _traces_sampler(sampling_context):
    """Custom sampler: skip health checks, sample everything else."""
    tx_name = sampling_context.get("transaction_context", {}).get("name", "")
    if tx_name in ("GET /health", "GET /health/"):
        return 0.0  # never trace health checks
    return settings.sentry_traces_sample_rate


def init_sentry():
    """Initialize Sentry SDK. No-op if SENTRY_DSN is empty."""
    if not settings.sentry_dsn:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release or None,
        traces_sampler=_traces_sampler,
        before_send=_before_send,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(transaction_style="endpoint"),
            DramatiqIntegration(),
            LoggingIntegration(
                level=None,           # don't capture INFO as breadcrumbs
                event_level="ERROR",  # capture ERROR+ as events
            ),
        ],
        # Send PII only if we explicitly set_user()
        send_default_pii=False,
    )
```

**Почему один файл:**
- DRY — все entry points (api, bot, worker) вызывают `init_sentry()`.
- `before_send` и `traces_sampler` тестируемы отдельно.
- Feature flag: пустой `SENTRY_DSN` = Sentry полностью отключён.

---

## Integration Points

### 1. FastAPI (`api/server.py`)

**Что делает SDK автоматически:**
- Ловит unhandled exceptions → Sentry event с полным traceback.
- Создаёт transaction на каждый HTTP request.
- Записывает breadcrumbs (SQL, HTTP, logging).

**Что нужно добавить вручную:**

```python
# api/server.py
from sentry_config import init_sentry

# Вызвать ДО создания FastAPI app
init_sentry()

app = FastAPI(...)
```

**User context middleware** (после auth):

```python
# api/deps.py — внутри get_current_user() или отдельный middleware
import sentry_sdk

async def set_sentry_user(user: User):
    sentry_sdk.set_user({
        "id": str(user.id),
        "username": f"athlete_{user.athlete_id}" if user.athlete_id else f"user_{user.id}",
        "role": user.role,
    })
```

Вызывать в `get_current_user()` после успешной аутентификации.

**Global exception handler** (новый, для красивых 500):

```python
# api/server.py
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    sentry_sdk.capture_exception(exc)
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
```

---

### 2. Telegram Bot (`bot/main.py`)

**Проблема:** `python-telegram-bot` не имеет встроенной Sentry-интеграции.

**Решение:** Sentry init + ручной `capture_exception` в error handler.

```python
# bot/main.py
from sentry_config import init_sentry

init_sentry()

# Существующий error handler — добавить capture:
async def error_handler(update, context):
    sentry_sdk.set_user({
        "id": str(update.effective_user.id) if update.effective_user else None,
    })
    sentry_sdk.set_context("telegram", {
        "chat_id": update.effective_chat.id if update.effective_chat else None,
        "message_text": update.message.text[:100] if update.message and update.message.text else None,
    })
    sentry_sdk.capture_exception(context.error)
    logger.exception("Telegram error", exc_info=context.error)
```

**ClaudeAgent (`bot/agent.py`)** — обернуть tool-use loop:

```python
import sentry_sdk

async def chat(self, text: str, mcp_token: str) -> str:
    with sentry_sdk.start_transaction(op="ai.chat", name="ClaudeAgent.chat"):
        try:
            # existing tool-use loop
            ...
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise
```

---

### 3. Dramatiq Workers (`tasks/`)

**Что делает SDK автоматически:**
- `DramatiqIntegration` перехватывает exceptions в actors.
- Каждый actor failure → Sentry event с actor name, args, traceback.
- Dramatiq message_id прикрепляется как tag.

**Что нужно добавить вручную:**

```python
# tasks/worker.py (или tasks/broker.py — до инициализации брокера)
from sentry_config import init_sentry

# ВАЖНО: init ДО импорта Dramatiq broker
init_sentry()

from tasks.broker import broker  # noqa: E402
```

**User context в actors** — хелпер:

```python
# tasks/utils.py (или отдельный tasks/sentry.py)
import sentry_sdk
from tasks.dto import UserDTO


def set_sentry_user_from_dto(user: UserDTO):
    """Attach user context to Sentry scope for current actor."""
    sentry_sdk.set_user({
        "id": str(user.id),
        "username": f"athlete_{user.athlete_id}" if user.athlete_id else f"user_{user.id}",
    })
```

Вызывать в начале каждого actor:

```python
# tasks/actors/wellness.py
@dramatiq.actor
def actor_sync_wellness(user: UserDTO, date: DateDTO):
    set_sentry_user_from_dto(user)
    # ... existing logic ...
```

**Кастомные spans для тяжёлых операций:**

```python
# tasks/actors/activities.py
@dramatiq.actor
def actor_process_fit(user: UserDTO, activity_id: str):
    set_sentry_user_from_dto(user)
    with sentry_sdk.start_span(op="fit.download", description=f"Download FIT {activity_id}"):
        fit_data = download_fit(activity_id)
    with sentry_sdk.start_span(op="fit.parse", description=f"Parse FIT {activity_id}"):
        result = parse_fit(fit_data)
```

---

### 4. MCP Server (`mcp_server/`)

**Проблема:** MCP сервер работает внутри FastAPI (mount на `/mcp`), но tool calls — это отдельный уровень обработки.

**Что SDK ловит автоматически:**
- HTTP-уровень ошибок (FastAPI integration).
- Unhandled exceptions в tool handlers.

**Что нужно добавить:**

```python
# mcp_server/context.py — после set_current_user_id()
import sentry_sdk

def set_current_user_id(user_id: int, athlete_id: str = None):
    _current_user_id.set(user_id)
    # Attach to Sentry
    sentry_sdk.set_user({
        "id": str(user_id),
        "username": f"athlete_{athlete_id}" if athlete_id else f"user_{user_id}",
    })
```

**Tool-level error capture** — декоратор для MCP tools:

```python
# mcp_server/sentry.py
import functools
import sentry_sdk


def sentry_tool(func):
    """Wrap MCP tool with Sentry span + error capture."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tool_name = func.__name__
        with sentry_sdk.start_span(op="mcp.tool", description=tool_name):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                sentry_sdk.set_tag("mcp.tool", tool_name)
                sentry_sdk.capture_exception(e)
                raise
    return wrapper
```

Применять к tools, которые обращаются к внешним API или делают тяжёлые вычисления:

```python
# mcp_server/tools/wellness.py
@mcp.tool()
@sentry_tool
async def get_wellness(target_date: str | None = None) -> str:
    ...
```

---

### 5. Intervals.icu Client (`data/intervals/client.py`)

**Spans для внешних API вызовов:**

```python
import sentry_sdk

async def _request(self, method: str, path: str, **kwargs):
    with sentry_sdk.start_span(
        op="http.client",
        description=f"{method} intervals.icu{path}",
    ) as span:
        span.set_data("http.method", method)
        span.set_data("http.url", f"https://intervals.icu/api/v1{path}")
        response = await self.session.request(method, path, **kwargs)
        span.set_data("http.status_code", response.status)
        return response
```

**Retry failures → Sentry breadcrumb:**

```python
# В retry loop
sentry_sdk.add_breadcrumb(
    category="intervals_icu",
    message=f"Retry {attempt}/{MAX_RETRIES} for {path}: {status}",
    level="warning",
)
```

---

## Data Scrubbing

### Что фильтруем

| Data | Source | Method |
|------|--------|--------|
| `INTERVALS_API_KEY` | request headers, actor args | `before_send` → REDACTED |
| `mcp_token` | request headers, DB queries | `before_send` → REDACTED |
| `FIELD_ENCRYPTION_KEY` | env vars в breadcrumbs | `before_send` → REDACTED |
| `JWT_SECRET` | env vars | `before_send` → REDACTED |
| `ANTHROPIC_API_KEY` | env vars, request headers | `before_send` → REDACTED |
| `TELEGRAM_BOT_TOKEN` | env vars | `before_send` → REDACTED |
| User API keys (encrypted) | DB fields | Не попадают (Fernet encrypted) |

### Sentry built-in scrubbing

SDK автоматически фильтрует headers: `Authorization`, `Cookie`, `X-Csrf-Token`, и request body fields с ключами `password`, `secret`, `token`.

### Custom `before_send`

Дополнительно фильтруем по паттернам: `api_key`, `fernet`, `encryption_key`, `mcp_token` — см. `_before_send()` в `sentry_config.py` выше.

### `send_default_pii=False`

По умолчанию Sentry НЕ отправляет:
- IP-адреса
- User-Agent
- Cookies
- Request body

Мы явно вызываем `set_user()` только с `id`, `username`, `role` — без email и IP.

---

## GitHub Auto-Issues (#67)

### Нативная интеграция (рекомендуемый путь)

Sentry имеет встроенную GitHub интеграцию — **без кода**, настраивается в UI.

**Настройка:**

1. Sentry → Settings → Integrations → GitHub → Install
2. Авторизовать GitHub, выбрать repo `radikkhaziev/triathlon-agent`
3. Sentry → Alerts → Create Alert Rule:

| Параметр | Значение |
|----------|----------|
| Trigger | "A new issue is created" |
| Filter | `level: error`, `is:unresolved` |
| Action | "Create a GitHub Issue" |
| Repository | `radikkhaziev/triathlon-agent` |
| Labels | `bug`, `sentry` |
| Assignee | (опционально) |

4. Каждая новая уникальная ошибка → автоматический GitHub issue с:
   - Заголовок: Sentry issue title (exception message)
   - Body: ссылка на Sentry, stacktrace summary, affected users count
   - Labels: `bug`, `sentry`

### Alert Rules

| Rule | Trigger | Action |
|------|---------|--------|
| New Error | Первое появление ошибки | GitHub issue + Telegram notification |
| High Volume | >10 events за 1 час | Telegram notification (owner) |
| Actor Failed | Dramatiq actor exhausted retries | GitHub issue |

### Telegram Notification (опционально)

Sentry Alert → Webhook → FastAPI endpoint → Telegram message:

```python
# api/routers/webhooks.py
@router.post("/webhooks/sentry")
async def sentry_webhook(payload: dict):
    """Receive Sentry alert webhooks and notify via Telegram."""
    action = payload.get("action")
    data = payload.get("data", {})

    if action == "triggered":
        issue = data.get("issue", {})
        title = issue.get("title", "Unknown error")
        url = issue.get("web_url", "")
        count = issue.get("count", 0)

        message = f"🚨 Sentry: {title}\nEvents: {count}\n{url}"
        await send_telegram_notification(message)

    return {"ok": True}
```

---

## Phases

### Phase 1: Core Integration (эта спека)

1. Добавить `sentry-sdk[fastapi,dramatiq]` в `pyproject.toml`
2. Создать `sentry_config.py` с `init_sentry()`, `before_send`, `traces_sampler`
3. Добавить `SENTRY_*` в `config.py` и `.env.example`
4. Вызвать `init_sentry()` из трёх entry points: `api/server.py`, `bot/main.py`, `tasks/worker.py`
5. User context: `set_user()` в FastAPI deps, Dramatiq actors, MCP context
6. `@sentry_tool` декоратор для MCP tools
7. Spans для Intervals.icu client
8. Global exception handler в FastAPI
9. `capture_exception()` в Telegram error handler
10. Настроить GitHub integration в Sentry UI
11. Alert rules: new error → GitHub issue

### Phase 2: Advanced (отдельная задача)

- Sentry Crons для APScheduler jobs (morning report, sync wellness, sync activities)
- Sentry webhook → Telegram notification
- Profiling (`profiles_sample_rate`)
- Frontend React integration (`@sentry/react`)
- Custom dashboards в Sentry

---

## File Changes Summary

| File | Change |
|------|--------|
| `pyproject.toml` | +`sentry-sdk[fastapi,dramatiq]` |
| `config.py` | +`sentry_dsn`, `sentry_environment`, `sentry_traces_sample_rate`, `sentry_release` |
| `.env.example` | +`SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` |
| **NEW** `sentry_config.py` | `init_sentry()`, `_before_send()`, `_traces_sampler()`, `_scrub_dict()` |
| `api/server.py` | +`init_sentry()`, +global exception handler |
| `api/deps.py` | +`sentry_sdk.set_user()` в `get_current_user()` |
| `bot/main.py` | +`init_sentry()`, +`capture_exception()` в error handler |
| `bot/agent.py` | +transaction для `ClaudeAgent.chat()` |
| `tasks/worker.py` | +`init_sentry()` (до импорта broker) |
| `tasks/actors/*.py` | +`set_sentry_user_from_dto()` в начале каждого actor |
| `mcp_server/context.py` | +`sentry_sdk.set_user()` в `set_current_user_id()` |
| **NEW** `mcp_server/sentry.py` | `@sentry_tool` декоратор |
| `mcp_server/tools/*.py` | +`@sentry_tool` на key tools |
| `data/intervals/client.py` | +spans для HTTP requests |

---

## Testing

**Unit tests:**
- `test_before_send` — проверить что sensitive keys редактируются
- `test_traces_sampler` — health check = 0, всё остальное = configured rate
- `test_sentry_disabled` — пустой DSN → `init_sentry()` no-op

**Integration check:**
```bash
# После деплоя — убедиться что events приходят:
python -c "
import sentry_sdk
sentry_sdk.init(dsn='...')
sentry_sdk.capture_message('Test event from triathlon-agent')
"
```

**Verify in Sentry UI:**
- Events появляются с правильным `environment`
- User context содержит `user_id` и `athlete_id`
- Sensitive data не видна в event details
- GitHub issues создаются при новых ошибках

---

## Existing Error Handling Gaps (to fix alongside)

При внедрении Sentry стоит закрыть найденные пробелы:

| Gap | Location | Fix |
|-----|----------|-----|
| Dramatiq actors — 3 retry then silent drop | `tasks/broker.py` | `DramatiqIntegration` ловит exhausted retries автоматически |
| No global FastAPI exception handler | `api/server.py` | Добавить `@app.exception_handler(Exception)` |
| Telegram race condition silently caught | `bot/main.py:54-56` | Добавить `sentry_sdk.capture_exception()` |
| MCP tool failures crash Claude conversation | `mcp_server/tools/` | `@sentry_tool` + graceful error return |
| Intervals.icu retry без алертинга | `data/intervals/client.py` | Breadcrumbs при retry, event при exhaustion |
