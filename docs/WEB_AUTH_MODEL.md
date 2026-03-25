# Web App — Auth Model

> Три уровня доступа: анонимный, друг (Telegram), владелец (Telegram).

---

## Роли

| Роль | Условие | Доступ |
|---|---|---|
| **anonymous** | Нет initData | Только `index.html` (лендинг) с кнопкой "Open in Telegram" |
| **viewer** | initData валидный, `user.id != TELEGRAM_CHAT_ID` | Чтение: report, plan, activities, activity details. Без sync кнопок |
| **owner** | initData валидный, `user.id == TELEGRAM_CHAT_ID` | Полный доступ: всё viewer + sync кнопки + POST job triggers |

`TELEGRAM_CHAT_ID` — из `config.settings`, уже используется в боте.

---

## Backend

### Новые helper-функции в `api/routes.py`

Заменить текущий `_verify_request()` на две функции:

#### `_get_user_role(authorization) -> str`

```python
def _get_user_role(authorization: str | None) -> str:
    """Determine user role from Telegram initData.

    Returns: "owner", "viewer", or "anonymous"
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not bot_token or not authorization:
        return "anonymous"
    if not verify_telegram_init_data(authorization, bot_token):
        return "anonymous"

    # Extract user.id from initData
    parsed = parse_qs(authorization)
    user_json = parsed.get("user", [None])[0]
    if not user_json:
        return "anonymous"

    import json
    user = json.loads(user_json)
    user_id = str(user.get("id", ""))

    if user_id == str(settings.TELEGRAM_CHAT_ID):
        return "owner"
    return "viewer"
```

#### `_require_viewer(authorization) -> str`

Для GET endpoints — требует минимум viewer. Возвращает role.

```python
def _require_viewer(authorization: str | None) -> str:
    role = _get_user_role(authorization)
    if role == "anonymous":
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    return role
```

#### `_require_owner(authorization) -> None`

Для POST endpoints — требует owner.

```python
def _require_owner(authorization: str | None) -> None:
    role = _get_user_role(authorization)
    if role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
```

### Изменения в endpoints

**GET endpoints** — добавить авторизацию (минимум viewer):

```
GET /api/report                  → _require_viewer(authorization)
GET /api/scheduled-workouts      → _require_viewer(authorization)
GET /api/activities-week         → _require_viewer(authorization)
GET /api/activity/{id}/details   → _require_viewer(authorization)
```

Добавить `role` в ответ каждого GET endpoint:
```json
{
  "role": "owner",
  ...existing fields...
}
```

Фронтенд использует `role` чтобы показать/скрыть sync кнопки.

**POST endpoints** — заменить `_verify_request` на `_require_owner`:

```
POST /api/jobs/sync-workouts     → _require_owner(authorization)
POST /api/jobs/sync-activities   → _require_owner(authorization)
POST /api/jobs/morning-report    → _require_owner(authorization)
POST /api/jobs/sync-wellness     → _require_owner(authorization)
```

**GET /health** — без авторизации (публичный).

### Обратная совместимость

Удалить старый `_verify_request()` — заменён на `_require_viewer` / `_require_owner`.

---

## Frontend

### `index.html` — три состояния

**anonymous (нет initData):**
- Одна кнопка: "Open in Telegram" (primary)
- Нет кнопок Dashboard / Plan / Activities

**viewer (initData, не owner):**
- Кнопки: Dashboard, Training Plan, Activities (read-only pages)
- Кнопка "Open in Telegram" (secondary)

**owner (initData, user.id == CHAT_ID):**
- Те же кнопки что viewer — визуально идентично
- Разница проявляется на внутренних страницах (sync кнопки)

Логика определения owner на index.html:
- Проверять `role` не нужно — index.html показывает одинаковые кнопки для viewer и owner
- Достаточно проверки `hasAuth` (есть initData) — как сейчас

### `plan.html`, `activities.html` — sync кнопки по роли

При загрузке данных API возвращает `"role": "owner"` или `"role": "viewer"`.

```javascript
const data = await resp.json();
if (data.role === 'owner') {
    syncBtn.style.display = 'inline-flex';
} else {
    syncBtn.style.display = 'none';
}
```

Для viewer — скрыть кнопки Sync. Остальной контент идентичен.

### `report.html` — аналогично

Если в будущем добавим кнопки "Trigger morning report" — показывать только owner.

### `activity.html` — без sync кнопок

Нет мутирующих действий — одинаков для viewer и owner.

### Auth gate (без initData → redirect)

Текущая логика: страницы проверяют `hasAuth` и блокируют без initData. Это остаётся — anonymous не может открыть внутренние страницы напрямую.

---

## Что НЕ делать

- Не добавлять десктоп-авторизацию без Telegram (token-based login) — это отдельная задача (#10 в Next Steps)
- Не создавать таблицу users — single-tenant, owner определяется по TELEGRAM_CHAT_ID
- Не добавлять авторизацию на MCP endpoints — у них свой Bearer token
- Не менять Telegram bot логику

---

## Порядок реализации

1. Backend: `_get_user_role()`, `_require_viewer()`, `_require_owner()` в `api/routes.py`
2. Backend: GET endpoints — добавить `_require_viewer`, вернуть `role` в ответе
3. Backend: POST endpoints — заменить `_verify_request` на `_require_owner`
4. Frontend: `plan.html` — скрыть Sync если `role != "owner"`
5. Frontend: `activities.html` — аналогично
6. Проверить: открыть от другого Telegram пользователя — видит данные, не видит Sync
