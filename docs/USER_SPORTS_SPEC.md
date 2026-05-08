# User Sports Spec

> Каждый атлет выбирает, какими видами спорта он занимается (swim / ride / run).
> Поле хранится как JSONB-массив, редактируется в Settings, новый онбординг
> блокирует доступ к данным до выбора. Цель — отойти от неявного
> «все атлеты — триатлеты» к explicit per-user sport mix.

**Related:**

| Issue / Spec | Связь |
|---|---|
| `data/db/user.py:191` | Существующее `users.primary_sport` (String(20)) — заменяем |
| `data/db/dto.py:84` | `AthleteThresholdsDTO.primary_sport` — заменяем на `sports` |
| `data/db/athlete.py:179` | Единственное место чтения; заменяем на `user.sports` |
| `webapp/src/components/OnboardingPrompt.tsx` | Существующий gate-паттерн (no athlete_id) |
| `webapp/src/App.tsx:51-85` | Цепочка auth-gates, добавляем 3-й уровень |
| `bot/prompts.py` | Будущая интеграция в `_ATHLETE_BLOCK_TEMPLATE` (вне scope этой спеки) |
| `bot/tool_filter.py` | Будущая фильтрация MCP-tools по выбранным видам (вне scope) |
| `tasks/utils.py:35` | `RampTrainingSuggestion(sports=...)` — точка инъекции фильтра в утренний отчёт |
| `tasks/actors/reports.py:100` | Вызывающий код, прокидывает выбор юзера в suggestion |

---

## 1. Мотивация

Поле `users.primary_sport` существует с миграции `k1f2a3b4c5d6` (декабрь 2025), но на 2026-05-08 его читает **ровно одно место** — `AthleteSettings.get_thresholds` копирует значение в DTO, и **никто** дальше его не использует. Промпт Claude'а, MCP tools, webapp, фильтрация — все ведут себя так, будто каждый юзер триатлет: рендерят зоны для всех трёх дисциплин, не фильтруют tools, выдают triathlon-центричные секции в утреннем отчёте.

Пользовательская реальность другая: сейчас в БД `id=1` = `triathlon`, `id=2` = `run`. По мере роста подписки появятся бегуны, велосипедисты, fitness-онлюди. Без явного per-user сигнала о виде спорта мы:

- не можем урезать промпт (триатлон-блок ~30% токенов на bike+swim для бегуна);
- не можем отфильтровать MCP tools (бегун получает swim/bike-only tools и иногда вызывает их случайно);
- не можем адаптировать секции morning/weekly report (rendering swim-CTL для не-пловца — шум);
- не можем правильно роутить ramp-test detection (только бегун = искать только Run-ramp).

Эта спека закрывает **инфраструктурный** слой: storage + API + UI + gate. Использование сигнала в промптах/tool-filter/repts — отдельной итерацией (см. §10).

---

## 2. Scope

### В этой итерации (один PR)

- Schema migration: drop `users.primary_sport`, add `users.sports JSONB nullable`.
- ORM: `User.sports: list[str] | None` + `AthleteThresholdsDTO.sports`.
- API: расширить `GET /api/auth/me` (вернуть `sports` + `available_sports_from_settings`); новый `PUT /api/auth/sports`.
- Frontend gate: новый `<SportsPicker/>` показывается, когда `sports === null`. Auto-prefill из `available_sports_from_settings`.
- Settings: новая секция «Виды спорта» с вертикальными чекбоксами (паттерн как у Language).
- **Фильтр ramp-test предложений в утреннем отчёте**: `RampTrainingSuggestion` получает `sports` из `User.sports` (с маппингом lowercase→Intervals casing). Если бегун выбрал только `["run"]` — не предлагаем Ride-ramp, и наоборот.
- i18n: ru/en строки для Settings + Picker.
- Тесты: миграция up/down, `auth_me` сериализация, `PUT /sports` валидация, smoke на SportsPicker, ramp-suggestion respects `user.sports`.

### Вне scope этой спеки

- **Прокидывание `sports` в промпт Claude** — отдельный PR, потому что меняет поведение модели и нужна regression-проверка на morning report.
- **Условный рендер `_zones_block`** — тот же PR, что и промпт.
- **Фильтрация MCP-tools** в `bot/tool_filter.py` — отдельная итерация.
- **Адаптация morning/weekly report** — после прокидывания в промпт.
- **Walk / hike / fitness** как опции — пока нет use-case'а; добавится по запросу.
- **«Основной» vs «дополнительные»** — в данных это плоский массив, никакого primary-флага. Если понадобится в промпте «основной = первый по объёму», вычислим из `AthleteSettings` на лету.

---

## 3. Data model

### Schema

```sql
ALTER TABLE users DROP COLUMN primary_sport;
ALTER TABLE users ADD COLUMN sports JSONB;  -- nullable, no default
```

`sports` — JSON-массив строк из enum `{"swim", "ride", "run"}`. Дубли запрещены, порядок не важен (UI сортирует канонически), пустой массив = «выбрал ноль» = эквивалентно NULL.

Никакого CHECK-constraint на содержимое — валидируем в Pydantic-DTO на API-границе. Защита БД-уровня была бы избыточной (одна точка входа = `PUT /api/auth/sports`, миграции под контролем), а добавит шум в тесты.

### Семантика NULL vs `[]`

- `NULL` — значение никогда не задавалось (gate ловит).
- `[]` — теоретически возможно, если фронт пошлёт пустой массив. **Запрещаем на API-уровне** (`min_items=1`), чтобы не было третьего состояния.

### ORM

```python
# data/db/user.py
sports: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
```

```python
# data/db/dto.py
class AthleteThresholdsDTO(BaseModel):
    age: int | None = None
    sports: list[str] | None = None  # was: primary_sport: str | None
    lthr_run: int | None = None
    ...
```

```python
# data/db/athlete.py:170 (get_thresholds)
dto = AthleteThresholdsDTO(
    age=user.age if user else None,
    sports=user.sports if user else None,
    ...
)
```

---

## 4. Migration

### Upgrade

`migrations/versions/<rev>_user_sports_jsonb.py`:

```python
def upgrade() -> None:
    op.drop_column("users", "primary_sport")
    op.add_column("users", sa.Column("sports", postgresql.JSONB, nullable=True))
```

### Стратегия данных

**Все существующие юзеры получают `sports = NULL`** (явное решение, см. диалог 2026-05-08). После деплоя `id=1` (owner = triathlon) и `id=2` (run) при следующем заходе в webapp пройдут через `<SportsPicker/>`. Это намеренная UX-проверка: убедиться, что gate работает, прежде чем масштабировать на новых юзеров. Owner (он же выступает в роли demo-юзера для смок-теста) пройдёт через picker сам — отдельной seed-логики для demo не нужно.

### Downgrade

```python
def downgrade() -> None:
    op.add_column("users", sa.Column("primary_sport", sa.String(20), nullable=True))
    op.drop_column("users", "sports")
```

Без data-роллбэка (старые значения уже потеряны upgrade'ом, восстанавливать нечего).

---

## 5. API

### `GET /api/auth/me` — расширение ответа

Поля поверх существующих:

```python
class AuthMeResponse(BaseModel):
    ...
    sports: list[str] | None  # null = ещё не выбрал
    available_sports_from_settings: list[str]  # ["run","ride"] на основе athlete_settings rows
```

`available_sports_from_settings` вычисляется как:

```python
all_settings = await AthleteSettings.get_all(user_id)
mapping = {"Run": "run", "Ride": "ride", "Swim": "swim"}
available = sorted({mapping[s.sport] for s in all_settings if s.sport in mapping})
```

Используется фронтом для prefill чекбоксов в SportsPicker. Если у юзера нет `AthleteSettings` (онбординг не завершён) — пустой список, picker открывается без prefill.

### `PUT /api/auth/sports` — новый endpoint

```python
class SportsUpdateRequest(BaseModel):
    sports: list[Literal["swim", "ride", "run"]] = Field(..., min_length=1, max_length=3)

    @field_validator("sports")
    def no_duplicates(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("duplicate sports")
        return sorted(set(v))  # canonical order
```

```python
@router.put("/sports", dependencies=[Depends(require_viewer)])
async def update_sports(body: SportsUpdateRequest, user_id: int = Depends(get_current_user_id)):
    await User.update_sports(user_id, body.sports)
    return {"sports": body.sports}
```

Валидации:
- `min_length=1` — пустой массив запрещён.
- `max_length=3` — больше 3-х значений не существует в текущем enum.
- `Literal[...]` — `swim`/`ride`/`run` only; неизвестные виды → 422.
- Дубли удаляются, порядок канонизируется.

`require_viewer` (не `require_athlete`) — пользователь без `athlete_id` тоже должен иметь возможность поставить sports (хотя в реальности gate-цепочка показывает Onboarding раньше; на всякий случай).

---

## 6. Frontend

### Auth-gate цепочка (`webapp/src/App.tsx`)

```
not authenticated → <Landing/>
authenticated, athleteState === 'checking' → spinner
authenticated, athleteState === 'no' → <OnboardingPrompt/>           (existing)
authenticated, athleteState === 'yes', sports === null → <SportsPicker/>  (NEW)
authenticated, athleteState === 'yes', sports !== null → routes
```

`sports` хранится в App-state рядом с `athleteState`, обновляется одним вызовом `/api/auth/me` на mount. После успешного `PUT /sports` колбэк из SportsPicker обновляет state → re-render → user попадает на основной flow.

### `<SportsPicker/>` (новый компонент)

`webapp/src/components/SportsPicker.tsx` — full-screen prompt по образцу `OnboardingPrompt.tsx`:

```
┌─────────────────────────────────────┐
│         🏊‍♂️ 🚴 🏃                    │
│   Какими видами спорта               │
│   ты занимаешься?                   │
│                                     │
│   Выбери все, что подходит.         │
│                                     │
│   ☐ 🏊 Плавание                     │
│   ☐ 🚴 Велосипед                    │
│   ☐ 🏃 Бег                          │
│                                     │
│   [    Сохранить    ]               │
└─────────────────────────────────────┘
```

- Чекбоксы вертикально, full-width, стиль как у активной/неактивной кнопки в Settings.tsx (`bg-accent text-white` для checked, `bg-surface border-border` для unchecked).
- Auto-prefill из `available_sports_from_settings` при mount (если ≥1 элемент).
- Кнопка «Сохранить» disabled пока ничего не выбрано.
- На submit: `PUT /api/auth/sports` → колбэк в App обновляет `sports` state.
- Без skip-кнопки — gate жёсткий.

### Settings секция

`webapp/src/pages/Settings.tsx`, после Language:

```tsx
<Section title={t('settings.sports.title')} icon="🏊">
  <div className="flex flex-col gap-2">
    {(['swim', 'ride', 'run'] as const).map(s => (
      <button
        key={s}
        onClick={() => toggleSport(s)}
        className={`w-full py-2.5 rounded-xl ... ${
          sports.includes(s) ? 'bg-accent text-white border-accent' : 'bg-surface ...'
        }`}
      >
        {t(`settings.sports.${s}`)}
      </button>
    ))}
  </div>
</Section>
```

Optimistic update + rollback на ошибку (паттерн `patchGoal` уже в Settings.tsx). Пустой выбор → inline error («Выбери хотя бы один»), PATCH не отправляется.

---

## 7. Morning report — ramp test filter

Утренний отчёт вызывает `RampTrainingSuggestion(user, wellness)` без `sports` —
дефолт `["Run", "Ride"]` срабатывает для всех. После этой спеки бегун, выбравший
только `["run"]`, перестаёт получать предложение Ride-теста, и наоборот.

### Точка изменения

`tasks/actors/reports.py:100`:

```python
# Before:
ramp = RampTrainingSuggestion(user=user, wellness=wellness)

# After:
ramp = RampTrainingSuggestion(
    user=user,
    wellness=wellness,
    sports=_user_ramp_sports(user.sports),
)
```

### Маппинг

`User.sports` хранится в lowercase enum (`["swim","ride","run"]`), а
`RampTrainingSuggestion` ждёт Intervals.icu casing (`["Run","Ride","Swim"]`).
Маппер живёт рядом с актором:

```python
# tasks/utils.py
_RAMP_SPORT_MAP = {"run": "Run", "ride": "Ride", "swim": "Swim"}

def _user_ramp_sports(user_sports: list[str] | None) -> list[str]:
    """Filter ramp-supported sports by athlete's selection.

    Returns the Intervals.icu-cased subset of ``["Run", "Ride"]`` (the only
    sports `create_ramp_test` currently supports — Swim is on the roadmap,
    see RAMP_TEST_SWIM_SPEC.md). Empty list → caller should skip suggestion.
    Users with ``user_sports is None`` (gate not yet passed) get ``["Run"]``
    only — Run is the most common discipline and the safer conservative
    default than the historical ``["Run","Ride"]``.
    """
    if user_sports is None:
        return ["Run"]
    supported = {"Run", "Ride"}  # add "Swim" when create_ramp_test supports it
    return [_RAMP_SPORT_MAP[s] for s in user_sports if s in _RAMP_SPORT_MAP and _RAMP_SPORT_MAP[s] in supported]
```

### Поведение по сценариям

| `user.sports` | Что предлагается |
|---|---|
| `None` (gate ещё не пройден) | `["Run"]` — консервативный дефолт, не спамит бегунам Ride-suggest |
| `["run"]` | Только Run-ramp |
| `["ride"]` | Только Ride-ramp |
| `["swim"]` | Ничего (swim-ramp пока не поддерживается в `create_ramp_test`) |
| `["swim","run"]` | Только Run |
| `["swim","ride","run"]` (триатлет) | `["Run","Ride"]` — как сейчас |
| `[]` | Невозможно: API запрещает (см. §5, `min_length=1`) |

### Защита от пустого фильтра

Когда `_user_ramp_sports(user.sports)` возвращает `[]` (например, юзер выбрал
только `["swim"]`), `RampTrainingSuggestion.is_test_needed` должен корректно
вернуть `False`. Текущая реализация в `tasks/utils.py:96` итерирует по
`self.sports` — пустой список выдаст пустой `freshness` dict, `bootstrap_sports`
будет пуст, `stale` будет пуст → `return False`. Никаких дополнительных guard'ов
не нужно, существующая логика уже handle'ит пустоту.

### Что **не** меняется

- `_is_ramp_test_activity` (`tasks/actors/activities.py:546`) — это **детектор**
  факта проведённого теста (post-activity), не предложение. Юзер может вручную
  провести Run-ramp, даже если выбрал только `["swim"]` — детектор всё равно
  его засчитает и обновит зоны.
- `create_ramp_test_tool` MCP-tool — Claude может создать ramp по запросу
  «сделай мне ramp-test», независимо от `user.sports`. Это явный intent,
  фильтр на autonomous-suggestions, не на explicit requests.
- Пост-активити zones-update в `build_ramp_test_message` — обрабатывает
  фактически проведённый тест, не зависит от `user.sports`.

### Тесты

`tests/tasks/test_ramp_suggestion.py` — добавить cases:

- `user.sports = None` → suggestion работает как раньше (`["Run","Ride"]`).
- `user.sports = ["run"]` → если Run stale → suggest Run; если Run свежий и Ride stale → `is_test_needed == False`.
- `user.sports = ["swim"]` → `is_test_needed == False` независимо от freshness.
- `user.sports = ["ride"]` + только Ride stale → suggest Ride.

---

## 8. i18n keys

### `webapp/src/i18n/{ru,en}.json`

```json
{
  "settings": {
    "sports": {
      "title": "Виды спорта",
      "swim": "🏊 Плавание",
      "ride": "🚴 Велосипед",
      "run": "🏃 Бег",
      "save_failed": "Не удалось сохранить",
      "empty_warning": "Выбери хотя бы один вид"
    }
  },
  "sports_picker": {
    "title": "Какими видами спорта ты занимаешься?",
    "description": "Выбери все, что подходит. Это влияет на рекомендации тренировок и зон.",
    "cta": "Сохранить",
    "saving": "Сохраняем..."
  }
}
```

EN-эквиваленты — параллельно. Эмодзи в строки не вшиваем (отдельные иконки в JSX) — упрощает A/B label-changes без правки эмодзи.

---

## 9. Тесты

### Backend

- `tests/db/test_user_sports_migration.py` — round-trip up/down.
- `tests/api/test_auth_me.py` — `sports` поле в ответе (null + non-null), `available_sports_from_settings` корректно отражает athlete_settings rows.
- `tests/api/test_sports_endpoint.py`:
  - 200 + canonical order на валидный `["run","swim"]` → возвращает `["run","swim"]` (отсортировано).
  - 422 на пустой массив, на `["fitness"]`, на `["run","run"]` (дубли), на `["run","ride","swim","extra"]` (>3).
  - 401 без auth.

### Frontend

- Smoke-тест: `<SportsPicker/>` рендерится, чекбоксы переключаются, кнопка disabled при пустом выборе.
- Smoke-тест: App.tsx показывает `<SportsPicker/>` когда `sports === null`, скрывает когда не-null.

Не пишем e2e на полный gate-flow — низкая ROI, паттерн уже покрыт OnboardingPrompt.

---

## 10. Risks & Mitigations

| Риск | Mitigation |
|---|---|
| Demo-юзер залочен на gate | Seed `sports = ["swim","ride","run"]` для demo-аккаунта в той же миграции |
| Существующие юзеры (id=1, id=2) удивлены повторным онбордингом | Это намеренное решение (см. §4). Сообщить заранее в личке. |
| Auto-prefill не сработает для юзеров без `AthleteSettings` (новые) | Picker открывается с пустым выбором — это нормальный UX для нового юзера |
| Webapp кеширует `sports === null` после первого PUT | После успешного PUT обновляем state в App → re-render. Тестируем явно. |
| Нагрузка на `/api/auth/me` (extra query на `AthleteSettings.get_all`) | Уже выполняется внутри `auth_me` (строка `await AthleteSettings.get_thresholds(...)`). Маппинг в `available` — in-memory, копеечная стоимость. |

---

## 11. Follow-up roadmap

После приземления этой спеки — отдельные итерации (свой PR на каждую):

### 11.1 Прокидывание в промпт

- `_ATHLETE_BLOCK_TEMPLATE`: строка `Sports: {sports}` после age.
- `SYSTEM_PROMPT_V2` / `SYSTEM_PROMPT_WEEKLY`: аналогично.
- Условный рендер `_zones_block` — печатать только секции выбранных видов.
- Regression: morning report для триатлета не должен поменяться (всё ещё `["swim","ride","run"]`), для бегуна — короче на ~30%.
- Кеш-сегменты: `sports` попадает в per-user tail (вместе с goal/zones/facts) — инвалидируется при PUT, что ок.

### 11.2 Фильтрация MCP-tools

- `bot/tool_filter.py`: для `["run"]` исключать `get_polarization_index(sport='ride'/'swim')`, swim/bike-specific tools, etc.
- Аналогично для weekly report `get_progression_analysis` — вызывать только для выбранных видов.

### 11.3 Адаптация report secций

- Morning report: для не-триатлета убрать «оценка тренировки» в стиле «swim+bike+run», делать одиночный фокус.
- Weekly: per-sport breakdown показывать только для выбранных видов.

### 11.4 Swim ramp-test поддержка

После приземления `RAMP_TEST_SWIM_SPEC.md`: добавить `"Swim"` в `supported`
set'е `_user_ramp_sports`, расширить `create_ramp_test` swim-протоколом.
Текущая спека намеренно оставляет swim out — нет рабочей реализации
swim-ramp в `data/ramp_tests.create_ramp_test`.

### 11.5 Detection routing по выбранным видам

`_is_ramp_test_activity` (post-activity) сейчас детектирует ramp-факт
независимо от `user.sports`. Если в будущем найдём false-positive (юзер
сделал интервалы, похожие на ramp, в виде, которым не занимается) —
добавить guard. Пока низкий приоритет: false-positive просто ничего
не сломает (он всё равно зачтётся как валидный тест с обновлением зон,
если математика сошлась).

---

## 12. Decisions log

| Дата | Решение | Альтернатива | Причина |
|---|---|---|---|
| 2026-05-08 | Multi-select `["swim","ride","run"]`, без `triathlon`/`fitness` | Single string + отдельный enum-тег `triathlon` | Триатлон = union трёх; отдельный тег порождает дубли (`["triathlon","run"]` — что это?). Fitness — нет use-case'а пока. |
| 2026-05-08 | `sports` в JSONB, не в отдельной таблице | `user_sports(user_id, sport)` | Массив фиксированной длины ≤3, никаких per-row атрибутов, no relational join needed. JSONB проще. |
| 2026-05-08 | Все existing → NULL | Смигрировать `triathlon → ["swim","ride","run"]`, `run → ["run"]` | Намеренная UX-проверка: пройти через gate самим, прежде чем масштабировать. |
| 2026-05-08 | Auto-prefill из `AthleteSettings` | Пустой picker всегда | Уменьшает клики для триатлета, который уже подключил Intervals. Юзер всё равно подтверждает галкой «Сохранить». |
| 2026-05-08 | Прокидывание в промпт — отдельный PR | Делать всё в одном PR | Меняет поведение Claude → нужна отдельная regression-проверка на morning report. Инфраструктура без изменения промпта безопасна для приземления. |
| 2026-05-08 | Фильтр ramp-suggestions включить в Phase 1 | Отложить в Phase 2 вместе с промптом | Зависит только от `User.sports` (без промпта), 5 строк кода + маппер. Безопасно делать сразу. |
| 2026-05-08 | `user.sports = None` → `["Run"]` only | (a) Suppress всё; (b) Legacy `["Run","Ride"]` | Morning report — фоновая cron-задача, юзер мог не открыть webapp до 7am первой ночью. Suppress всё = silent regression. Legacy `["Run","Ride"]` спамит Ride-suggest бегунам, которые ещё не зашли. Конкретно `["Run"]`: Run — самая частая дисциплина, минимум ложного шума, и после прохождения gate реальная подборка вступает в силу. |
| 2026-05-08 | Swim из ramp-фильтра пока выкидывать | Доверять `user.sports = ["swim"]` буквально и предлагать swim-ramp | `create_ramp_test` пока не поддерживает swim. RAMP_TEST_SWIM_SPEC.md существует — после его приземления убрать `Swim`-исключение из `_user_ramp_sports`. |

---

## 13. Status

- [x] Phase 1 — Schema + API + Frontend gate + Settings + ramp-suggestion filter (приземлено 2026-05-08)
- [x] Phase 2 — Прокидывание в промпт (`_ATHLETE_BLOCK_TEMPLATE`, conditional `_zones_block`) — приземлено 2026-05-08
- [ ] Phase 3 — Tool filter + report adaptation
- [ ] Phase 4 — Swim ramp-test support + detection routing
