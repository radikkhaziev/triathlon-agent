# User Sports Spec

> Каждый атлет выбирает, какими видами спорта он занимается (swim / ride / run).
> Поле хранится как JSON-массив, редактируется в Settings, новый онбординг
> блокирует доступ к данным до выбора. Цель — отойти от неявного
> «все атлеты — триатлеты» к explicit per-user sport mix.

**Status:** Phase 1+2+3 приземлены 2026-05-08. Phase 4 (Swim ramp + detection routing) gated на `docs/RAMP_TEST_SWIM_SPEC.md`.

**Anchors in code (source of truth, not the spec):**

| Слой | Файл |
|---|---|
| Schema/ORM | `data/db/user.py` (`User.sports`), `data/db/dto.py` (`AthleteThresholdsDTO.sports`) |
| API | `api/dto.py:SportsUpdateRequest`, `api/routers/auth.py:auth_me` + `set_sports` |
| Frontend gate | `webapp/src/App.tsx`, `webapp/src/components/SportsPicker.tsx`, `webapp/src/pages/Settings.tsx` |
| Sport mapping | `data/sport_map.py` (`INTERVALS_TO_LOWER` / `LOWER_TO_INTERVALS` / `RAMP_PRIORITY` / `RAMP_SUPPORTED_INTERVALS`) |
| Morning report ramp filter | `tasks/utils.py:user_ramp_sports`, called from `tasks/actors/reports.py` |
| Prompt integration | `bot/prompts.py` (`_format_sports`, `_primary_sport`, `_show_ride_progression`, `_zones_block(sports=...)`) |
| Migration | `migrations/versions/y5f6a7b8c9d0_replace_primary_sport_with_sports_jsonb.py` |
| Tests | `tests/db/test_athlete_settings.py`, `tests/api/test_auth_me.py`, `tests/api/test_sports_endpoint.py`, `tests/tasks/test_ramp_suggestion.py`, `tests/bot/test_prompts_zones.py` |

---

## 1. Мотивация

Поле `users.primary_sport` существовало с миграции `k1f2a3b4c5d6` (декабрь 2025), но его читало **ровно одно место** (`AthleteSettings.get_thresholds`), и никто дальше по коду не ветвился. Промпт Claude'а, MCP tools, webapp, фильтрация — все вели себя так, будто каждый юзер триатлет: рендерили зоны для всех трёх дисциплин, не фильтровали tools, выдавали triathlon-центричные секции в утреннем отчёте.

Пользовательская реальность другая: появляются бегуны-only, велосипедисты-only, fitness-only. Без явного per-user сигнала о виде спорта мы не могли:
- урезать промпт (триатлон-блок ~30% токенов на bike+swim для бегуна);
- адаптировать секции morning/weekly report (rendering swim-CTL для не-пловца — шум);
- роутить ramp-test suggestions (бегун получал предложения Ride-теста).

---

## 2. Scope (delivered)

- **Phase 1**: schema (`User.sports JSON`), API (`PUT /api/auth/sports` + `auth_me` extension), frontend gate (`<SportsPicker/>`), Settings секция, ramp-test filter в утреннем отчёте.
- **Phase 2**: `Sports: …` линия в трёх промпт-шаблонах (morning, weekly, chat tail) + conditional `_zones_block` (бегун видит только Run-зону).
- **Phase 3**: параметризация hardcoded `sport='run'` / `sport='Ride'` в morning/weekly промтах через `_primary_sport(sports)`; conditional Ride-блоки (`_show_ride_progression`).

Цель **достигнута**: триатлет видит legacy-вывод (regression-tested), runner-only / cyclist-only — урезанный промпт без irrelevant Ride/Swim шума.

---

## 3. Open risks

Все риски из исходной спеки разрулены. Единственный остаточный — **demo asymmetry**: `auth_me` пинит demo `sports=["ride","run","swim"]`, но prompt-path читает `User.sports` напрямую. Закрывается рекомендацией «keep demo's DB row NULL» (документировано в `api/routers/auth.py:266`).

---

## 4. Follow-up roadmap

### 4.1 Swim ramp-test support (Phase 4 — pending)

После приземления `RAMP_TEST_SWIM_SPEC.md`:
- Добавить `"Swim"` в `RAMP_SUPPORTED_INTERVALS` (`data/sport_map.py`).
- Расширить `data/ramp_tests.create_ramp_test` swim-протоколом (CSS).
- Текущий filter (`user_ramp_sports`) автоматически начнёт пропускать Swim в suggestion'ы.

### 4.2 Detection routing по выбранным видам (low priority)

`_is_ramp_test_activity` (`tasks/actors/activities.py`) сейчас детектирует ramp-факт независимо от `user.sports`. Если найдём false-positive (юзер сделал интервалы похожие на ramp в виде, которым не занимается) — добавить guard. Пока низкий приоритет: false-positive просто зачтётся как валидный тест с обновлением зон, если математика сошлась.

---

## 5. Decisions log

Durable knowledge — почему мы выбрали именно эти решения. Сохраняется даже когда implementation детали устаревают.

| Дата | Решение | Альтернатива | Причина |
|---|---|---|---|
| 2026-05-08 | Multi-select `["swim","ride","run"]`, без `triathlon`/`fitness` | Single string + отдельный enum-тег `triathlon` | Триатлон = union трёх; отдельный тег порождает дубли (`["triathlon","run"]` — что это?). Fitness — нет use-case'а пока. |
| 2026-05-08 | `sports` в JSON, не в отдельной таблице | `user_sports(user_id, sport)` | Массив фиксированной длины ≤3, никаких per-row атрибутов, no relational join needed. JSON проще. |
| 2026-05-08 | Все existing юзеры → `sports=NULL` на миграции | Смигрировать `triathlon → ["swim","ride","run"]`, `run → ["run"]` | Намеренная UX-проверка: пройти через gate самим, прежде чем масштабировать. Owner в роли demo-юзера для смок-теста. |
| 2026-05-08 | Auto-prefill из `AthleteSettings` в SportsPicker | Пустой picker всегда | Уменьшает клики для триатлета, который уже подключил Intervals. Юзер всё равно подтверждает кнопкой «Сохранить». |
| 2026-05-09 | **Reverted:** пустой picker всегда | Auto-prefill (см. строку выше) | Auto-prefill делал «Сохранить» сразу активной у триатлета с synced `AthleteSettings`, юзеры жаловались на неочевидность («что от меня хотят?»). Empty start + disabled Save до явного клика — однозначнее визуально. Удалено: `available_sports_from_settings` в API response, `AthleteThresholdsDTO.available_sports` поле + derive-логика в `get_thresholds`, `prefill` prop у `SportsPicker`, тесты `TestGetThresholdsAvailableSports` и три `test_available_sports_*` (148 удалённых строк). Re-edit flow остался в Settings, без изменений. |
| 2026-05-08 | Прокидывание в промпт — отдельный PR (Phase 2) | Делать всё в одном PR | Меняет поведение Claude → нужна отдельная regression-проверка на morning report. Инфраструктура без изменения промпта безопасна для приземления. |
| 2026-05-08 | Фильтр ramp-suggestions включить в Phase 1 | Отложить в Phase 2 вместе с промптом | Зависит только от `User.sports` (без промпта), 5 строк кода + маппер. Безопасно делать сразу. |
| 2026-05-08 | `user.sports = None` → `["Run"]` only в `user_ramp_sports` | (a) Suppress всё; (b) Legacy `["Run","Ride"]` | Morning report — фоновая cron-задача, юзер мог не открыть webapp до 7am первой ночью. Suppress всё = silent regression. Legacy `["Run","Ride"]` спамит Ride-suggest бегунам, которые ещё не зашли. `["Run"]` — Run самая частая дисциплина, минимум ложного шума. После прохождения gate реальная подборка вступает в силу. |
| 2026-05-08 | Swim из ramp-фильтра выкидывать пока | Доверять `user.sports = ["swim"]` буквально и предлагать swim-ramp | `create_ramp_test` пока не поддерживает swim. RAMP_TEST_SWIM_SPEC.md существует — после его приземления убрать `Swim`-исключение из `RAMP_SUPPORTED_INTERVALS`. |
| 2026-05-08 | §11.2 tool-list filter — SKIPPED | Добавить sport-аргумент-aware фильтр в `bot/tool_filter.py:TOOL_GROUPS` | В `TOOL_GROUPS` нет sport-specific tool-имён (нет `*_run`/`*_ride`/`*_swim`). Sport-routing идёт через `sport=` arg в general-purpose tools (`get_polarization_index`, `get_progression_analysis`). Ограничение задаётся через prompt-context (Phase 2 `Sports:` line + `_zones_block` filter + Phase 3 `{primary_sport}` подстановка). Tool-list trim не нужен. |
| 2026-05-08 | Phase 3: hardcoded `sport='run'` → `{primary_sport}` placeholder | Оставить hardcode и доверять Claude инферить из `Sports:` line | Hardcode `sport='run'` для cyclist-only юзера активно вредит — Claude послушно вызовет `get_polarization_index(sport='run')` и получит пусто. Параметризация: 3 строки в шаблоне + helper. Триатлет: legacy ≡ new (Run priority по `RAMP_PRIORITY`). |
| 2026-05-08 | Weekly Ride-блоки conditional через `{format_sections_tail}` (rebuild per branch) | Numbering gap 1,2,3,4,6,7 для не-Ride юзера | Claude tends to renumber visible output; "section 5 missing" jump в Telegram-отчёте смущает читателя. Rebuild tail per branch — 5/6 для не-Ride, 5/6/7 для Ride. |
| 2026-05-08 | `RAMP_PRIORITY = ("Run","Ride","Swim")` для tie-break в ramp-suggestion | Сохранять порядок из `user.sports` | API canonicalises `user.sports` алфавитно (`["run","ride"]` → `["ride","run"]`); без priority-проекции tie-break biased к Ride для триатлета. Run-first matches legacy expectation. |

---

## 6. Status

- [x] Phase 1 — Schema + API + Frontend gate + Settings + ramp-suggestion filter (приземлено 2026-05-08)
- [x] Phase 2 — Прокидывание `Sports:` линии в три промпт-шаблона + conditional `_zones_block` (приземлено 2026-05-08)
- [x] Phase 3 — `{primary_sport}` параметризация в morning/weekly + conditional Ride-блоки. **§11.2 tool-list filter SKIPPED** — обоснование в Decisions log. Приземлено 2026-05-08.
- [ ] Phase 4 — Swim ramp-test support + detection routing (gated на `RAMP_TEST_SWIM_SPEC.md`)
