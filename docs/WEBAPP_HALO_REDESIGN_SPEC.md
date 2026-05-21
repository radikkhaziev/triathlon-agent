# Webapp Halo Redesign Spec

> Полный визуальный редизайн webapp (направление **B «Halo»**) пришёл
> готовым пакетом: React+inline-style прототипы 14 экранов + handoff-README
> с фазовым rollout. Спека фиксирует **что нашёл ревью при сверке с реальной
> схемой бэкенда** — блокеры, открытые продуктовые решения, бэкенд-задачи и
> фронтовый/IA punch-list — чтобы ничего не потерять между ревью и спринтом.
> Это **не** сам редизайн (он в пакете), а слой prerequisites + дельта-задачи.

**Status (2026-05-18, текущий снапшот):**
- **Full-fidelity-заход (2026-05-18, по явному запросу «хочу видеть дизайн дизайнера»):** ✅ **Nav=A** (`HaloBottomTabs` смонтирован, legacy bar удалён) · ✅ **L1 clearance** (content-under-bar пофикшен, 9 wrappers) · ✅ **G1=B-узкий** (Settings Personal = прототип: Weight + per-sport HR-max read-only, БЕЗ миграции; backend `auth_me +weight +hr_max`, контракт-тест 10/10) · ✅ **Settings full structural port** («settings не отвечает дизайну» → перевёрстан в композицию прототипа: identity-Card, `Panel`/`StatTile` вместо эмодзи-`Section`, порядок Profile→Personal→Thresholds→Intervals→Goals→Sports→Language→MCP→id-footer; **логика байт-в-байт**, load-bearing секции сохранены — см. F-Settings-port). Все — code-reviewer **APPROVE**, 0 Crit/High. Коммита по-прежнему нет (по просьбе).
- **Гейты:** G1=**A→B-узкий** (B приземлён 2026-05-18) · G2=A · G3=(b) — закрыты.
- **Бэкенд:** BE-1/BE-3 = no-op (G1/G2=A). **BE-2 + BE-2b ✅ закрыты** — enum (`green|yellow|red|insufficient_data`) и шкала `banister_recovery` 0–100 подтверждены из кода, фикстуры выровнены, контракт-тест `tests/api/test_wellness_day.py` (5/5). BE-4 намеренно не открыт.
- **Re-skin:** Phase 0/1/2/5/7 + Phase 4 (Plan/Workout) приземлены; holistic cross-phase ревью — SHIP-READY. Коммита нет (по просьбе).
- **Активный бэкенд-бэклог: пуст.** **Блокеров планирования нет.**
- **🎉 ВСЯ ПРОГРАММА HALO ЗАВЕРШЕНА (2026-05-17).** Phase 0/1/2/3/4(Plan/Workout **+ merged-week**)/5/6/7 приземлены; все 8 гейтов закрыты (G1/G2/G3, BE-2/2b, F15=A, F2=A); holistic + per-phase ревью пройдены.
- **⚠️ СТАТУС «завершена» ПЕРЕСМОТРЕН (2026-05-18, явный запрос «нам сделать дизайн точь в точь»):** ревью показало, что Phase 3/5/6/7 (Wellness частично, Plan/Activities/Activity/Dashboard/Progress/Weekly) были **token-swap re-skin**, НЕ структурный порт композиции прототипа — только Settings (+ частично Wellness/MergedWeek) реально перевёрстаны. Запущен **Full-Fidelity Structural Port** — см. **§9** (новый раздел, source of truth по этому заходу). Коммита по-прежнему нет (ждёт пользователя). **Без GitHub-issues** — трекинг только здесь.

**Anchors in code / package (source of truth, not the spec):**

| Слой | Путь |
|---|---|
| Дизайн-пакет | `design-package/endurai/` (handoff: `design_handoff_endurai_halo/README.md`) |
| Прототипы экранов | `direction-b-halo.jsx` (7 осн.), `direction-b-extras.jsx` (Settings/Login/…), `direction-merged.jsx` (merged-week), `shared.jsx` (DATA + RECOVERY map) |
| Фикстуры API | `design-package/endurai/uploads/sample-data.json` |
| Profile API | `api/routers/athlete.py:121` (`patch_athlete_profile`, только `age`), `api/dto.py:120` (`AthleteProfilePatchRequest`) |
| Goal API | `api/routers/athlete.py:58` (`patch_athlete_goal` — `ctl_target`/`per_sport_targets`/`sport_type`, см. `:115`) |
| Profile schema | `data/db/user.py:208` (`age`), `:372` (`update_age`) — нет `sex`/`height`/`resting_hr` |
| Per-sport thresholds | `data/db/athlete.py:61` (`athlete_settings.max_hr`, per-sport), `:229` `ctl_target`, `:230` `per_sport_targets` |
| Wellness status | `api/routers/wellness.py:70,106` (passthrough `*.status`), `:63,99` (`insufficient_data`), `:158` (`readiness_level`) |
| Status enum source | `data/metrics.py:97` (`green|yellow|red`), `tasks/formatter.py:80` (`STATUS_EMOJI`), `:41` (`CATEGORY_DISPLAY`) |
| Morning report | `tasks/tools.py:791` (`generate_morning_report_via_mcp` → `wellness.ai_recommendation`, free-form string) |

---

## 1. Мотивация

Пакет помечает Phase 1 (Settings) как «lowest risk, no live data». Сверка с
кодом показала обратное: новая **Personal-карточка** speccит 6 полей, из
которых 4 не существуют в схеме. Если не зафиксировать это до спринта —
Phase 1 уходит в скрытую миграцию. Аналогично: фикстура `sample-data.json`
рассинхронена с реальным enum статусов (тихий серый светофор на герое), а
AI-рекомендация в дизайне — статичная вёрстка, не воспроизводимая из строки
API. Эти находки легко потерять между ревью-диалогом и планированием.

---

## 2. Scope

**В scope:** prerequisites для порта Halo — бэкенд-дельты, продуктовые гейты,
исправление фикстур, punch-list найденных проблем (включая фронтовые, чтобы
не забыть). **Не в scope:** сам порт экранов (фазовый план — README §13),
новые routes (их нет), смена стека.

---

## 3. Продуктовые гейты

| # | Решение | Выбор | Что значит |
|---|---|---|---|
| G1 | **Personal-карточка Settings** (блокер 1) | ✅ **A → B-узкий** (B приземлён 2026-05-18) | Изначально A (бэкенд 0, только Age). **Full-fidelity-заход переоткрыл → G1=B-узкий приземлён:** GET-профиль `+weight +hr_max`(per-sport, read-only, БЕЗ миграции), Settings Personal = прототип (Age edit + Weight + HR-max grid). sex/height/rhr-миграция остаётся архивом (нет спроса). Детали §4.1. F13 обновлён (теперь реально рендерится). |
| G2 | **Morning report формат** (блокер 3) | ✅ **A** (2026-05-17) | Фронт рендерит plain `ai_recommendation`; stat-вставки строит из числовых полей wellness-day. Блок «How this score is built» остаётся. Бэкенд = 0 (BE-3 no-op). Дизайн-следствие → F14. **Приземлено в дизайне Rev.2.** |
| G3 | **Recovery «voice» на Wellness-герое** (остаток F3) | ✅ **(b)** (2026-05-17, wave-2) | Выбрана ветка (b) — совпала с рекомендацией спеки. AI-строка убрана с Wellness-героя (`direction-b-halo.jsx:342–344` — карточка заменена на комментарий «do not duplicate»), зарезервирована под будущий `/coach`-view. chip+rec (из `RECOVERY` map) — единственный источник «что делать сегодня». **Приземлено в дизайне.** Следствие: 4 derived-chip'а ушли вместе с AI-карточкой — не регресс, HRV/RHR/CTL-ATL-TSB остались отдельными карточками. Остаток UX → F17. |

**Все три гейта закрыты.** G1/G2 = ветка A, G3 = ветка (b) (совпала с
рекомендацией). B-ветки G1/G2 в §4.1/§4.3 — архив на реверс. G3 (b)
породил follow-up: будущий `/coach`-view (отдельный раунд дизайна, вне
этого редизайна — README «Still open · G3 follow-up»). Текущий статус
прохождения фаз и оставшиеся gated-зависимости — в §8 (ledger истины).

---

## 4. Бэкенд-задачи

### 4.1 BE-1 — Personal-карточка: схема и эндпоинт `[gated G1]`

Реальность: `users.age` (`data/db/user.py:208`), `PATCH /api/athlete/profile`
принимает **только** `age` 18–90 (`api/dto.py:120`). `Weight` — из `wellness`
(Intervals), не из профиля. `HR max` — `athlete_settings.max_hr` **per-sport**
(`data/db/athlete.py:61`), авто-синк. `Sex`/`Height`/`HR rest` — колонок нет.

- **G1=A — ВЫБРАНО (2026-05-17): бэкенда 0.** GET-профиль отдавал
  `{age,lthr_run,lthr_bike,ftp,css}`; фронт размечал read-only. **Переоткрыто
  в full-fidelity-заходе (2026-05-18): G1=B-узкий приземлён** — пользователь
  захотел точную Personal-карточку прототипа (Weight + per-sport HR-max).
- **G1=B — узкий срез ✅ ПРИЗЕМЛЁН (2026-05-18), БЕЗ миграции:**
  - [x] GET-профиль `+weight` (последний `Wellness.get_latest_weight` — новый `@dual`-метод, паттерн как `get`) `+hr_max` (per-sport из `AthleteSettings.get_all`, маппинг Run→run/Ride→bike/Swim→swim, read-only). `api/routers/auth.py:auth_me`, `data_uid` (tenant-safe). Контракт-тест `test_auth_me.py` (+`TestProfilePersonalFields`, partial-sport; 10/10). code-reviewer **APPROVE** (Medium `toFixed(1)` пофикшен).
  - [x] Settings.tsx Personal → прототип `direction-b-extras.jsx:101–150`: Age editable (как было) + Weight read-only (kg) + HR-max 3-col grid Swim/Bike/Run (read-only, bpm). Section-hint `auto_sync_intervals`. +6 i18n EN/RU. **Дев. от прототипа:** per-field provenance-подписи свёрнуты в один section-hint (`Row` без sub-slot — идиома shipped Thresholds); Age sub-text не добавлен (не трогаем shared `EditableNumberRow`, исп. goals тоже).
- **G1=B — sex/height/resting_hr (НЕ активно, архив):** требует миграцию `users.sex/height_cm/resting_hr` + DTO/PATCH/`update_*`. Продуктового спроса нет → push-back-on-over-engineering держится. Развернуть только по явному запросу.

### 4.2 BE-2 — wellness-day status enum `[блокер 2]` — ✅ ЗАКРЫТО 2026-05-17

Бэкенд-код **верен** (`wellness.py:_hrv_block/_rhr_block` passthrough
`hrv_row.status`; `data/db/hrv.py` колонка прямо помечена
`# green | yellow | red | insufficient_data`; `metrics.py:100-104` enum;
`insufficient_data` sentinel при отсутствии analysis-строки). Баг был
**только в фикстуре** — `sample-data.json` клал `"balanced"` (это
`recovery.readiness_level`, отдельное поле, не `hrv.status`).

- [x] `uploads/sample-data.json`: `hrv.status`/`rhr.status` `balanced → yellow`; `recovery.readiness_level` оставлен `balanced` (легитимен). JSON валиден.
- [x] Контракт-тест `tests/api/test_wellness_day.py::TestStatusEnum` (3 кейса: insufficient_data-путь, green/yellow passthrough, **«readiness никогда не течёт в *.status»**-guard). 5/5 зелёные.
- [x] handoff-README §«Still open #2» помечен RESOLVED + правило «map real API enum, `readiness_level ≠ *.status`».
- **Acceptance достигнут:** тест падает, если сериализатор положит readiness-словарь в `*.status`.

#### BE-2b — шкала `stress.banister_recovery` — ✅ ЗАКРЫТО 2026-05-17

Подтверждено из кода: **0–100** (не 0–1).
`data/metrics.py:calculate_banister_recovery` клампит `max(0, min(100, r))`;
`combined_recovery_score` клампит `min(100, …)`. Истина была на стороне
`shared.jsx` (`68`); ошибалась `sample-data.json` (`0.68`).

- [x] Шкала подтверждена из `data/metrics.py` (бэкенд-код менять не нужно — рекомендация README «0–100 на API» уже = реальность).
- [x] `sample-data.json` `banister_recovery 0.68 → 68.0`; `shared.jsx` уже `68.0` (верно, оставлен).
- [x] Контракт-тест `tests/api/test_wellness_day.py::TestBanisterScale` (значение проходит unscaled, ∈ [0,100], > 1 — guard от 0–1-дроби). Зелёный.
- [x] handoff-README §«Still open #1» помечен RESOLVED.
- **Acceptance достигнут:** breakdown `${banister} / 100` корректен (~68–80, не «1»).

### 4.3 BE-3 — Morning report формат `[gated G2]`

`ai_recommendation` = свободная строка от Claude (`tasks/tools.py:791`).

- **G2=A — ВЫБРАНО (2026-05-17): бэкенда 0.** Фронт рендерит строку +
  строит chip'ы из числовых полей. BE-3 закрыт как no-op. Дизайн-дельта
  вынесена в F14. B-ветка ниже — архив на случай реверса решения.
- **G2=B (не активно):**
  - [ ] Изменить промпт morning-report → структура `{summary, callouts[], today_directive}` или markdown.
  - [ ] Хранение: новое поле/JSON рядом со строкой; решить миграцию `wellness`.
  - [ ] Учесть инвалидацию prompt-кэша (правка `bot/prompts.py` → cache miss; см. CLAUDE.md).

### 4.4 BE-4 — Серверный matched-week `[опционально — НЕ делать без запроса]`

Блокер 5 чинится на фронте (см. §5). Альтернатива (только если клиентский
матчинг окажется хрупким): отдать `/api/week-merged` поверх `training_log`
(уже хранит pre/actual/post + compliance + `race_id` FK) с честным
compliance. Нарушает «no API changes» README — фиксируем как «знаем
альтернативу, если припрёт», по умолчанию **не открываем**.

---

## 5. Фронтовый / IA punch-list (НЕ бэкенд — чтобы не потерять)

Статусы по Rev.2 (проверено по коду `direction-b-*.jsx`, не по changelog):
✅ приземлено в дизайне · 🔁 переведено в гейт · ⏳ отложено осознанно ·
🛠 implementation-time (дизайн-правок не требует, зафиксировано в README «Still open»).

| # | Ст | Экран | Проблема | Действие / что сделано |
|---|---|---|---|---|
| F1 | ✅ част. | Nav | `/progress` не имел точки входа. | Добавлен drill-down «Detailed trends» (`a href="#progress"` → на проде `<Link to="/progress">`). **Остаток → F16.** До Phase 7. |
| F2 | ✅ closed + built | Merged-week | **Reconcile (A, day-level) + Phase-4 merged-week ПОСТРОЕН 2026-05-17.** Логика: `buildWeek()` — нет 1:1 sport-матчинга, все факты дня закрывают план дня (брик-нога ≠ extra), `compliance=Σ/Σ`, `isRace`/`unplanned`. **Webapp:** новый `pages/MergedWeek.tsx` (own useWeekNav + 2×useApi `scheduled-workouts` ∪ `activities-week`, Halo Planned\|Done day-cards + roll-up, tap→`/workout|/activity`); `Plan.tsx`→`PlanList` (chrome снят, логика byte-identical); `PlanScreen.tsx` = `/plan`-хост c сегмент-тогглом **[Week·Plan], Week default** (mount=B, no new routes — README §3.5). +`merged.*` i18n EN/RU. code-reviewer: **APPROVE**, F2=A parity verified unit-correct; webapp намеренно НЕ дропает typeless-факты (улучшение vs прототип, закомментировано). |
| F3 | 🔁→G3 | Wellness | Три «голоса» об одном состоянии. | Декоративный `recovery.title` убран ✅. Остаток (AI-строка vs детерминир. chip+rec) эскалирован в **продуктовый гейт G3**. |
| F4 | ✅ | Wellness | Sleep-стадии / VO₂max-Δ / Weight-trend — данных нет. | Sleep → одиночный circular gauge от `score`; фейковые sparkline убраны. Аудит остальной микрокопии — README «Still open #3». |
| F5 | ✅ | Activity | ESS капался на 100. | Шкала 0–200, tick на 100 («1h at LTHR»), без clip. Проверено: `direction-b-halo.jsx:677–685`. |
| F6 | 🛠 | Workout | %→абсолют зашит магией; run-нога брика = `lthr_run`. | README «Still open #6» — на проде `thresholds.{ftp,lthr_bike,lthr_run}` per-step. Дизайн-правок нет. |
| F7 | 🛠 | Merged-week | `actualMin/planMin` врёт для off-plan интенсивности. | README «Still open #5» — на проде брать бэкендный `compliance`. |
| F8 | 🛠 | Все | EN/RU микс. | README «Still open #7» — дефолт locale RU, EN = fallback. i18n-задача impl-фазы. |
| F9 | 🛠 | Wellness | Date-strip = 4 фикс-пилюли. | README «Still open #10» — на проде `‹ Today ›` степпер (как на Plan). |
| F10 | ✅ | Settings | Per-sport CTL `current>target` неотличимо от «ровно». | Overshoot-хвост (faded, opacity .35) + `taper`-chip. Проверено: `direction-b-halo.jsx:783–798`. |
| F11 | 🛠 | Charts | Chart.js mandate vs inline-SVG. | README «Still open #9» — gauges SVG, line/scatter через Chart.js на проде. |
| F12 | 🛠 | What's-new | localStorage vs Telegram `CloudStorage`. | README «Still open #8» — swap 1 строкой, если важен кросс-девайс. Не блокер. |
| F13 | ✅ реально приземлён (G1=B-узкий, 2026-05-18) | Settings | Карточка = Age editable + **Weight read-only (kg)** + **HR-max per-sport grid Swim/Bike/Run read-only (bpm)**, section-hint «auto-sync · Intervals». Sex/Height/HR-rest нет. **Больше не «дизайн-дельта без рендера» — фактически отрисовано** (`Settings.tsx` Personal, прототип `direction-b-extras.jsx:101–150`). Бэкенд `auth_me +weight +hr_max` без миграции. |
| F14 | ✅→G3 | Wellness | **Следствие G2=A → переиграно G3=(b).** | В Rev.2 AI-карточка = plain `w.ai` + 4 derived-chip'а. В wave-2 (G3=b) **вся AI-карточка убрана** с героя (`:342–344`). Chip'ы ушли с ней — НЕ регресс: HRV/RHR — отдельные парные карточки, CTL/ATL/TSB — карточка Training load. |
| F15 | ✅ решён (A) | Dashboard·Load | **Reconcile сделан 2026-05-17, ветка A (привести к CLAUDE.md, 4 зоны).** `shared.jsx` TSB-карта переписана точно по CLAUDE.md «Business Rules»: `<−25 overtrain · −25..−10 overreach · −10..+10 optimal · >+10 undertrain` (граница −30→**−25** исправлена; fresh/peak split убран — методология проекта = 4 зоны; CLAUDE.md НЕ менялся). Miscited-комментарий («Units contract»→«Business Rules») и устаревший «Used for: AI-chips» поправлены. Прототип-band в `direction-b-halo.jsx:800–830` приведён к 4 сегментам + тики −25/−10/+10. Демо −7.8 = OPTIMAL (без видимой регрессии). **Phase 6 приземлён.** ⚠️ Спековая заметка «перенести в webapp-util» **УСТАРЕЛА**: webapp уже имеет CLAUDE.md-конформный `TsbZoneBadge`/`tsbZone`/`TSB_ZONE_COLORS` (Dashboard.tsx, «bands match `data/utils.py:tsb_zone`») — F15 был багом ТОЛЬКО дизайн-фикстуры; `utils/tsb.ts` НЕ нужен (создание = дубль source-of-truth). **Не форкать.** |
| F16 | ✅ | Nav | **wave-2: закрыт.** Drill-down подтверждён в **Load-табе**. |
| F1/F16 финал | ✅ закрыт (A, 2026-05-17) | Nav | **Full-fidelity-заход: `HaloBottomTabs` смонтирован.** Решение A — точная IA прототипа: 5 табов **Today→/wellness · Plan→/plan · History→/activities · Trends→/dashboard · Profile→/settings** (`lib/navItems.HALO_BOTTOM_TABS`). Legacy эмодзи-`BottomTabs` + «Ещё»-меню **удалён** (zero callers; changelog-«что нового» переехал на Wellness-баннер в Phase 3). `Layout` рендерит Halo-бар `fixed bottom md:hidden`; Sidebar (desktop) остался legacy — вне mobile-Halo-скоупа (бриф: desktop = bonus). `/progress`+`/weekly` не в баре (drill-down, роуты живы). code-reviewer: **APPROVE**, 0 Critical/High. |
| F17 | ✅ | Wellness | **Закрыт в Phase 3.** Детерминированный `<Link to="/plan">` CTA «→ today's plan» на recovery-карточке (`Wellness.tsx`, i18n `wellness.see_plan`). |
| F-Settings-port | ✅ закрыт (full structural port, 2026-05-18) | Settings | **Phase-1 был token-re-skin легаси-структуры; по явному запросу пользователя («settings не отвечает дизайну» → выбран «полный структурный порт») Settings.tsx перевёрстан в композицию прототипа** `direction-b-extras.jsx`: TopBar «Profile»+role-eyebrow → **identity-Card** (gradient-аватар-монограмма от `athlete_id`, `@id`, age/role-sub, role-pill) → Personal → **Thresholds 2-кол `StatTile`-grid** → Intervals (компакт-shell, вся OAuth-стейт-машина verbatim) → Goals → Sports → Language → MCP → centered id-footer → logout. Легаси `Section({title,icon-эмодзи})` **удалён** → `Panel({label,hint?})` (`Card`+`MicroLabel`-eyebrow, без эмодзи) + `StatTile`. **Логика байт-в-байт** (diff: строки 72-351 — все handlers/effects/seq-guards/`apiFetch` — не тронуты; подтверждено code-reviewer'ом по diff). code-reviewer **APPROVE**, 0 Crit/High. **Точная формулировка (L1/L2):** Personal+Thresholds — НЕ чистый wrapper-swap: вобрали G1=B (weight/hr_max) + полный i18n хардкод-лейблов + split на 2 Panel + `Row`→`StatTile`; гард `lthr_run/lthr_bike` ужесточён truthy→`!= null` (осознанная корректность: stored `0` теперь виден, не глотается). **Deviations (залогировано):** (1) identity-Card data-honest — на payload нет name/@handle/TG-id, поэтому аватар-монограмма от `athlete_id`, primary=`@athlete_id`, footer только Intervals-id (прецедент: G1=B section-hint / MergedWeek typeless); (2) role показан дважды (TopBar-eyebrow + identity-pill) — было намеренно, но **✅ закрыто в §9-review-проходе (2026-05-18) по запросу**: TopBar role-`right` убран (остался только identity-pill); TopBar title=литеральный `"Profile"` (EN-only, не локализуется в «Профиль»), dead-key `settings.profile_title` удалён из en/ru; лого TopBar = реальный `/endurai-icon.png` (фикс в самом примитиве `TopBar` → глобально на всех экранах, прежде рисовался пустой кобальт-плейсхолдер); (3) legacy `React.ReactNode` без import — pre-existing, tsc проходит (global JSX types), не регресс. N1 (RU `profile_title`=`personal_title`=«Профиль» → дубль в TopBar+eyebrow) **исправлен**: RU `settings.profile.personal_title` → «Личное» (EN «Personal» vs «Profile» уже различались). +5 i18n EN/RU (`profile_title`, `identity.{role_athlete,role_demo,athlete_prefix,age}`). |

---

## 6. Закрытые open questions README §15 (ответы из кода)

| Вопрос | Ответ |
|---|---|
| Бэкенд принимает запись `goal.ctl_target`/`per_sport_targets`? | **ДА.** `PATCH /api/athlete/goal/{goal_id}` (`api/routers/athlete.py:58`) — `ctl_target`/`per_sport_targets`/`sport_type`, `require_athlete`. Inline-edit Goals — сразу, не read-only. |
| Схема `/api/profile`? | Эндпоинт = `PATCH /api/athlete/profile`, **только** `age` 18–90. Остального в `users` нет (§4.1). |
| Откуда MCP-токен? | Существующий `User.mcp_token`; апп уже отдаёт URL+token. URL=`/mcp`, per-user Bearer. Новый эндпоинт не нужен. |
| Markdown-рендерер `/weekly`? | Уже есть в webapp (`/api/weekly-reports`, markdown). Переиспользовать. |
| LoadingSpinner vs скелетоны / OAuth-migration prompt? | На усмотрение, бэкенд не блокирует. Бриф: скелетон ≤ «пустая карточка + pulse». |

---

## 7. Decisions log

| Дата | Решение | Альтернатива | Причина |
|---|---|---|---|
| 2026-05-17 | **G1=A** — урезать Personal-карточку | B (миграция users.sex/height/resting_hr + эндпоинт) | Sex/Height/HR rest колонок нет; миграция ради дизайн-мокапа без продуктового спроса = переинжиниринг. A держит Phase 1 «no live data». Дельта → F13. Реверс возможен через §4.1 B-ветку. |
| 2026-05-17 | **G2=A** — фронт собирает отчёт из строки + chip'ов | B (структурный/markdown morning report) | Смена morning-report промпта = риск регрессии ежедневного отчёта + инвалидация prompt-кэша. Все нужные числа уже в wellness-day. A = 0 риска. Дельта → F14. Реверс через §4.3 B-ветку. |
| 2026-05-17 | Оформить ревью спекой, не GitHub-issues | Сразу issues по BE-1..BE-4 | Punch-list содержит фронтовые/IA пункты + продуктовые гейты, не только бэкенд-таски; нужен единый durable документ, issues — производное от него. |
| 2026-05-17 | Default G1=A / G2=A | Сразу специть миграции/структурный отчёт | Memory: push back on over-engineering. Миграция/смена промпта ради дизайн-мокапа без продуктового запроса = переинжиниринг. A закрывает Phase 1/3 с нулём бэкенда. |
| 2026-05-17 | BE-2 = тест+фикстура, бэкенд-код не трогаем | «Починить сериализатор» | Сериализатор уже верен (`wellness.py:70` passthrough, `metrics.py:97` enum). Баг локализован в `sample-data.json`. |
| 2026-05-17 | BE-4 по умолчанию не открываем | Сразу серверный matched-week через `training_log` | README контракт «no API changes»; блокер 5 решается клиентски (F2). Серверный путь — fallback, не первичный. |
| 2026-05-17 | Фронтовые находки держим в этой же спеке | Отдельный UX-документ | «Чтобы ничего не забыть» = единый источник; разнесение теряет связь блокер↔решение. |
| 2026-05-17 | **Никаких GitHub-issues по Halo — всё через эту спеку** (явное указание пользователя) | Нарезать BE-2/F-list в issues | Единый durable документ; issues расходятся с спекой и дублируют трекинг. Применяется ко всему Halo-воркстриму, не разово. |
| 2026-05-17 | Rev.2: верифицировать дизайн **по коду JSX**, не по changelog | Доверять README changelog | Changelog описывает намерение; правки проверены в `direction-b-*.jsx` (ESS:677, per-sport CTL:783, AI-card:342, Personal:101). Нашло F15/F16 — их в changelog нет. |
| 2026-05-17 | F3 не «закрыт», а эскалирован в **G3** (продуктовый гейт) | Считать F3 done после удаления `recovery.title` | Удалён только 3-й (декоративный) голос. AI-строка vs детерминир. chip+rec — продуктовый выбор (a/b), не дизайн-правка; блокирует Phase 3. |
| 2026-05-17 | F2 (брик в merged-week) — **известный gap**, ждёт Phase 4 | Чинить матчинг в дизайне сейчас / impl-фриланс | Дизайнер осознанно отложил (README «Still open #4»); фикс требует решения «1 план → N фактов» + протаскивания `is_race`. Impl не правит ad hoc. |
| 2026-05-17 | **F2 закрыт веткой A — day-level агрегация** | B: brick-aware матчинг (детект по cat/name/desc) | Durable: merged-week — day-level, НЕ 1:1 по спорту. Все факты дня коллективно закрывают план дня; `compliance = Σфакт/Σплан`; `unplanned` = факты без плана; `is_race` всегда бейджится. B зависел бы от хрупкого brick-сигнала (cat/парсинг). A робастна (брик/двойные/гонки), no API. `buildWeek()` + `BWeekMerged` приведены; A/C back-compat. |
| 2026-05-17 | **merged-week mount = B (тоггл [Week·Plan] на `/plan`, Week default)** | A: `/plan`=MergedWeek, plain Plan орфанится | Бриф «no new routes» (kills `/week`-роут) + README §3.5 «/plan и /activities оба сохраняются» (kills pure-replace) → единственная faithful опция = in-page тоггл. `Plan.tsx`→`PlanList` (без chrome, логика byte-identical), `PlanScreen` владеет Layout/TopBar/тогглом. Каждый режим — свой `useWeekNav` (переключение сбрасывает на текущую неделю — намеренно, это разные виды). 14→14 routes. |
| 2026-05-17 | webapp MergedWeek **сохраняет typeless-факты** (prototype дропал `if(!a.t)`) | Точный порт прототипа (дропать type-null) | `Activity.type` nullable в БД; typeless-сессия всё равно потратила тренировочное время — дроп занижал бы day-compliance. Осознанное улучшение vs прототип, закомментировано в `MergedWeek.tsx`. |
| 2026-05-17 | Rev.3 wave-2: **G3 закрыт веткой (b)** | (a) оставить AI на герое + rules-фильтр | Дизайнер выбрал (b) — совпало с рекомендацией спеки. chip+rec остаётся single source of truth, LLM-нарратив уходит в будущий `/coach`-view. Бэкенда 0, риск противоречия снят без rules-фильтра. |
| 2026-05-17 | F15 реализован, но **реоткрыт на reconcile** | Принять TSB-карту как есть (демо рендерит верно) | Введённые границы (−30, fresh/peak split) противоречат CLAUDE.md «Business Rules» (−25, единый «under-training»). Демо −7.8 маскирует баг (OPTIMAL в обеих моделях). Та же логика, что BE-2/banister: «фикс, тихо вводящий новый рассинхрон с source-of-truth» ловим до Phase 3. |
| 2026-05-17 | **F15 закрыт веткой A — conform к CLAUDE.md (4 зоны), CLAUDE.md НЕ менялся** | B: оставить 5-зонную TrainingPeaks-модель + обновить CLAUDE.md | Durable: TSB = **4 зоны** `<−25 overtraining \| −25..−10 productive overreach \| −10..+10 optimal \| >+10 under-training` (CLAUDE.md — канон). Граница −30 была **багом** (недо-предупреждала о перетрене на −25..−30). fresh/peak — методологическое расширение, но бриф «не переизобретать домен» + не править sacred-doc без явного запроса → conform. `shared.jsx` + прототип-band синхронизированы; Phase 6 наследует. |
| 2026-05-23 | **РЕВЕРС 2026-05-17 решения по F15: 5-зонная модель фронта стала каноном на всём стеке** | Оставить расхождение (5 зон только на `/wellness/load`, 4 зоны везде ещё) | Явное указание пользователя «5 зон везде, обнови всё». Бриф «не переизобретать домен» отменяется: домен переопределён осознанно. `CLAUDE.md`, `docs/BUSINESS_RULES.md`, `docs/knowledge/fitness-fatigue-model.md`, `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` синхронизированы с `LoadDetail.tsx::TSB_ZONES`. Бэк: `data/utils.py:tsb_zone` теперь возвращает `risk\|optimal\|gray\|fresh\|transition` (5 строк), `data/workout_adapter.py:298` ослаблен с `<−25` до `<−30` (Z2-cap fires only in `risk`), `tasks/formatter.py:build_morning_message` потерял ветку `<−10` (productive-overreach warning исчезает — диапазон −30..−10 теперь `optimal`), `tasks/tools.py` + `mcp_server/resources/athlete_profile.py` сообщают Claude новую матрицу. **Trade-off (принят пользователем):** атлет с TSB в −25..−30 больше не получит ни 🔴-предупреждения, ни Z2-кэпа на адаптации — это и было «under-warning bug» из 2026-05-17, теперь это feature. |
| 2026-05-17 | **Phase 6: переиспользован существующий `TsbZoneBadge`, `utils/tsb.ts` НЕ создавали** | Создать `utils/tsb.ts` по образцу `recovery.ts` (как планировала F15-заметка) | Durable: webapp уже имел CLAUDE.md+`data/utils.py:tsb_zone`-конформный TSB-классификатор (`TsbZoneBadge`/`tsbZone`/`TSB_ZONE_COLORS`). F15 был багом ТОЛЬКО дизайн-фикстуры `shared.jsx`. Новый util = второй source-of-truth (анти-паттерн). Спековая «port»-заметка устарела — помечена. Route-fold `/weekly`→Recap отложен (net-new IA, прецедент F2). |
| 2026-05-17 | BE-2b: дизайнер НЕ трогал фикстуры — **правильно** | Ожидать, что дизайн выровняет шкалу | Выбор шкалы `banister_recovery` — решение бэкенда (источник `data/metrics.py`), не дизайна. README залогировал рекомендацию 0–100 на API. Ownership за бэкендом, спека ведёт. |
| 2026-05-17 | **BE-2/BE-2b закрыты контракт-тестом, без правки бэкенд-кода** | Изменить сериализатор/добавить нормализацию | Durable: `*.status` enum = `green\|yellow\|red\|insufficient_data`, `recovery.readiness_level` (может быть `balanced`) — **отдельное поле**; `stress.banister_recovery` = **0–100 %** (клампы в `data/metrics.py`). Сериализатор уже верен — рассинхрон жил только в дизайн-фикстуре. Лочим тестом (`tests/api/test_wellness_day.py`, 5/5), фикстуры выровнены, handoff RESOLVED. **Phase 3 Wellness тем самым РАЗБЛОКИРОВАН.** |
| 2026-05-17 | **Phase 3: G3=(b) применён к live-Wellness — chip+rec из `utils/recovery.ts` единственный голос; AI/ESS/readiness сняты с героя** | Оставить AI/ESS как есть (минимальный re-skin) | G3=(b) — утверждённый гейт. README §6 прямо требует деривить chip/rec на фронте, не из бэкенд-строк. Durable: Wellness больше не показывает `ai_recommendation` (→ future `/coach`) и `ess_today` (нет surface — M2-ESS на подтверждение). H1 (null-score рисовал фабрикованный «low/REST») — реальный cold-start баг, пойман ревью, исправлен neutral-state'ом. `BWellnessCalibrating` отложен как net-new (прецедент F2). |
| 2026-05-17 | `sed -i ''` обнулил `Wellness.tsx` → восстановлен Write'ом | — | Инцидент инструментирования (не дизайн-решение). Зафиксировано для честности: integrity восстановленного файла подтверждена code-reviewer'ом (single export, все хелперы по разу, tsc/build/13-tests зелёные). Урок: не гонять `sed -i ''` по только что Write-нутым файлам — правки через Edit. |
| 2026-05-17 | **Phase 0: токены A1 (extend, namespaced)** | A2 reskin-in-place | A1 — единственная ветка, держащая Phase-0 «zero user-visible change»: legacy `:root`/Tailwind байт-в-байт нетронуты, Halo через `halo-*` namespace, opt-in. Dual-palette — намеренный временный долг, чистится в конце миграции. |
| 2026-05-17 | **Phase 0: добавлен vitest, запинен `^3.2.4`** | vitest@4 (ставился первым) / без тестов | Spec acceptance требует boundary-тесты `recovery.ts`. vitest@4 требует peer `vite@7` (у нас `vite@6`) → ERESOLVE на чистом CI; review M1. Запинен `vitest@^3.2.4` (поддерживает vite@6). 11/11 зелёные. |
| 2026-05-17 | Phase-0 primitives — namespaced, **не смонтированы** | Заменить live `BottomTabs`/`Layout` сразу | Монтаж = user-visible change, нарушает A1. Шелл собран в `components/halo/`, подключается пофазно при порте экранов. `HaloBottomTabs` берёт `items` пропом — финальная IA (Today/Plan/…) это F1/F16, не Phase 0. |
| 2026-05-17 | `npm run lint` сломан — **не чиним в Phase 0** | Завести eslint-конфиг сейчас | В репо вообще нет eslint-конфига (eslint 8.3.0 сканирует `dist/`) — сломано ДО Phase 0, не регресс. Свой конфиг = новая инфра/пакеты без sign-off + scope creep. Вынесено в §5 как infra-долг. |
| 2026-05-17 | **Phase 1: in-place re-skin `Settings.tsx`, не parallel-page** | Новая `pages/halo/Settings.tsx` за флагом | README §13 «shippable in place»; нет flag-инфры; дубль 350 строк OAuth/token/seq-guard логики = drift + риск регрессии в security-flow. In-place: строки 1–349 байт-в-байт, меняется только presentational слой → поведение сохранено by construction (ревьюер подтвердил byte-level). |
| 2026-05-17 | **Phase 1 F13: Weight/HR-max/Sex/Height/HR-rest НЕ рендерим** | Показать read-only из wellness/athlete_settings | Payload Settings (`/api/auth/me→profile`) несёт только `{age,lthr_run,lthr_bike,ftp,css}`. Доп. fetch wellness/athlete_settings = «live data flow», Phase-1 это исключает. Честная реализация G1=A: Age editable + Thresholds read-only, остальное вне data-source. Связано с архивной BE-1 (если появится profile-endpoint с этими полями — вернуть). In-code комментарий `Settings.tsx:524`. |
| 2026-05-18 | **Full-fidelity-заход: Nav=A — `HaloBottomTabs` смонтирован** | Оставить legacy эмодзи-`BottomTabs` (Phase-0 шелл не монтировать) | Пользователь явно: «хочу видеть дизайн, который сделал дизайнер». Phase-0 A1 («не монтировать») снят по запросу: смысл follow-up — прототип-fidelity. IA = точный 5-таб набор прототипа; legacy bar удалён (zero callers); changelog-сюрфейс не потерян (Wellness-баннер). Sidebar/desktop вне скоупа (бриф mobile-first). |
| 2026-05-18 | **L1 clearance — чиним сейчас, не батчем** (точечно `-mb-20 md:-mb-8`→`md:-mb-8`) | Отложить в Layout-seam-фазу | Пользователь тестирует live; content-under-bar — видимый дефект ровно сейчас. Recipe уточнён vs «убрать `-mb-*` целиком»: сохранён `md:-mb-8` → нет desktop-strip-регрессии. 9 nav-несущих wrappers, presentational-only. Остаточный negative-margin-долг → Layout-seam. |
| 2026-05-18 | **G1 переоткрыт A→B-узкий: surface read-only Weight + per-sport HR-max БЕЗ миграции** | Держать G1=A (строка выше) / сразу полный G1=B с sex/height/rhr-миграцией | Пользователь явно захотел Personal прототипа. Узкий B: данные УЖЕ в БД (`Wellness.weight`, `AthleteSettings.max_hr`) → 0 миграций, новый `@dual get_latest_weight` + `get_all`-маппинг в `auth_me` (tenant-safe `data_uid`). sex/height/rhr остаётся архивом — миграция без спроса = переинжиниринг (push-back держится). Прецедент: «no live data flow» был Phase-1-ограничением, follow-up его осознанно снимает по запросу. |
| 2026-05-18 | **Settings — полный структурный порт в композицию прототипа** (выбор пользователя из 3: full / chrome-only / точечно) | Phase-1 token-re-skin легаси-структуры (статус-кво) / только chrome-выравнивание | «settings не отвечает дизайну» — диагноз: Phase-1 был **token-only** (цвета, не композиция), прототип-Settings — другая вёрстка, её не строили. Пользователь выбрал full. Дисциплина соблюдена: full-file Write (нужен для reorder), но **логика байт-в-байт** (handlers/effects/seq-guards/OAuth-стейт-машина verbatim, подтверждено diff'ом hunk-headers + code-reviewer). Load-bearing секции которых нет в моке (полный OAuth-flow, MCP-токен, logout) **сохранены** в bxCard-стиле — мок их сворачивает/прячет, но в реале они нужны → faithful ≠ слепая копия мока. Deviations (identity data-honesty, double-role-pill) залогированы в F-Settings-port. |

---

## 8. Status

- [x] **G1** — **A→B-узкий**: B приземлён 2026-05-18 (Weight + per-sport HR-max read-only, БЕЗ миграции; sex/height/rhr — архив)
- [x] **G2** — решено **A**, приземлено (Rev.2)
- [x] **G3** — решено **(b)**, приземлено (Rev.3 wave-2): AI-строка убрана с героя, → будущий `/coach`-view
- [x] BE-1 — **no-op** (G1=A)
- [x] **BE-2 — ✅ закрыто**: фикстура выровнена + `tests/api/test_wellness_day.py::TestStatusEnum` + handoff RESOLVED
- [x] **BE-2b — ✅ закрыто**: шкала 0–100 подтверждена из кода + фикстура `0.68→68.0` + `::TestBanisterScale` + handoff RESOLVED
- [x] BE-3 — **no-op** (G2=A)
- [ ] BE-4 — серверный matched-week — **намеренно не начат** (fallback)
- [x] F1(част.) F4 F5 F10 F13 F14 F16 — приземлено (проверено по коду)
- [x] **F15 — ✅ решён (A, 2026-05-17):** TSB-карта `shared.jsx` → CLAUDE.md (4 зоны, −25), прототип-band синхронизирован, CLAUDE.md не менялся. Phase 6 приземлён **без `utils/tsb.ts`** — webapp `TsbZoneBadge` уже конформен (`data/utils.py:tsb_zone`-aligned); не дублировать.
- [x] F17 — ✅ CTA «→ today's plan» на recovery-карточке (Phase 3)
- [ ] F2 — брик-матчинг — **известный gap**, Phase 4 prerequisite (не править ad hoc)
- [ ] F6 F7 F8 F9 F11 F12 — 🛠 implementation-time, в README «Still open», дизайн-правок не требуют

**Активный бэкенд-бэклог: пуст** (BE-1/BE-3 no-op, BE-2/BE-2b закрыты, BE-4 не открываем).
**🎉 Программа завершена.** Приземлено: **Phase 0/1/2/3/4(полностью, incl merged-week)/5/6/7** (2026-05-17), holistic + per-phase ревью пройдены. Открытых блокеров/решений — ноль.
- `BWellnessCalibrating` (<14d-HRV экран вместо score) — **отложен** (net-new behavior + продуктовое решение, прецедент F2; H1-фикс уже даёт честный neutral-state на null-score, см. follow-ups).

### Phase rollout (README §13)

- [x] **Phase 0 — Foundations (2026-05-17).** Токены A1 (`src/styles/index.css` `--color-*` additive + `tailwind.config.js` `halo` namespace + `rounded-card/pill/chip` + `shadow-card`); `utils/recovery.ts` (§6 verbatim) + `recovery.test.ts` (11 тестов, vitest@3); примитивы `components/halo/` (Card/TopBar/MicroLabel/StatusChip/HaloBottomTabs, opt-in, не смонтированы). **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **Approve**, 0 Critical/High; M1/M2/L1/L3/M3 закрыты в этом же заходе. Коммита нет — ждём пользователя.
- [x] **Phase 1 — Settings re-skin (2026-05-17).** In-place re-skin `pages/Settings.tsx`: строки 1–~349 (все hooks/handlers: OAuth init/disconnect/migrate, `patchGoal/patchProfile/toggleSport` seq-guards, MCP-masking) **байт-в-байт нетронуты**; переверстан только презентационный слой (`Section`→Halo `Card`, `Row`/`EditableNumberRow`, токены `halo-*`, `Layout` без title + `bg-halo-bg` + Halo `TopBar`). **F13/G1=A честно:** Profile разбит на Personal (Age editable) + Thresholds (LTHR/FTP/CSS read-only); Weight/HR-max/Sex/Height/HR-rest **НЕ рендерятся** — payload `/api/auth/me→profile` их не несёт, добавлять wellness/athlete_settings fetch = «live data» вне Phase-1 (in-code комментарий + здесь). Hardcoded-строки → i18n (EN+RU parity). **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **APPROVE**, 0 Critical/High, поведение verified byte-level. Коммита нет.
- [x] **Phase 2 — Onboarding re-skin (2026-05-17).** In-place re-skin: `pages/Login.tsx` (auth-handlers 1–143 байт-в-байт нетронуты — telegram-widget callback, verify-code, demo, `routeAfterLogin`, widget script; переверстан JSX 145+: radial-gradient backdrop, Halo card/inputs/buttons, brand-pill submit), `components/SportsPicker.tsx` (логика `toggle/submit/onSaved` нетронута; Halo toggle-кнопки + ink Continue), `components/OnboardingPrompt.tsx` (оба бранча — `startOAuth` + ApiError-412 `bot_chat_not_initialized` нетронуты; Halo card + ink CTA). Без новых i18n (экраны уже keyed). **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **APPROVE**, 0 Critical/High, поведение verified line-diff. Medium (bottom bg-bleed strip) **исправлен сразу**: `-mb-8` / `-mb-20 md:-mb-8` на bleed-wrappers (Login/SportsPicker/Onboarding + ретрофит Settings) — закрывает видимый симптом L1. Коммита нет.
- [x] **Phase 3 — Wellness re-skin (2026-05-17).** `pages/Wellness.tsx` переписан презентационно (data-hooks `useDayNav/useApi/useChangelog` + 4-way conditional нетронуты): Halo `TopBar`+`bg-halo-bg`; recovery-герой = inline-SVG 240° арка + score + **детерминированный chip+rec из `utils/recovery.ts`** (README §6 contract, lang by i18n) + skip-override-баннер + **F17** `<Link to="/plan">` CTA + «how this score» disclosure; HRV/RHR/Sleep/Load/Body → Halo `Section`/`MetricCell`; статус-пилюли = Halo `StatusChip` + **N2 `rmssdToTone`**; empty-state = Halo card; **What's-new = переиспользован `useChangelog`** (не новая localStorage-инфра). **G3=(b): `AiRecommendation` убран** (→ future `/coach`); ESS/readiness_level-строки тоже сняты с героя (Halo single-voice — см. M2-ESS follow-up). **Инцидент:** `sed -i ''` обнулил файл → восстановлен Write'ом, integrity подтверждена ревьюером. **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 13/13 ✓. code-reviewer: H1 (null-score рисовал фабрикованный «REST RECOMMENDED») — **исправлен** (neutral-state при `score==null`, i18n `wellness.score_unavailable`); M1 a11y (`aria-expanded/controls`) — исправлен; L1 (`STATUS_COLOR` canonical-only) — помечен комментарием. Коммита нет.
- [x] **Phase 4 — Plan + Workout + merged-week (2026-05-17, ПОЛНОСТЬЮ).** **(a) re-skin** `pages/Plan.tsx`+`pages/ScheduledWorkout.tsx`; **(b) merged-week (net-new, F2=A)** — `pages/MergedWeek.tsx` (own useWeekNav + 2×useApi `scheduled-workouts`∪`activities-week`, day-level `buildWeek`, Halo Planned\|Done + roll-up, tap→`/workout|/activity`), `Plan.tsx`→`PlanList` (chrome снят, логика byte-identical), `PlanScreen.tsx`=`/plan`-хост с тогглом **[Week·Plan] Week-default** (mount=B, no new routes — README §3.5), `App.tsx` route repoint, `merged.*` i18n EN/RU. code-reviewer (build): **APPROVE**, F2=A parity unit-correct, Plan не орфанится, 14→14 routes. Деталь re-skin-части ниже. **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 13/13 ✓. Коммита нет.
  Re-skin in place: `pages/Plan.tsx` (зеркало Phase-5 Activities — `Layout` без title + `bg-halo-bg` + `TopBar`, day-cards/`WorkoutItem` halo; логика `useWeekNav/useApi` нетронута), `pages/ScheduledWorkout.tsx` (699 строк, вкл. SVG `TimelineChart` corridor-viz — «main challenge»; вся chart/step-математика `flattenSteps/primaryTarget/targetToYRange/absoluteRange/pickZoneIndex`/repeat-recursion/pace-inversion **байт-в-байт нетронута**; presentational через safe file-wide `replace_all` + SVG/JS CSS-var свопы `var(--text-dim)→var(--color-ink-dim)` и `var(--border)→var(--color-border)`, оба таргета определены в `:root` Phase-0). **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **APPROVE**, 0 Critical/High/Medium; подтвердил highest-risk failure-mode (невидимый chart) provably avoided + merged-week scope absent. Коммита нет.
  **merged-week — ✅ ПОСТРОЕН** (см. строку Phase 4 выше). Phase 4 закрыт полностью.
- [x] **Phase 5 — Activities re-skin (2026-05-17).** In-place re-skin `pages/Activities.tsx` + `pages/Activity.tsx`: вся логика нетронута (`useWeekNav`/`useApi`, `toggleDetail` lazy-fetch + `cacheRef`, `InlineDetail` sport-branch metrics; Activity pace/GAP/swim-pace деривации, id-regex guard, `windOctant`/`fmtHMS`/`fmtPaceKm`, 3 early-return Layout'а). Презентационный слой → `halo-*` (Activity.tsx — безопасные file-wide `replace_all` token-swaps + `DfaItem` тернар на `status-green/red`+`amber`). Shared-компоненты (`ZoneBar/WeekNav/LastSyncedLabel/LoadingSpinner/ErrorMessage`) намеренно НЕ перекрашены — своя фаза. **DFA neutral-band = `text-halo-amber` намеренно** (фиделити: legacy `#d97706` ≈ amber `#d18b00`, не bright `status-yellow #eab308`) — не «чинить» на harmonize-pass. **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **APPROVE**, 0 Critical/High; Medium (`font-bold` survivor на today-badge) исправлен сразу. Hardcoded-копирайт RaceSection(RU)/WeatherCard(EN)/таблицы/`Details →` — НЕ i18n'или (вне token-re-skin scope, см. F8-расширение ниже). Коммита нет.
- [x] **Phase 6 — Dashboard re-skin (2026-05-17).** In-place re-skin `pages/Dashboard.tsx` (652 стр, табы load/goal/week, 3 Chart.js в LoadTab + `.destroy()`, `ProgressBar`/`GoalCard`/projection, `WeekTab`-пагинация — **байт-в-байт нетронуты**) + `pages/WeeklyReports.tsx` + `pages/WeeklyReport.tsx` (markdown-детали; `shiftIsoDate/safeUrlTransform/notFound` нетронуты). Презентационно: safe file-wide `replace_all` + Halo `TopBar`/`bg-halo-bg`/tab-pills; `text-red-600→halo-coral`, `text-green-600→halo-status-green`, `var(--text-dim)→var(--color-ink-dim)`. **`TsbZoneBadge`/`tsbZone`/`TSB_ZONE_COLORS` НЕ тронуты** — webapp уже CLAUDE.md-конформен (`data/utils.py:tsb_zone`-aligned); **`utils/tsb.ts` НЕ создавали** (был бы дубль source-of-truth — ревьюер подтвердил, спековая «port»-заметка устарела). Инцидент: `text-text `-trailing-space `replace_all` смержил `text-halo-inkhover:` ×2 — пойман и исправлен. **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 13/13 ✓, no merge-corruption. code-reviewer: **APPROVE**, 0 Critical/High/Medium (1 Low — `TSB_ZONE_COLORS` key-name vs backend-slug, **pre-existing**, не Phase 6, → cross-stack-note ниже). Route-fold `/weekly`→Recap-таб **отложен** (net-new IA, прецедент F2). Shared `ChartCard/RacePlanPanel` — своя фаза. Коммита нет.
- [x] **Phase 7 — Progress re-skin (2026-05-17).** In-place re-skin `pages/Progress.tsx` (1473 строки, ~7 Chart.js инстансов): вся chart/data-логика — `useApi` endpoint, `sport`/`days` state, все `new Chart()` configs (datasets/scales/annotations) + `.destroy()` cleanup, sport-conditional widgets — **байт-в-байт нетронута**. Presentational через safe file-wide `replace_all` (9 token-свопов) + targeted (sport-pills `bg-halo-brand`/`bg-halo-surface-2`, `Layout` без title + `bg-halo-bg` + `TopBar`). **Chart.js dataset/annotation цвета (`CHART_COLORS`/`STATUS_COLORS`/hex/`rgba` grid) НЕ трогали** — прецедент Phase-5 `ZONE_COLORS`. JS-`var()` chrome-строки (TrendBadge/badge/verdict/dot) → `--color-*`. Trailing-space fat-finger (`bg-halo-surfaceborder`×7) пойман и исправлен. **Verify:** `tsc -b` ✓, `vite build` ✓, vitest 11/11 ✓. code-reviewer: **APPROVE**, 0 Critical/High/Medium/Low (1 Nit — wrapper несёт `font-sans text-halo-ink`, → Layout-фаза dedup). Hardcoded EN (title/error/axis-labels) не i18n'или — F8-debt. Коммита нет.

### Review-derived follow-ups (не Phase 0)

- [ ] **N1 → Phase 1 (token hardening):** перевести status-токены на channel-форму (`--color-status-green: 34 197 94` + `rgb(var(--…) / <alpha-value>)`), тогда нативный Tailwind `bg-halo-status-green/15` и `StatusChip` без inline `color-mix`. Не делать в Phase 0 (риппл в `STATUS_COLOR`).
- [x] **N2 — ✅ закрыт (Phase 3):** `rmssdToTone()` + `RMSSD_TONE` + `StatusTone` в `recovery.ts` (co-located, `yellow→warn` амбер), +2 vitest. Wellness `StatusPill` использует Halo `StatusChip` + `rmssdToTone`.
- [ ] **M2-ESS → продуктовое подтверждение (Phase 3 review):** Halo-Wellness (G3=b single-voice) снял с героя строки `Readiness`(readiness_level) и `ESS` (`stress.ess_today`); Banister выжил только в свёрнутом disclosure. Данные в `WellnessResponse` не потеряны, но **ESS теперь не имеет surface нигде на Wellness**. Спека ранее фиксировала только удаление AI-карточки — подтвердить с продуктом, что ESS на Wellness убирается намеренно (Halo-дизайн: ESS живёт в Activity-detail post-context).
- [ ] **N1-note (review нит, Phase 3):** H1 null-score guard (`RecoveryHero` `hasScore`-гейтинг) — **component-level, не покрыт unit'ом** (нет RTL-харнесса в проекте; `recovery.ts` тесты — pure-fn, `classifyRecovery(0)→'low'` корректно тестит реальный 0). Зафиксировано чтобы не плодить «добавь тест» — TS-флоу (`score: number|null`) + простота гейтинга достаточны без компонент-теста.
- [x] **Cross-stack TSB-контракт — закрыт 2026-05-23 реверсом к 5-band:** `data/utils.py:tsb_zone` теперь возвращает `risk|optimal|gray|fresh|transition`, ровно те же ids, что в `LoadDetail.tsx::TSB_ZONES`. Старая нота про `productive`/`under`-key drift устарела — 4-зонная модель снята со всего стека, расхождение по именам исчезло вместе с ней.
- [ ] **`ActivitiesWeekResponse` без `has_next` (type-fidelity nit, Phase-4-merged review):** API `/api/activities-week` не отдаёт `has_next` (только `has_prev`); `api/types.ts` это честно отражает. MergedWeek драйвит WeekNav от `scheduled-workouts` (есть `has_next`) — здесь ОК. Заметка: если будущий вид погонит WeekNav от `activities-week.data` — тихо потеряет forward-nav. Вне scope, для самого типа.
- [ ] **Route-fold `/weekly`→Dashboard Recap-таб (отложен, net-new IA):** README §10/§14 хотят, чтобы `/weekly` вёл на Dashboard с recap-табом. Сейчас `/weekly` + `/weekly/:weekStart` — отдельные re-skinned-роуты (прецедент F2/calibrating: net-new ≠ re-skin). Отдельной задачей при запросе.
- [ ] **`BWellnessCalibrating` → отложен (net-new, прецедент F2):** <14d-HRV экран (baseline-gauge вместо score) прячет реальный бэкенд-score → продуктовое решение, не token-re-skin. H1-фикс в Phase 3 уже даёт честный neutral-state при `score==null` (cold-start), что закрывает главный риск. Полный calibrating-UX — отдельным раундом при запросе.
- [ ] **N1 (token hardening) — всё ещё открыт:** Phase 1 снова обошёл alpha-on-var солидными токенами (тосты Settings). Channel-форма (`--color-status-*: R G B` + `rgb(var(--…)/<alpha-value>)`) убрала бы и `StatusChip` `color-mix`, и даст soft-fill тостам. Не блокер; делать когда понадобится мягкая заливка по статусу.
- [x] **L1 → content-under-bar clearance (ЗАКРЫТ 2026-05-17, fix-now):** Halo-bleed-wrappers гасили `Layout pb-20` через `-mb-20 md:-mb-8`; после монтажа fixed-таб-бара последняя карточка уходила под бар. **Фактический recipe (точнее, чем «убрать `-mb-*` целиком»):** `-mb-20 md:-mb-8` → **`md:-mb-8`** на 9 nav-несущих wrappers (Wellness, PlanScreen, Activities, Dashboard, Settings, Progress, WeeklyReports, WeeklyReport, OnboardingPrompt). Mobile base-margin=0 ⇒ `pb-20` clearance восстановлен, неблендованная полоса скрыта непрозрачным fixed-баром; `md:-mb-8` сохранён ⇒ **нет desktop-регрессии** (на desktop бара нет, полный `-mb-*` вернул бы 2rem body-strip). `-mb-8`-only wrappers (ScheduledWorkout/Activity/SportsPicker) не тронуты — `hideBottomTabs`-страницы, бара нет. TSC/BUILD/13 ✓; presentational-only. **Остаточный долг → Layout-seam:** сам negative-margin-bleed-хак убрать при порте `Layout` (фон должен давать сам `Layout`).
- [ ] **N1-toast (продуктовый нит, Phase 1 review):** success/error тосты Intervals различаются теперь только цветом рамки/текста (fill убран ради alpha-on-var). Сознательный выбор; при желании больше контраста — solid `--color-*-soft` токены в будущей фазе (связано с N1 выше).
- [ ] **F8-расширение → i18n impl-фаза (Activities):** при re-skin Phase 5 НЕ i18n'или хардкод-строки (token-swap scope). Полный список к выносу: `Activity.tsx` `RaceSection` RU-метки (Финиш/Дистанция/Темп/Место/Покрытие/Погода/RPE/Fitness), `WeatherCard` EN (Temperature/Wind/Rain/Snow/Clouds), `DFA Alpha 1`, `Intervals`, табличные шапки (#/Duration/Power/Pace/HR/Cadence), `Activities.tsx` `Details →`, и `ZoneBar` label'ы `HR/Power/Pace Zones`. Чтобы i18n-задача не недооценила объём.
- [ ] **Visual-QA (holistic Low-2):** `TopBar` имеет `px-5`, а bleed-wrapper контента — `px-4` → заголовок шапки на 20px от края, карточки на 16px (4px рассинхрон левого края на Settings/Activities/Plan одновременно). Spec-faithful README §4 «padding 18/20/10» — возможно намеренно. Решение на device-QA: принять как есть (тогда не «чинить» ad hoc) либо выровнять `TopBar` на `px-4`. Не блокер.
- [ ] **Visual-QA (Phase 4 review Low):** corridor-chart gridline теперь `var(--color-border)` (8% alpha-on-ink) — заметно бледнее старого solid `#d4d4dc` на светлом `--color-bg`. Spec-faithful, не баг. Если на девайсе слишком washed-out — НЕ откатывать своп, а ввести отдельный `--color-grid` (~`rgb(10 13 24 / 0.14)`); батчить с N1 token-hardening, не точечно.
- [ ] **Infra-долг (вне Halo-фаз):** в репо нет eslint-конфига, `npm run lint` нерабочий (сканирует `dist/`) — сломано до Phase 0. Завести flat-config — отдельное infra-решение с sign-off, не в рамках редизайна.

---

## 9. Full-Fidelity Structural Port — «точь в точь» (2026-05-18)

**Мотивация.** Пользователь явно: «посмотри новый дизайн и спеку. нам сделать
дизайн точь в точь». Аудит (6 параллельных per-screen сверок `direction-b-*.jsx`
↔ `webapp/src/`) подтвердил: статус «завершена» был неточен — Phase 3/5/6/7 =
**token-swap re-skin** (только Tailwind-цвета → `halo-*`), композиция прототипа
НЕ воспроизведена; структурно портированы были только Settings (+ частично
Wellness/MergedWeek). Запущен полный структурный порт всех экранов в композицию
прототипа — тем же приёмом, что Settings.

**Продуктовое решение (этот заход):**
- **G4 — fidelity stance = LITERAL COPY** (выбор пользователя из 2: literal vs
  Settings-precedent). Воспроизводим композицию + декоративные/статические
  элементы прототипа точь-в-точь; реальные данные биндим где API даёт (формы
  совпадают с `sample-data.json`); чисто декоративную статику (photo-strip,
  «Synced N min ago», VO₂max/weight subs, legal footer, «sweet spot», Body-subs)
  воспроизводим как литеральные i18n-строки. **Реверсит over-trims живого
  webapp'а** (выкинутые Body-card / date-strip→DayNav / HRV-RHR mini-gauges /
  training-load stacked bar / не-прототипный «See plan→» CTA), но **НЕ реверсит
  собственные дизайн-фиксы прототипа** (G3 AI-off-hero, F15 TSB-4-зоны, BE-2
  enum, BE-2b banister 0–100) — те уже правильны в прототипе.
- **G5 — backend-blocked = defer, frontend-only**: Wellness-calibrating
  (нужен `days_of_hrv`), Dashboard-Recap inline weekly-list w/ Load/Ramp/TSB/miss
  (нет полей в API), WeeklyReport structured hero/stat-grid (API даёт только
  `content_md`) — оставлены в честном текущем поведении; 3 backend-стори ниже.
- **G6 — Landing**: прототипа нет → ретокен в Halo-язык, композиция не менялась.

**Chart.js-vs-SVG (бриф §2 / README §9) — единственное санкционированное
отклонение от pixel-exact:** arcs/donuts/gauges/structure-bars = литеральный
inline SVG; line/scatter (CTL-проекция, EF-trend, decoupling-scatter) =
**оставлен Chart.js** (мандат брифа), контейнер ре-стайл под мок. Прототип
Progress целиком inline-SVG → не портируется verbatim; Progress сохранён как
Halo-superset chrome (см. ниже), приземлён только ink sport-pill fidelity-дельта.

### 9.1 Phase ledger (все code-reviewer **APPROVE**, 0 Crit/High; коммита нет)

| Ph | Экран(ы) | Сделано | Verify |
|---|---|---|---|
| A | shared primitives | `components/halo/`: `geometry.ts`(+test, formula-independent golden), Gauge/MiniRangeGauge/Donut/StackedBar/TaperBar/ESSScale/EffortBar/SportAvatar/SegmentedTabs/DateStrip/ToggleTile/SegmentedCodeInput/PhotoStrip; Card +`heroInk`; `lib/constants.sportColor` | tsc/build/vitest 27/27 |
| B | Login/Landing/SportsPicker | BLogin 88px-icon-hero+3-zone+SegmentedCodeInput+demo-textlink; Landing Halo-ретокен (legacy `<style>` снят); SportsPicker ToggleTile+step-eyebrow. Auth/onSaved логика byte-identical | ✓ |
| C | Wellness + empty | BWellness restore: DateStrip, Synced+Refresh, Gauge ticks+0/100, breakdown emoji-col, HRV/RHR paired+MiniRangeGauge, Sleep circle, training-load StackedBar, Body card; ghost-arc empty. `useDayNav.goTo` добавлен | ✓ |
| D | Plan/Workout/MergedWeek | BPlan day-cards+today-cobalt+Open/Adjust; BWorkoutDetail cobalt-hero+AI-badge+Start CTA (TimelineChart math byte-identical); MergedWeek cobalt/coral fill-инверсия | ✓ |
| E | Activities/Activity | BActivities flat-list+SportAvatar+EffortBar+accordion сохранён; BActivityRace ink-hero+Donut+ESSScale+Conditions; #44/fmtHMS/windOctant/WeatherCard-гейтинг byte-identical | ✓ |
| F | Dashboard | Goal/Load/Recap tab-model+SegmentedTabs; Goal=Gauge-arc+TaperBar; Load=Chart.js(byte-identical)+FormTsbCard StackedBar+drill-down; Recap=WeekTab сохранён. formatProjectionWarning/WeekTab-pagination byte-identical | ✓ |
| G | Progress | ink sport-pills (прототип-дельта); остальное = Halo-superset chrome (7 Chart.js + 6 widgets byte-identical, см. §9.3) | ✓ |
| H | WeeklyReports/Report | BWeeklyList card (uppercase label + «This week» pill + «Written every Monday…»); WeeklyReport Halo TopBar+«Coach report»; ReactMarkdown/safeUrlTransform/shiftIsoDate byte-identical | ✓ |
| I | spec + cleanup | этот раздел; удалены 6 zero-importer dead-компонентов; удалены orphan CSS | ✓ |

### 9.2 Deferred backend stories (НЕ начаты; **без GitHub-issues** — здесь)

1. **`days_of_hrv` на wellness-day** → разблокирует `BWellnessCalibrating`
   (<14d-HRV baseline-gauge вместо score). Сейчас честный neutral-state.
2. **`headline`/`ramp`/`tsb`/`miss` на `/api/weekly-reports` list** —
   **✅ закрыто 2026-05-22 (Phase Z), кроме `miss`.** Добавлены `headline`
   (парсер ведущего `# `-H1) + per-week `by_sport`/`ctl_start`/`ctl_end`/
   `ctl_delta`/`ramp`/`tsb_end`; Dashboard Recap-таб перестроен в
   weekly-report-driven `BDashboard·recap`. `miss` намеренно пропущен — выбор
   пользователя: карточка `BDashboard·recap` его не рендерит, тон ramp-пилюли
   берётся из значения ramp. Route-fold `/weekly`→Recap всё ещё открыт (стр. 262).
3. **Структурные поля на `/api/weekly-reports/{ws}`** (week-N eyebrow, headline,
   summary, Load/CTL/Ramp/TSB grid) → разблокирует BWeeklyDetail hero/stat-grid.
   Сейчас Halo-shell + verbatim markdown.

### 9.3 Logged deviations (literal-copy последствия / data-honesty)

- **Workout:** VI/PI убраны с hero (прототип hero = TSS/NP/Intensity только) —
  реальные метрики, потеряны как literal-copy последствие.
- **Dead/placeholder CTA:** Plan «Adjust», Workout «Start workout» — нет
  webapp-endpoint (AI-чат в Telegram / Intervals владеет исполнением);
  отрисованы per literal-copy как placeholder. Wellness-empty «Force sync now»
  — **выкинут** (unbacked + нет API), как и fabricated «yesterday tip» card /
  «Garmin syncs by 6:30».
- **Activity:** ESS-строка = реальный `d.trimp` (TRIMP — ближайший прокси к
  прототиповому «ESS», шкала 0–200 tick@100); photo-strip = реальные
  pace/cadence/RPE (не fabricated «6:00/184/8»); list Race-бейдж = только
  «Race» (нет place/total — `ActivityItem` не несёт RaceInfo); sync-time
  показан всем (было owner-only `LastSyncedLabel`) — прототип BActivities
  показывает безусловно, low-sensitivity (своя свежесть), сознательно;
  `power_hr` P:HR сохранён в bike Power-superset card (M2 reviewer).
- **Dashboard:** per-bar inline assumption-строка («+x/wk · proj. DATE»)
  убрана (прототип Goal её не имеет; off-track по-прежнему в warnings-панели
  через сохранённый `formatProjectionWarning`); FormTsbCard без прототиповой
  caption-строки (`tsbZone` = F15 source-of-truth, не форкаем ради caption).
- **Progress:** прототип целиком inline-SVG ↔ бриф мандат Chart.js → Progress
  сохранён как Halo-superset (7 Chart.js + 6 не-прототиповых виджетов
  byte-identical), приземлена только ink sport-pill дельта. Per-widget
  numeral/r²/donut-legend/decoupling-caption chrome **намеренно НЕ** rebuild —
  риск регрессии 7 charts > marginal fidelity (precedent: «keep superset»).
- **Generic TopBar titles** (Activities/Activity/Dashboard/WeeklyReport) вместо
  контекстных прототиповых («This week's work»/«Race recap»/«On track») —
  переиспользование существующих i18n-ключей, осознанное упрощение.
- **Gauge geometry fix (2026-05-18, «шкала уехала»):** прототип/`shared.jsx`
  `arcPath` со `start=-210/end=+30` рендерит **право-открытую** дугу, при этом
  `0`/`100`-лейблы захардкожены в нижних углах (README §7 «labels under the
  gauge ends») → дуга и шкала не совпадали. `components/halo/Gauge.tsx`
  переведён на симметричную **низ-открытую** 240°-дугу `start=-120/end=+120`
  (0% — нижний-левый кончик, 50% — верх, 100% — нижний-правый, gap по центру
  снизу). Осознанная девиация от литеральных чисел мока (фикс mock-бага;
  README-намерение сохранено). `geometry.ts`/тесты не тронуты (чистые
  `arcPath`/`pointAtPct` тестятся явными аргументами). Аффектит все 3
  Gauge-консьюмера (Wellness recovery hero, Wellness-empty ghost, Dashboard
  goal arc) — все становятся низ-открытыми (корректный единый вид).
  Wellness `arcWash` приведён к точным per-score тинтам дизайна
  (good/excellent→`brand-light`, moderate→`#f5e6c8`, low/skip→`#fde6e6`,
  no-score→`surface-2`); `arcColor` уже был верен (good/excellent=cobalt
  brand, moderate=amber, low/skip=coral, no-score=dim). code-reviewer APPROVE.
- **Cleanup:** удалены zero-importer dead-компоненты `DayNav StatusBadge
  AiRecommendation SportCtlBars MetricCard WeekNav` + orphan CSS
  `--accent-glow` / `@keyframes pulse-dot` (нулевые потребители после порта).
- **Settings interactive refinement-pass (2026-05-18, по запросам пользователя
  «по страницам», каждая правка → code-reviewer APPROVE):** (a) **бэкенд**
  `/api/auth/me` +`display_name`/`username` (из авторизованного user, не
  data_uid; контракт-тест `test_auth_me.py::TestIdentityExposed`, 12/12) →
  identity-карточка показывает реальное имя + инициалы-аватар, `@username`,
  сырой `role` английским как есть (не локализуется), без возраста (он в
  Personal); (b) **`TopBar` примитив**: дефолт-иконка = реальный
  `/endurai-icon.png` (был пустой кобальт-плейсхолдер) → лого видно глобально;
  (c) по явному запросу «без перевода как на дизайне / точь-в-точь»
  де-i18n'нуты и приведены к прототипу `BSettings`: TopBar `Profile`,
  Personal (label+sub-captions, exact type-scale), Active sports (Swim/Ride/
  Run литералы + плитки прототипа), Language (inline-row + EN/RU сегмент),
  MCP (single-config блок — RU-литералы как нарисовал дизайнер, ink-JSON +
  Копировать/Показать токен; copy/reveal/snippet логика verbatim, токен
  masked-by-default), Intervals connected (бренд-кобальт dot + `Live`
  brand-dark top-right; scope = wrapping mono-чипы, был overflow), кнопки
  (`BackfillSection` Halo-токенизирован + neutral-bordered, литеральные
  `Sync now`/`Retry`/`Disconnect`; стейт-машина byte-identical), Goals
  (per-goal cards: Race-pill+RU-priority+date, name, «Тип»-select-pill, big
  editable CTL Target + per-sport rows, dashed «+ Add goal»; `patchGoal`
  byte-identical). **Логика всех load-bearing хендлеров byte-identical**
  (подтверждено per-edit code-reviewer'ом). **Data-honest девиации:** нет
  per-sport current/progress в Goals (payload `/api/athlete/goals` несёт
  только targets); identity без TG-id (нет в payload). **Dead-CTA per
  literal-copy:** Goals «+ Add goal» (создание через бот `/race` — нет
  webapp-endpoint), как Plan «Adjust» / Workout «Start workout»; dropped
  `edit_via_chat_hint` (нет в моке) — UX-нит, осознанный продуктовый выбор.
  **i18n cleanup:** удалено ~40 dead-ключей (`settings.{mcp(all),language,
  russian,english,profile.{personal_title,auto_sync_intervals,sport_*},
  identity.{age,role_athlete,role_demo},goal.{title,category,event,date,
  ctl_target,*_ctl,sport_type,ctl_edit_hint_section,edit_via_chat_hint},
  intervals.{connected,method_oauth,disconnect}},backfill.{button_*,
  available_in_*}}`); shared-ключи (`sports.{swim,ride,run,save_failed,
  empty_warning}`, `goal.{save_failed,ctl_edit_hint,sport_type_options}`,
  `intervals.{method,connected_legacy,method_api_key,scope,disconnect_confirm,
  athlete}`) сохранены; en/ru parity 100%. Прецедент: «литерально как в
  моке» для Settings-лейблов (бренд/секции/роль/спорт) — но функциональные
  контролы (sport_type `<select>` опции) остаются i18n.

### 9.4 Decisions log (этот заход)

| Дата | Решение | Альтернатива | Причина |
|---|---|---|---|
| 2026-05-18 | **G4 = literal copy** | Settings-precedent (faithful + data-honest adapt) | Пользователь явно выбрал literal из 2 предложенных; прототип = source of truth, реверсит over-trims живого webapp но НЕ дизайн-фиксы прототипа |
| 2026-05-18 | **G5 backend-blocked = defer frontend-only** | spec+impl 3 backend-стори | Минимизировать риск/scope; честное текущее поведение сохранено; стори залогированы (§9.2), без issues |
| 2026-05-18 | **Полный структурный порт всех экранов, фазовый, code-reviewer на каждой, коммита нет** | Точечные правки / большой rewrite | Тот же дисциплинированный приём, что у Settings; load-bearing логика byte-identical, presentational-only |
| 2026-05-18 | **Progress = Halo-superset, только ink-pill дельта** | Полный per-widget rebuild под прототип | Прототип Progress = inline-SVG (конфликт с Chart.js-мандатом брифа); rebuild 1477-стр/7-charts = регресс-риск ≫ fidelity-выигрыш; precedent «keep superset» (Activity/Dashboard) |
| 2026-05-18 | **Чистка dead-кода в Phase I (6 компонентов + orphan CSS)** | Оставить до commit / отдельной задачи | Memory: zero-caller code goes outright; порт осиротил их, нулевые потребители подтверждены grep'ом, tsc/build/27-tests зелёные |

## 10. Halo-v2 wave — desktop + design-package re-drop (2026-05-19)

**Мотивация.** Дизайнер обновил `design-package/endurai` и пользователь явно:
«обновлён дизайн, обрати внимание: 1 появилась десктоп-версия, 2 в wellness ai
recommendation, 3 settings поле изменение возраста. и посмотри что ещё
поменялось». Подтверждённый scope (AskUserQuestion): **(1)** весь десктоп одним
заходом; **(2)** да — coach-плашка + новый `/coach` роут (реверсит G3=(b));
**(3)** полный Personal re-spec. Тот же дисциплинированный приём, что §9:
presentational-only, load-bearing byte-identical, фазовый, code-reviewer на
каждой фазе (все **APPROVE**, 0 Crit/High), **коммита нет**, tracking = только
этот spec (без GitHub-issues).

### 10.1 Phase ledger (все code-reviewer **APPROVE**, 0 Crit/High; коммита нет)

| Ph | Экран(ы) | Сделано | Verify |
|---|---|---|---|
| J | Wellness + `/coach` | **Реверс G3=(b)→(a):** новый роут `/coach` (`pages/Coach.tsx`, `dataRoute`-гейт, fetch `/api/wellness-day` сегодня, full `ai_recommendation` 24px, `coach.no_note` fallback, back-«‹ Wellness»); Wellness — ink coach-teaser-плашка после BodyCard, gated на `ai_recommendation?.trim()` (recovery chip+rec остаётся authoritative «что делать сегодня»). `useDayNav`/`useApi` byte-identical | tsc/build/vitest 27/27 |
| K | Wellness/Thresholds + Onboarding | VO₂max StatTile (литерал «VO₂max», из `Wellness.get_latest_vo2max` — новый `@dual` classmethod + `auth_me` profile.vo2max + `test_auth_me.py::TestProfilePersonalFields`); `OnboardingPrompt` default-ветка = прототип `BIntervalsConnect` (step-indicator, radial-backdrop, service-card, scope-checklist, privacy-note); «try demo» CTA выкинут (data-honest — юзер уже authed). `needsBotStart`/`startOAuth` byte-identical | ✓ |
| L | Settings Personal re-spec | `PersonalCard`: age-stepper (`BpStepper`-стиль, `clampAge` 18-90) + `BpSource`-бейджи (wellness/intervals provenance) + batch-save footer (2s autosave-таймаут + display-interval). Только Age writable; Weight/HR-max read-only с source-бейджами (G1=B прецедент — нет backend для Weight-override / HR-max popover-slider-history). `patchProfile` byte-identical | ✓ |
| M1 | Desktop shell + sidebar | `components/halo/HaloSidebar.tsx` (прототип `BdSidebar`, 240px=`w-60`, `hidden md:flex fixed`, inline line-SVG `NavIcon` по route, active=`brand-light`/`brand-dark`, what's-new-after-`/plan` сохранён, logout user-pill). `Layout` → `HaloSidebar` + `md:pl-60`; удалён dead `Sidebar.tsx` (legacy `--accent`/`EnduraiLogo`; `EnduraiLogo` сохранён — нужен `Landing`); `navItems.ts`/`useChangelog.ts` doc-комменты обновлены | ✓ |
| M2 | Desktop per-screen | Shared shell: `Layout` `md:!max-w-[1180px] md:mx-0` (`!important` бьёт non-important inline mobile-cap); `TopBar` — 2 breakpoint-switched хедера (mobile `md:hidden` без изменений + новый desktop sticky `hidden md:flex` = прототип `BdShell`-хедер: 24px title + opt `subtitle`, `-mx-9 px-9` bleed против `md:px-9`-gutter). Reflow существующих карточек в `md:`-грид: **Wellness** `md:grid-cols-[1.4fr_1fr]` (hero col1 rows1-2, Sleep+Load col2, Paired/Body/Coach full-width — flat DOM, mobile-порядок byte-identical); **Dashboard** `contents→md:grid-cols-2` (GoalCard hero+by-sport / LoadTab post-CTL / WeekTab list; SegmentedTabs `md:static` чтобы не коллидить со sticky-хедером); **Plan** desktop-only `DayColumn` в `md:grid-cols-7` (прототип `BdPlan` week-as-columns; mobile `DayCard` стек `md:hidden`); **Activities** desktop-only прототип `BdActivities` table (`ActivityTableRow`→`/activity/:id`; mobile cards+accordion `md:hidden`); **Settings** = `BdSettings` single-column в широком canvas (литерал-EN subtitle). 6 не-M2 Halo-страниц (Coach/Progress/Activity/ScheduledWorkout/WeeklyReport/WeeklyReports) получили `md:px-9` для alignment shared-хедера | tsc/build/vitest 27/27, i18n 412 ключей parity OK |
| N | spec + holistic | этот раздел; финальный holistic code-reviewer |
| O | Halo-v3 drop (Wellness + Settings) | **2026-05-20 design package re-drop.** (O.1) `pages/MetricDetail.tsx` — детальный sub-view HRV/RHR (прототип `BMetricDetail`, halo.jsx:758), data-honest: hero (today/unit/delta/status pill) + Statistics table из реальных `HRVBlock`/`RHRBlock` полей (mean_7d±sd_7d, mean_30d/60d±sd, bounds, cv_7d + verdict, swc + verdict, trend.direction+r², days_available — оказалось бэкенд уже отдаёт; первоначальная аудит-оценка «CV/SWC не в API» опровергнута повторной проверкой `api/types.ts`). Sparkline 60d-ряд + per-metric AI-interpretation — нет в API → §10.4 deferred story #5; коуч-нота живёт на `/coach` (one-voice), pointer-card к ней. (O.2) Роут `/wellness/:metric`, HRV/RHR плитки `PairedMetrics` → `<Link>` (block, hover, chevron-hint). (O.3) Wellness chrome: ticks `[33,66]→[40,70,85]` (соответствие `classifyRecovery`-гранцам, data-honest); Body card `updated_at` HH:MM (литерал-копия прототипа `обновлено …`, реальное время из `data.updated_at`). (O.4) `components/PersonalCard.tsx` — вынесен из `Settings.tsx` (PersonalCard + BpSource + AGE_MIN/MAX/clampAge, ~228 строк), bare-body форма (без card chrome — caller оборачивает); Settings обёрнут в `Panel label="Personal" hint=<Link Edit›>` (Halo-v3 affordance, прототип `BdSettings:755` «Редактировать»); новый `pages/PersonalEdit.tsx` (`/settings/personal/edit`, dedicated focused-page, тот же `<PersonalCard>` + независимый `/api/auth/me` fetch + `patchProfile` optimistic+rollback паттерн, byte-identical к Settings). **Полная мобильная и десктоп DOM byte-identical** (вынос — pure refactor); zero дублирования логики между Settings и PersonalEdit | tsc/build/vitest 27/27, i18n 431 ключ parity OK |
| P | Recovery trend detail (Wellness) | **2026-05-21.** Порт прототипа `BRecoveryDetail` + `BRecoveryTrendChart` (`direction-b-halo.jsx:2398-2672`). Новый роут `/wellness/recovery` + `pages/RecoveryTrend.tsx`: back-хедер + «Updated HH:MM», today-snapshot card (Recovery/HRV/RHR, colour-keyed к сериям графика), period-filter 1m/3m/6m, **dual-axis inline-SVG line chart** (Recovery score 0-100 на левой оси + tinted area, HRV/RHR на правой auto-fit оси) + toggle-легенда (≥1 серия всегда on). «Trend›»-пилюля добавлена в хедер `RecoveryHero` (`Wellness.tsx`) → ведёт на новый роут. **Backend:** `/api/recovery-trend` расширен — добавлена `rhr`-серия (`resting_hr`, Intervals-сентинел `restingHR=0` нормализован в null — конвенция `Wellness.recent_resting_hr`), `days`-кап 90→180 (6m-пилюля). Dashboard Load-tab консьюмер не затронут (rhr аддитивна, кап только поднят, skip-условие лишь строже). `RecoveryTrendSeries` +`rhr`. Сегодняшние headline-числа + HRV/RHR-дельты — из `/api/wellness-day` (как `MetricDetail`); серии графика — из `/api/recovery-trend`. Inline-SVG порт (не Chart.js) — точь-в-точь прототип; `preserveAspectRatio="none"` растягивает по ширине карты. null-точки пропускаются, линия спанит gap. i18n `recovery_trend.*` (4 ключа, EN+RU parity) | tsc/vite build ✓, vitest 29/29, pytest `test_dashboard.py` 77 ✓ (TestRecoveryTrend 8/8). code-reviewer: **APPROVE**, 0 Crit/High; M1 (`resting_hr=0` сентинел) — **исправлен** + тест. Коммита нет |
| Q | Sleep trend detail (Wellness) + финал Sleep-градации | **2026-05-21.** Порт прототипа `BSleepDetail` + `BMiniBarChart` + `BSleepScoreChart` (`direction-b-halo.jsx:2735-3214`). Новый роут `/wellness/sleep` + `pages/SleepTrend.tsx`: back-хедер, today-snapshot (Duration + Score + zone-chip), period-filter 1m/3m/6m, **Sleep duration** bar-chart (минуты, weekly-агрегация >45 дней, bar окрашен по score-зоне ночи, 8h-goal линия) + **Sleep score** zoned line-chart (4 зональные полосы, line-цвет по зоне точки) с легендой. `SleepCard` (`Wellness.tsx`) → `<Link>` на новый роут (chevron + score zone-chip), 7-ночные столбики перекрашены через общий `sleepZoneOf`. **Sleep-градация финализирована** (см. §10.3) — `SLEEP_ZONES`/`SLEEP_ZONE`/`sleepZoneOf` в `utils/recovery.ts` (single source of truth), локальный `SLEEP_TONE` в `Wellness.tsx` удалён. **Backend:** новый `/api/sleep-trend` (dates/duration_min/score, ≤180 дней, `sleep_secs=0` сентинел → null) — параллель `/api/recovery-trend`. `SleepTrendSeries` type. i18n `sleep_trend.*` (4 ключа, EN+RU parity) | tsc/vite build ✓, vitest 33/33, pytest `test_dashboard.py` 83 ✓ (TestSleepTrend 6/6). code-reviewer: **APPROVE**, 0 Crit/High; M3 (explicit `lo`-bound у `SleepZone` — убрал ad-hoc index-арифметику) + L3 — применены; M1 (`last_7_nights` type honesty) проверен — non-issue (`/api/report` + `/api/wellness-day` оба через `_build_wellness_response`). Коммита нет |
| R | Body trend detail (Wellness) | **2026-05-21.** Порт прототипа `BBodyDetail` + `BMiniLineChart` + `BMiniBarChart` (`direction-b-halo.jsx:2680-2960`). Новый роут `/wellness/body` + `pages/BodyTrend.tsx`: back-хедер, period-filter 1m/3m/6m, по карточке на метрику — **Weight / Body fat / VO₂max** (inline-SVG line-chart, area-fill, auto-fit Y) + **Steps** (bar-chart, weekly-агрегация >45 дней); у каждой карточки window-дельта (`vs Nd ago`, цвет по «good» направлению — weight/fat вниз = green; steps = window-avg, нейтрально). `BodyCard` (`Wellness.tsx`) → `<Link>` на `/wellness/body` (chevron), **2×2 сетка с добавленным Body fat** (поле `body.body_fat` уже было в API), decorative sub-captions прототипа убраны → orphan i18n `wellness.{weight,vo2,steps}_sub` удалены. **Backend:** новый `/api/body-trend` (dates/weight/body_fat/vo2max/steps, ≤180 дней; skip-row если все 4 null; None-only — конвенция `get_latest_weight`/`get_latest_vo2max`, без 0-сентинел-нормализации). `BodyTrendSeries` type. i18n `body_trend.*` (4 ключа) + `wellness.body_fat`, EN+RU parity. Метрика рендерится только при ≥1 valid-точке (data-honest — нет body_fat → карточка скрыта). null-точки в графиках пропускаются | tsc/vite build ✓, vitest 33/33, pytest `test_dashboard.py` 89 ✓ (TestBodyTrend 6/6). code-reviewer: **APPROVE**, 0 Crit/High; M2 (`vs Nd ago` → `over Nd` — body-метрики разрежены, дельта спанит окно, не точку N-дней-назад) + L2 (`fmtMd` dedupe) — применены; M1 (hardcoded `over Nd`/`window`) — оставлено литералом осознанно: consistent с Phase P/Q (`· 7d`, `Avg · 90d`) + §10.3 de-i18n metric-chrome precedent. Коммита нет |
| S | Training load detail (Wellness) | **2026-05-21.** Порт прототипа `BLoadDetail` + `BLoadChart` + `BTsbZoneChart` + `BSportTssChart` (`direction-b-halo.jsx:1626-2395`). Новый роут `/wellness/load` + `pages/LoadDetail.tsx`: back-хедер, headline CTL/ATL/TSB, period-filter 1m/3m/6m, **Fitness & fatigue** (CTL+ATL inline-SVG line-chart + toggle-легенда), **Form (TSB)** (zoned line-chart, **5-зонный PMC-banding** Transition/Fresh/Gray/Optimal/High-risk — выбор пользователя точь-в-точь по дизайну, см. §10.3) + zone-легенда, **Daily TSS by sport** (stacked bars Swim/Ride/Run, weekly-агрегация >45 дней, день-ось = `training-load.dates` чтобы шарить x-окно с line-графиками), **By sport** collapsible — **CTL only** (ATL per discipline отложен по запросу). `TrainingLoadCard` (`Wellness.tsx`) → `<Link>` на `/wellness/load` (chevron). **Будущее не рисуется** (по запросу): нет forecast-tail / dashed / planned-bars / today-rule / forecast-tint — субтитр без «+ 30-day forecast». **Backend:** `/api/training-load` расширен — `ctl_swim`/`ctl_ride`/`ctl_run` из `Wellness.sport_info` через `extract_sport_ctl` (аддитивно — Dashboard Load-tab не затронут); `/api/activities` переиспользован для TSS-баров. `TrainingLoadSeries` +3 поля. Chrome — литеральный EN (consistent с де-i18n'нутой `TrainingLoadCard`, без i18n-namespace) | tsc/vite build ✓, vitest 33/33, pytest `test_dashboard.py` 91 ✓ (TestTrainingLoad 9/9). code-reviewer: **APPROVE**, 0 Crit/High; L5 (тест interleaved-null per-sport CTL) — добавлен; M2/M3/L2 (single-point line невидим без dot / weekly x-label = chunk-start / `acts`-fetch error swallowed) — оставлены как design-faithful + non-critical. Коммита нет |
| T | Holistic review (P–S) | **2026-05-21.** Сквозной code-review всех 4 detail-экранов (Recovery/Sleep/Body/Load) — **APPROVE**, 0 Crit/High. Применены кросс-катящие фиксы: **M1** `/api/training-load` `days`-кап `365→180` (выровнен с тремя sibling-эндпоинтами; ни один консьюмер не просит >180 — Dashboard 84, LoadDetail ≤180); **M2** `body_trend` — добавлен комментарий, почему body-метрики НЕ нормализуют 0-сентинел (конвенция `get_latest_weight`/`get_latest_vo2max`; `steps=0` = реальный rest-day, не sentinel) + тест `test_zero_steps_kept_as_real_value`; **M3** `load_detail.*` i18n-namespace (`updated`/`no_data`) — «Updated …» и empty-state переведены (это UI-chrome, не метрик-вокабуляр; title/chart-titles остаются литералом); **L1/L2** комментарии на `SportTssChart` (weekly-SUM не average + `dates`-aligned инвариант); **L3** shadowed `ctlToday`→`sportCtl`. **L4** (RecoveryTrend empty-state — chart-only vs full-screen у siblings) — оставлено осознанно: оба варианта defensible, headline честно деградирует в «—», ре-структура approved-фазы ради косметики не оправдана | tsc/vite build ✓, vitest 33/33, pytest `test_dashboard.py` 92 ✓, i18n parity OK. Коммита нет |
| U | Chart scrubber (P–S графики) | **2026-05-21.** Дизайн обновил графики — добавлена вертикальная черта с текущими значениями при hover/touch. Порт `useChartScrubber` + `ChartScrubLine` + `fmtScrubDate` из обновлённого `direction-b-halo.jsx`. Новый shared-модуль `components/halo/ChartScrubber.tsx` (экспорт из `halo/index.ts`): `useChartScrubber(n, padL, innerW)` — pointer→idx через clientX→viewBox конверсию (работает несмотря на `preserveAspectRatio="none"`), `setPointerCapture` для drag вне SVG, `touchAction: pan-y`; `ChartScrubLine` — вертикальная линия + floating callout-бокс (дата + per-series значения, авто-флип влево у правого края). Подключён во **все 8 chart-компонентов**: `RecoveryTrendChart` (Recovery/HRV/RHR), `SleepDurationChart` (duration) + `SleepScoreChart` (score+зона), `MiniLineChart`+`MiniBarChart` (Body), `LoadLineChart` (CTL/ATL — +`label` в `lines`-prop под callout) + `TsbZoneChart` (TSB+зона) + `SportTssChart` (Swim/Ride/Run). null-точки → item опускается; bar-чарты скрабятся по индексу бара (N=M), callout-x = центр бара, weekly-режим добавляет «(wk)» к дате. Shared-модуль — осознанное отступление от «self-contained per page»: дизайн сам сделал scrubber общим, инлайнить pointer-handling+callout-layout ×8 = реальная дупликация инфраструктуры (не chart-rendering) | tsc/vite build ✓ (310 modules), vitest 33/33. code-reviewer: **APPROVE**, 0 Crit/High; M1 (`TsbZoneChart` скрабит `tsb[idx]` без null-guard, в отличие от 7 других) проверен — non-issue: `TrainingLoadSeries.tsb` = `number[]` dense by contract (эндпоинт фильтрует rows с ctl+atl notnull, `tsb = ctl−atl` всегда число); остальные 7 чартов гвардят т.к. их серии реально `(number\|null)[]`. M2/L1–L3 — non-blocking polish, design-faithful. Коммита нет |
| V | `1y` period filter (P–S) | **2026-05-21.** По запросу пользователя добавлен 4-й пресет периода **`1y`** (365 дней) ко всем 4 detail-экранам — `Range` type `… \| '1y'`, `RANGE_DAYS['1y']=365`, pill `['1m','3m','6m','1y']`. **Backend:** `days`-кап всех 5 эндпоинтов (`recovery-trend`/`sleep-trend`/`body-trend`/`training-load`/`activities`) поднят `180→365` — **отменяет Phase T M1** (тогда `training-load` опустили 365→180 «никому не нужно >180»; теперь нужно). Тесты `test_accepts_180_day_window`→`test_accepts_365_day_window` (365 ok / 366 → 422) ×3. **Проверка против реальных данных (MCP):** у пользователя wellness-история уходит ≥1 года назад (есть строки за 20.05.2025); `ctl`/`atl`/`resting_hr`/`recovery_score`/`weight` плотные на всём годе → CTL/ATL/TSB/Recovery/RHR/Weight линии покрывают весь 1y; `hrv`/`sleep_score`/`body_fat`/`vo2max`/`steps` — null в старых строках → их серии честно покрывают только недавний отрезок (null-spanning charts корректны). Bar-чарты при 365 дн → weekly-агрегация (~53 бара). per-sport `sport_info.ctl` год назад = `0.0` (литерал) → per-sport CTL линии стартуют с 0 — data-faithful | tsc/vite build ✓, pytest `test_dashboard.py` 92 ✓. Коммита нет |
| W | Удаление Load-таба с `/dashboard` | **2026-05-21.** По запросу пользователя — старые Chart.js-графики Load-таба Dashboard (CTL/ATL/TSB line, Form-TSB card, Daily-TSS-by-sport bars, Recovery+HRV chart) удалены: они дублируются новыми detail-экранами `/wellness/load` (Phase S) и `/wellness/recovery` (Phase P). `Dashboard.tsx` — удалены `LoadTab` + `FormTsbCard` + `LoadTabData` (~215 строк); `TabKey` `goal\|load\|recap` → `goal\|recap`; no-goal fallback `load`→`recap`. Очищены ставшие orphan импорты (`Chart`/`registerables`/`ChartCard`/`chartOptions`/`Link`/`StackedBar`/`useRef`/`TrainingLoadSeries`/`ActivitiesSeries`/`RecoveryTrendSeries`) — tsc (`noUnusedLocals`) подтвердил полноту чистки. Orphan i18n удалены (`dashboard.{tab_load,charts,detailed_trends,detailed_trends_sub,form_tsb}`), `desktop_subtitle` без «training load». Эндпоинты `/api/{training-load,activities,recovery-trend}` сохранены — их потребляют новые экраны. `components/ChartCard.tsx` сохранён — используется `Progress.tsx`. Dashboard теперь = Goal + Recap | tsc/vite build ✓, vitest 33/33, i18n parity OK. Коммита нет |
| X | Возврат Load-таба + English-вкладки | **2026-05-21.** По запросу пользователя Load-таб возвращён (3 вкладки Goal/Load/Recap). Лейблы вкладок — литеральный English (`TAB_LABEL` const, де-i18n как `BDashboard`-прототип) → orphan i18n `dashboard.{tab_goal,tab_recap}` удалены. `LoadTab` — placeholder (по выбору пользователя): только drill-down-карточка «Detailed trends → /progress» (то, что было внизу старого Load-таба под графиками; `dashboard.detailed_trends`+`_sub` восстановлены). Контент Load наполнится позже. no-goal fallback `recap`→`load`. **Recap-таб (Weekly reports по дизайну) — отдельная задача:** пользователь выбрал «с бэкенд-доработкой» — `weekly_reports` markdown уже содержит в секции «📊 Итог недели» compliance %, total TSS и sessions completed/planned, но не структурно; нужен `headline` + `miss_count` (структурно — column или парсер) + endpoint-поля + фронт-порт `BDashboard·recap`. План — следующий заход | tsc/vite build ✓, i18n parity OK. Коммита нет |
| Z | Recap-таб = Weekly reports (`BDashboard·recap`) | **2026-05-22.** Recap-таб Dashboard перестроен по дизайну `BDashboard·recap` (`direction-b-halo.jsx:1959`): вместо activity-volume `WeekTab` (`/api/weekly-recap`) — список реальных AI-отчётов из `/api/weekly-reports`. Карточка `RecapCard` = дата-рейндж + «This week»-пилюля, AI-заголовок, тоталы (время + TSS), per-sport строки (dot/Swim-Ride-Run/dur/km/tss), футер `CTL→CTL(Δ)`/`ramp`/`TSB`/chevron, тап → `/weekly/:week_start`. Cursor-пагинация «Load earlier weeks». TopBar Dashboard на Recap-табе → «Weekly reports» (`weekly.title`). **Решения пользователя (AskUserQuestion):** (1) **только реальные отчёты** — карточка = строка `weekly_reports`, эндпоинт `require_athlete`, demo теряет Recap-таб (отчёты упоминают травмы/контекст) → таб скрыт для `isDemo`, не 403-ErrorMessage; (2) **`miss` пропущен** — тон ramp-пилюли из значения ramp (>7/нед = amber, правило CLAUDE.md), без хрупкого scheduled-vs-actual. **Backend (без миграции — «парсер», не «column»):** (a) `extract_weekly_headline()` в `data/weekly_preview.py` — ведущий `# `-H1, `None` для legacy-отчётов → фронт fallback на `preview`; (b) `SYSTEM_PROMPT_WEEKLY` — первая строка отчёта теперь `# `-заголовок 3-6 слов, правило форматирования обновлено; (c) `/api/weekly-reports` list обогащён — `headline` + per-week `by_sport`/`ctl_start`/`ctl_end`/`ctl_delta`/`ramp`/`tsb_end` (helper `_week_training_stats` — та же агрегация, что делал `/api/weekly-recap`). **Cleanup (zero-caller после перепиливания консьюмера):** удалены `/api/weekly-recap` + `_nearest_wellness` + `TestWeeklyRecap` + типы `WeeklyRecapBucket`/`WeeklyRecapResponse` + `TSB_ZONE_COLORS` + orphan i18n `dashboard.week.*`. **Деталь-страница (`WeeklyReport.tsx`, по запросу пользователя):** убраны `right`-бейдж «Отчёт тренера» + prev/next-навигация (`shiftIsoDate`, `useNavigate`); back-линк «К списку» + not-found-CTA → `/dashboard?tab=recap` (Recap-таб = канонический список отчётов). Dashboard `activeTab` переведён на URL-параметр `?tab=` (`useSearchParams`, clamp к доступной вкладке) — чтобы deep-link на Recap-таб работал и восстанавливался по browser-back; orphan i18n `weekly.{coach_report,prev_week,next_week}` удалены. Subtitle «Пишется каждый понедельник…» убран с Recap-таба (остался на `/weekly`-странице). `/weekly`-страница (`WeeklyReports.tsx`) больше не имеет входящей nav, но route жив; полный route-fold `/weekly`→Recap — по-прежнему отдельный пункт (стр. 262) | tsc/vite build ✓ (310 modules), vitest 33/33, pytest 214 ✓ (weekly-preview/weekly-reports-routes/dashboard/prompts/changelog), flake8 + i18n parity ✓. code-reviewer: **APPROVE** (with minor), 0 Crit/High; M1 (demo-403) + M2 (Sunday-boundary тест) + L4 (`currentMondayIso` local/UTC mix) + L5 (link-strip в headline) — **исправлены**; M3 (2 DB-сессии на list — `list_for_user` reusable) / L6 (`/weekly` не использует `headline`) / L7 («Written every Monday» vs Sunday-cron, прототип-verbatim) — оставлены осознанно. Коммита нет |
| AA | All-history календарь (`BHistoryCalendar`) | **2026-05-22.** Новый экран `/wellness/history` — месячный календарь-хитмап recovery. Порт `BHistoryCalendar` из обновлённого дизайн-пакета (свежее локального `design-package` — выкачан через design-link). Полоска дат Wellness получила ведущую пилюлю «ALL HISTORY ›» (`DateStrip` +`leading`-prop) → `/wellness/history`. Экран (`pages/WellnessHistory.tsx`): месячная сетка Mon-first, день залит цветом recovery-бэнда (`classifyRecovery` 40/70/85 — data-honest; локальная heatmap-палитра `RECOVERY_BAND`, прецедент Sleep-zones), день#+score; навигация по месяцам (вперёд заблокирована), легенда, сводка месяца (avg/best/lowest). Тап по дню → `/wellness?date=YYYY-MM-DD`. `useDayNav` принимает optional initial date (clamp ≤ today), `Wellness` читает `?date` через `useSearchParams`. Полоска дат подрезана 4→3 дня (per дизайн `BDateStrip`). Дизайнер дал 2 варианта (calendar / weekly-list) — пользователь выбрал **calendar** (AskUserQuestion). Route `/wellness/history` смонтирован **до** `/wellness/:metric` (иначе `:metric` его перехватит). **Backend — ноль:** `/api/recovery-trend?days=60` (`require_viewer`, demo-ok) уже отдаёт per-day recovery. i18n `history.*` (10 ключей EN/RU) | tsc/vite build ✓, vitest 33/33, i18n parity OK (387). Коммита нет |
| AB | RacePlanPanel Halo re-skin (`RacePlanCard`) | **2026-05-22.** Закрыт последний легаси-остров Dashboard — §9.1 «Shared `ChartCard`/`RacePlanPanel` — своя фаза». `RacePlanPanel` + `RaceConditionsForm` пере-скинуты в Halo под прототип `RacePlanCard`: легаси-токены (`bg-surface`/`border-border`/`bg-accent`/`text-text-dim`/`bg-green-100`/`hover:bg-bg`) → Halo. **Кнопки:** «Generate plan» = full-width brand-пилюля `rounded-[12px]`; «Recalculate plan» = bordered + inline SVG-refresh-иконка (было «🔄 Regenerate plan» с эмодзи); generating/recalculating = inline-спиннер (`animate-spin`). **Пилюля карточки:** `ConfidenceBadge` (FINAL/LATE/MID/EARLY + tooltips) заменена на `{N}d to race` (новый проп `daysRemaining` = `goal.days_remaining`); «EARLY» — только для готового плана с бэкендным `confidence_tier==='early'`. Заголовок «Race execution plan» → «Race plan». **Контент has-plan** (headline/warmup/legs/fueling/transitions/contingencies/footer) — **superset сохранён** (бэкенд отдаёт больше секций, чем рисует мок — прецедент «keep superset» §9.3), Halo-skin + leg-row в `surface-2`-карточку с brand-бордером. `RaceConditionsForm` — Halo-skin, аккордеон с rotating-`›`, «Race conditions · optional» two-tone. **Логика byte-identical** (`useEffect`/`generate`/error-handling/`RateLimitNotice` не тронуты — presentational-only). i18n: `race_plan.{title,intro,regenerate_cta,regenerating}` обновлены, +`days_to_race` +`conditions.optional`; `ConfidenceBadge`/`TIER_BADGE` удалены | tsc/vite build ✓, vitest 33/33, i18n parity OK (390). Коммита нет |
| AC | Goal-таб «Progress · projection» карточка | **2026-05-22.** «По спорту»-карточка Goal-таба перестроена в `Progress · projection` (прототип `BDashboard`) — закрывает gap, который §9.3 осознанно отложил («per-bar inline projection убрана — прототип Goal её тогда не имел»; дизайн с тех пор обновился). Вместо простых баров `TaperBar` (cur/target + taper) — карточка с eyebrow «Progress · projection» + «race in {N}d», строка **Overall CTL** + Swim/Ride/Run; каждая строка = taper-бар + подпись `+{ramp}/wk · proj. {date}` + статус-пилюля (`on plan` / `Nw late` / `target reached` / `stalled`). Off-track-строки собираются в **футер-алерт внутри карточки** (`!`-бейдж) — отдельная warnings-карточка убрана. **Backend — ноль:** `/api/goal` уже отдавал `projection {ramp_per_week, projected_date, on_track, reason}` per goal + per sport (`project_ctl_target`) — фронт только форматирует. Хелперы `projectionInfo` / `ProjectionRow` / `weeksPastRace` / `fmtProjDate`; `formatProjectionWarning` — дата «Jul 5» вместо сырой ISO. Chrome — литеральный English (coaching shorthand `on plan`/`proj.`/`Nw late`, consistent с `formatProjectionWarning`); eyebrow «By sport» → «Progress · projection», orphan i18n `dashboard.by_sport` удалён; emoji со строк убраны (`SPORT_META.emoji` + `SPORT_ICONS`-импорт удалены — прототип `ProgressRow` = name-only) | tsc/vite build ✓, vitest 33/33, i18n parity OK (389). Коммита нет |
| AD | Load-таб = весь /progress влит внутрь (`BDashboard·load`) | **2026-05-22.** Dashboard Load-таб (был placeholder с одним «Detailed trends»-линком) наполнен — порт `tab==='load'` из обновлённого дизайна. Новый файл `pages/DashboardLoadTab.tsx` (~1450 стр). **Endurance Score** — статичная серая «coming soon»-карточка (метрики/бэкенда нет — явный выбор пользователя). Спорт-сегмент bike/run/swim + период-фильтр (swim). **bike/run:** Decoupling (last 5) · HR Zone Distribution · BikeReadiness|MarathonShape · EF-trend · Cardiac Drift · список сессий. **swim:** Pace · SWOLF · список заплывов. Все графики — hand-rolled **inline-SVG** (НЕ Chart.js — реверс §9.3, выбор пользователя «новые Halo-карточки, не переиспользовать Progress-виджеты»). **Backend — ноль:** `/api/progress` / `/api/polarization` / `/api/bike-readiness` / `/api/marathon-shape` уже есть; логика парсинга переиспользована из `Progress.tsx`. Placeholder `LoadTab` + orphan i18n `dashboard.detailed_trends*` удалены, +`load.*` namespace. **Дизайн-девиации:** HR/Power-тумблер Zone Distribution убран (polarization-бэкенд только HR-зоны, power нет); Endurance Score статичен. `/progress` пока жив (его контент теперь дублируется Load-табом, retire — отдельной задачей, выбор пользователя). Build делегирован general-purpose-subagent'у. code-reviewer: **APPROVE (with minor)**, 0 Crit/High-crash; исправлены: H1 (silent error-state в `BikeRunTrends`/`SwimTrends` → `ErrorMessage`), M2 (swim x-ось `2026-W12`→`W12`), M3 (polarization `signals` отрисованы футером), M4 (`days=28` коммент), L6 (`accessor`-hack убран) | tsc/vite build ✓, vitest 33/33, i18n parity OK (390). Коммита нет |
| AE | Load-таб — EF/Drift период-фильтр + `i`-тултипы | **2026-05-22.** Доработка Load-таба (AD) по фидбеку пользователя. **(1) Период-фильтр** 1m/3m/6m/1y для EF-trend + Cardiac-Drift — был пропущен при первом порте; прототип `BRangeSegmented` ставит его над EF/Drift-парой. Вынесен в новый компонент `EfDriftBlock` со своим `period`-state + своим `/api/progress`-фетчем (`days=PERIOD_DAYS[period]`); Decoupling-карточка выше осталась на фиксированном 180-дн окне (её «last 5» — оконно-независимая, фильтр её не ужимает) — `BikeRunTrends` теперь делает 2 `/api/progress`-вызова. **(2) `i`-инфо-тултипы** — порт прототиповых `InfoIcon`+`InfoPanel` (которые subagent опустил, code-review §Low). Добавлены на 6 карточек: Decoupling / Zone Distribution / EF trend / Cardiac Drift / Bike Readiness / Marathon Shape — кнопка `i` раскрывает тёмную панель с простым объяснением метрики. Тексты — `load.tip.*` (6 ключей, EN+RU, «просто и доступно» по запросу) | tsc/vite build ✓, vitest 33/33, i18n parity OK (396). Коммита нет |
| Y | Единые цвета по спорту | **2026-05-21.** В коде было 2 расходящихся per-sport палитры: `sportColor()`/дизайн-`SPORT_COLOR`/CLAUDE.md = Swim янтарь / Ride cobalt / Run коралл, а `CHART_COLORS.{swim,ride,run}` = синий/зелёный/оранжевый (его читали Dashboard `SPORT_META` «По спорту» + Progress sport-чарты). Пользователь выбрал канон **янтарь/синий/коралл** (дизайн-макет). Фикс: `CHART_COLORS.{swim,ride,run}` → канон-hex (`#d18b00`/`#3b6dff`/`#d94640` = `--color-amber/brand/coral`) — один edit чинит обоих консьюмеров (`SPORT_META` и Progress читают `CHART_COLORS.*`). Остальные точки (Wellness Training-load, LoadDetail, SleepTrend, Settings, Activities `sportColor()`) уже на каноне. После фикса вся webapp = одна палитра; CLAUDE.md sport-colour правило стало фактически верным (раньше «Dashboard SPORT_META follows it» было ложью) | tsc/vite build ✓. Коммита нет |

### 10.2 Logged deviations (Halo-v2)

- **G3 реверс:** §9 приземлял G3=(b) (AI-строка убрана с hero → «будущий
  `/coach`-view»). Halo-v2 дизайнер вернул coach как отдельный экран →
  реализован `/coach` + teaser. README §13 `[x] G3` остаётся «(b)»; здесь
  фиксируется, что «будущий view» материализовался в Halo-v2.
- **Personal — backend reality (G1=B прецедент):** прототип `BpPersonal`
  рисует Weight-override + HR-max popover/slider/history — **нет backend**.
  Реализован только Age (writable, `/api/athlete/profile`); Weight/HR-max
  read-only с `BpSource` provenance-бейджами. Залогировано как осознанная
  data-honest девиация (тот же приём, что G1=A→B-узкий §8).
- **Desktop fidelity-tier:** M2 = responsive reflow существующих
  mobile-faithful карточек в `md:`-гриды прототипа `BdShell`, НЕ внутренняя
  re-композиция каждой карточки под её desktop-вид. Конкретно: Wellness
  recovery-hero на десктопе остаётся mobile-композицией (gauge сверху,
  chip+breakdown-toggle снизу) в широкой col1 — прототип `BdWellness` рисует
  gauge **слева** + always-on breakdown справа + декоративные Today/7d/30d
  pills. Re-композиция каждого hero под desktop ≫ scope/regress-risk (5
  экранов); precedent «keep superset / presentational-only».
- **Wellness desktop — dropped:** прототип `BdWellness` Row2 имеет 3-ю карту
  «Восстановление · 7 дней» sparkline — **нет API** (`/api/wellness-day` не
  несёт 7d-recovery-серию); выкинута (data-honesty, как §9 recovery-sparkline).
  Body-card 4-я ячейка «Калории 2840» — нет поля, не воспроизведена (Body =
  Weight/VO₂max/Steps как §9).
- **Plan desktop — dropped:** прототип `BdPlan` под week-grid рисует
  weekly-summary stat-card (TSS-план/часы/сессии/длинная — fabricated, нет в
  `/api/scheduled-workouts`) + AI-«Тренер советует» card (coach-нота живёт на
  `/coach`). Обе выкинуты (data-honesty). `DayColumn` дата = только `dayNum`
  (i18n-консистентно), прототип = `{n} мая` (хардкод-RU-месяц) — осознанная
  i18n-девиация от мока (как §9.3 generic-TopBar). Open-CTA имеет доп.
  `workouts.length>0` guard (корректность-улучшение vs безусловная mock-кнопка).
- **Activities desktop:** прототип `BdActivities` = плоская table, row-chevron
  → detail-страница. Inline-accordion (`InlineDetail`) — mobile/current-only
  superset (§9 Phase E «accordion сохранён»), на десктопе **намеренно не
  воспроизведён** (прототип его не имеет; desktop row → `/activity/:id`).
  Mobile сохраняет accordion. Колонка «ЧСС ср.» = реальный `average_hr`;
  прототип-строка «07:30» (start-time) не воспроизведена — `ActivityItem` не
  несёт надёжный start-time (data-honesty).
- **Desktop subtitles = data-honest neutral:** прототип `BdShell`-subtitle'ы
  fabricated («Суббота, 16 мая · твой день для смешанной нагрузки», «Цель A ·
  … · через 35 дней», «11 – 17 мая · 5 сессий · 6 ч 25 мин»). Заменены на
  нейтральные дескрипторы (`{screen}.desktop_subtitle`, EN+RU parity) — не
  фабрикуем дату/вердикт/счётчики (прецедент §9.3 generic-TopBar). Settings
  desktop-subtitle = литерал-EN inline (консистентно с де-i18n'нутым Settings
  chrome §9.3, не i18n-ключ).
- **Shared-shell механика:** `Layout` desktop-canvas = `md:!max-w-[1180px]`
  (`!important` Tailwind-класс бьёт non-important inline `style={{maxWidth}}`
  — mobile-cap остаётся, desktop=прототип ~1100+gutters, left-aligned, без
  `mx-auto`). `TopBar` desktop-хедер `sticky top-0`; страницы с локальным
  sticky → `md:static` чтобы не коллидить: Dashboard SegmentedTabs (M2) +
  Progress sport-pills (Phase N holistic-review **H1** — не-M2 страница, но
  потребляет shared sticky `TopBar`; тот же one-class фикс, sweep подтвердил
  что только эти две имеют локальный `sticky top-0`).
  Контракт: каждая Halo-страница с `<TopBar>` обязана нести `md:px-9` (против
  хедерного `-mx-9`); проверено на всех 11 (incl. `hideBottomTabs`
  ScheduledWorkout/Activity — bleed self-contained внутри `md:!max-w`, нет
  overflow). Не-M2 страницы (Progress/Activity/WeeklyReport/…) на десктопе =
  single-column в широком canvas (acceptable, не M2-цель; полный desktop
  reflow вне scope этого захода).

### 10.3 Decisions log (Halo-v2)

| Дата | Решение | Альтернатива | Причина |
|---|---|---|---|
| 2026-05-19 | **Весь десктоп одним заходом (M1 shell+sidebar, M2 per-screen reflow)** | По экрану с паузами / отложить desktop | Явный выбор пользователя «весь десктоп одним заходом»; фазовый внутри (M1→M2), code-reviewer на каждой, без остановок-на-вопрос между экранами |
| 2026-05-19 | **M2 = responsive reflow существующих карточек, НЕ desktop-re-композиция каждой** | Форк mobile/desktop-дерева каждой карточки | Presentational-only/byte-identical инвариант + 5 экранов; внутренняя re-композиция hero (gauge-beside-breakdown) ≫ regress-risk; precedent «keep superset» |
| 2026-05-19 | **Plan/Activities = desktop-only sibling-компонент** (`DayColumn`/`ActivityTableRow`), mobile `md:hidden` | Один responsive-компонент на оба | Прототип desktop здесь = принципиально иная композиция (7-col week / table), не grid-reflow; sibling чище контортинга; ноль дублирования data-логики (pure-presentational, те же props) |
| 2026-05-19 | **G3=(b) реверс → `/coach` роут + teaser** | Оставить AI-строку убранной | Дизайнер вернул coach отдельным экраном в Halo-v2; пользователь подтвердил «плашка + новый /coach роут» |
| 2026-05-19 | **Personal: только Age writable, Weight/HR-max read-only+provenance** | Полный `BpPersonal` (Weight-override/HR-max slider/history) | Нет backend (нет миграции/эндпоинта); G1=B data-honest прецедент; пользователь подтвердил «полный Personal re-spec» в рамках реального backend |
| 2026-05-19 | **Не-M2 Halo-страницы: только `md:px-9`-alignment, не desktop-reflow** | Полный desktop-порт всех 13 страниц | M2-цель = 5 экранов прототипа `BdShell`; остальные desktop-single-column не хуже прежнего (не регресс); полный порт вне подтверждённого scope |
| 2026-05-20 | **Halo-v3: tier 1+2a/b+4 build now, tier 1b+2c defer (backend stories #4/#5)** | Литерально портировать `BMetricDetail` (sparkline+per-metric AI) + `direction-b-personal-edit.jsx` (Weight-override + HR-max popover+slider+history+auto-save) | G1=B precedent заново: фабриковать UI без backend = ровно та переусложнённость, против которой §9/§10. Аудит-агент ошибся заявив «CV/SWC не в API» — повторная проверка `api/types.ts` показала бэкенд **уже** отдаёт mean/sd 7-30-60d + cv_7d+verdict + swc+verdict + trend → MetricDetail построен почти на полном прототипе кроме sparkline-ряда и per-metric AI |
| 2026-05-20 | **`PersonalCard` вынесен в `components/`, не дублирован между Settings и PersonalEdit** | Дублировать PersonalCard в PersonalEdit / экспортировать internal Settings symbol | Single source of truth, zero дублирования логики autosave/optimistic-patch; bare-body форма позволяет caller'у обернуть в своё chrome (Settings → `<Panel>`, PersonalEdit → focused-page card) |
| 2026-05-20 | **Recovery gauge ticks: `[33,66]→[40,70,85]`** | Сохранить прототиповые визуальные 33/66 | Реальные границы `classifyRecovery` (low<40<moderate<70<good<85<excellent) — data-honest gradations; прототиповые 33/66 — наследие старого классификатора |
| 2026-05-20 | **MetricDetail: pointer-card к `/coach` вместо fabricated per-metric AI** | Сгенерить per-metric prose | Нет per-metric AI-эндпоинта; one-voice разговор живёт на /coach (G3=(b) реверс уже сделал coach отдельным экраном) |
| 2026-05-20 | **Sleep 4-категорийная шкала (Garmin-style): `<50 poor / 50-69 fair / 70-89 good / ≥90 excellent`** — **PROVISIONAL** до апдейта дизайна | (а) Прототип-3 (`<60/60-74/≥75`); (б) Recovery-4 (40/70/85); (в) Garmin-4 (50/70/90) — выбран (в) | Backend категории sleep_score не определяет (raw 0-100, вес 0.20 в recovery_score) — это product UX choice. Пользователь: «возможно цвета переиграем — что скажет дизайнер. берём от дизайна когда обновится». Текущий выбор — Garmin/Whoop standard широко узнаваемая, цвета совпадают с `classifyRecovery` (coral/amber/brand/status-green). Implementation: `classifySleep(score)` в `utils/recovery.ts` (JSDoc помечен **PROVISIONAL** + где обновить) + `SLEEP_TONE: Record<SleepCategory,{fill,border}>` в Wellness.tsx. Тесты `recovery.test.ts`. **Touch-up TODO когда дизайнер пришлёт финал:** (1) границы в `classifySleep`, (2) маппинг в `SLEEP_TONE`, (3) boundary-тесты, (4) эта запись. **✅ ФИНАЛИЗИРОВАНО 2026-05-21 (Phase Q):** дизайнер прислал Sleep-trend экран с zone-легендой — границы `<50/50-69/70-89/≥90` подтверждены (совпали с provisional Garmin-выбором, `classifySleep` не менялся); цвета взяты из прототипного `SLEEP_SCORE_ZONES` (poor `#dc2626` / fair `#d18b00` / good `#3b6dff` / excellent `#16a34a` — fair/good совпали с `--color-amber`/`--color-brand`, poor/excellent — литералы). Single source of truth — `SLEEP_ZONES`/`SLEEP_ZONE`/`sleepZoneOf` в `utils/recovery.ts`; локальный `SLEEP_TONE` в `Wellness.tsx` удалён, 7-ночные столбики и Sleep-trend экран берут цвет из общего map. PROVISIONAL снят с `classifySleep` JSDoc. |
| 2026-05-20 | **4-tab nav IA (Today / Week / Trends / Profile) — единая для mobile + desktop** | Сохранить 7-пунктовый desktop sidebar (как в `BdSidebar` прототипе) + 5-пунктовый mobile | Прототип `BBottomTabs` (direction-b-halo.jsx:82-88) теперь 4-tab + комментарий: «Plan + History merged into a single Week tab — past days show actuals, future days show plan». User явно подтвердил концепт merge'a → IA унифицирована на оба viewport'а. `/activities` `/progress` `/weekly` routes сохранены как deep-link (drill-down с Dashboard / detail-page chevron), но не в primary nav. `nav.activities/progress/weekly/history/wellness/plan/settings` ключи удалены как orphan; добавлен `nav.week`. `plan.title` → "Неделя"/"Week", subtitle → "Эта неделя — план впереди, факт по прошедшим дням" (передаёт суть merge'a). PlanScreen уже имеет toggle Week/Plan + `MergedWeek` default — структурно готов под новую IA без переписи. `HaloSidebar` `ICON` map очищен от 3 удалённых путей |
| 2026-05-21 | **Phase P: расширен существующий `/api/recovery-trend`, НЕ создан новый series-эндпоинт** | Новый `/api/wellness/{metric}/series` (deferred story #5) | Прототип `BRecoveryTrendChart` уже потребляет ровно форму `{dates, recovery, hrv}` — эндпоинт `/api/recovery-trend` (Dashboard Load-tab) её и отдаёт. Добавить `rhr` + поднять `days`-кап 90→180 = 3 строки, аддитивно, консьюмер Dashboard не затронут. Создавать параллельный per-metric series-эндпоинт = второй source-of-truth для той же серии — анти-паттерн. Story #5 остаётся открытой ТОЛЬКО для MetricDetail sparkline (per-metric ряд HRV/RHR с агрегатами — другой surface) |
| 2026-05-21 | **Phase P: chart = hand-rolled inline-SVG (порт `BRecoveryTrendChart`), не Chart.js** | Chart.js dual-axis (как Dashboard Load-tab recovery-chart) | Прецедент Halo Gauge/halo-примитивов — «inline SVG санкционирован для one-off графиков» (brief §2). Прототип сам hand-rolled SVG; порт точь-в-точь воспроизводит area+line, endpoint-dot, toggle-axis-dimming. Chart.js dual-axis потребовал бы форк-конфиг + потерю fidelity к моку. null-handling: точки-null пропускаются, линия спанит gap (daily wellness редко имеет дыры; разрыв на 1 день читается как баг) |
| 2026-05-21 | **Phase S: Form (TSB) график — 5-зонный banding прототипа, НЕ 4-зонная модель приложения** | App-модель 4 зоны (CLAUDE.md / `data/utils.py:tsb_zone` / `Dashboard FormTsbCard`: under-training >+10 / optimal −10..+10 / productive −25..−10 / risk <−25) | **Явный выбор пользователя** (AskUserQuestion): «5 зон точь-в-точь по дизайну». Прототип `TSB_ZONES` = Transition/Fresh/Gray/Optimal/High-risk, границы +25/+5/−10/−30 — generic PMC banding, расходится с app-моделью (дизайн зовёт −20 «Optimal», app — «productive overreach»). **Известная дивергенция:** TSB-зоны на `/wellness/load` ≠ TSB-вердикт на Dashboard / в боте / morning-report. `TSB_ZONES` живёт локально в `LoadDetail.tsx` (не трогает `data/utils.py:tsb_zone`). Если позже решим унифицировать — править здесь |
| 2026-05-21 | **Phase S: будущее не рисуется (forecast выкинут), by-sport — только CTL** | Полный порт `BLoadDetail` (30-дн forecast: dashed CTL/ATL/TSB tail, planned TSS-бары hatched, forecast-tint, today-rule; by-sport CTL+ATL) | **Явный запрос пользователя** «будущее пока не рисуй; данные по спортам пока только CTL». Forecast-данные есть (`/api/fitness-projection`) но намеренно не подключены — субтитр без «+ 30-day forecast», today-rule убран (без forecast-региона он бессмысленен — today = последняя точка). By-sport ATL отложен — `/api/training-load` отдаёт только `ctl_*` (per-sport ATL не добавлялся). Реактивировать = вернуть forecast-ветки charts + ATL-серии |
| 2026-05-21 | **Phase S: `LoadDetail` chrome — литеральный EN, без i18n-namespace** | `load_trend.*` namespace (как `recovery_trend`/`sleep_trend`/`body_trend`) | `TrainingLoadCard` на Wellness уже полностью де-i18n'нута (явный запрос «тут на английском все можно» — CTL/ATL/TSB/Fitness/Fatigue/Form/Swim/Ride/Run = бренд-метрики, §9.3/§10.3). Детальный экран наследует тот же de-i18n — «Training load», «Updated HH:MM», «Fitness & fatigue», «By sport» литералами. Единственное исключение — back-линк `t('wellness.title')` (навигация) и `t('wellness.load_error')` (generic fail). Recovery/Sleep/Body имеют i18n-title (переводимые концепты) — Training-load не имеет (метрик-вокабуляр), это не рассинхрон а тот же принцип |
| 2026-05-23 | **Tab routes переименованы: `/plan` → `/calendar`, `/dashboard` → `/trends`** | (а) оставить как есть; (б) `/plan` → `/week`; (в) полный URL=label (`/today`/`/week`/`/trends`/`/profile`) | **Выбор пользователя** (вариант B + `/calendar`). `/plan` рассинхронился с реальностью (теперь хостит merged Week — план+факт), а `/week` создавал визуальный конфликт с существующим `/weekly` (WeeklyReports deep-link). `/calendar` точнее передаёт merge план+факт и не конфликтует. `/dashboard` → `/trends` — URL = label, прозрачно. `/wellness` и `/settings` оставлены (короткие, читаемые, конвенциональные — переименование `/wellness/*` затронуло бы 6 nested routes без выгоды). Legacy paths `/plan` и `/dashboard` остаются как `<Navigate replace>` в `App.tsx` — не ломаются Telegram WebApp кнопки (race-plan PR4), букмарки, ссылки в morning report. Точки правки: `App.tsx`, `lib/navItems.ts`, `components/halo/HaloSidebar.tsx` (ICON map + changelog-injection check `/plan` → `/calendar`), `pages/ScheduledWorkout.tsx` (4 × backTo), `pages/Landing.tsx`, `pages/Wellness.tsx`, `pages/WeeklyReport.tsx` (RECAP_LIST_PATH), `bot/race_plan_telegram.py` + `tests/bot/test_race_plan_telegram.py` (web_app URL `/dashboard` → `/trends`). Telegram-команда `/dashboard` в `bot/main.py` оставлена — она открывает `API_BASE_URL` (root), а не `/dashboard` route, имя команды — отдельный UX-вопрос |
| 2026-05-23 | **Morning Telegram кнопка → `/coach?date=<wellness.date>`** (полный AI-rec markdown), не root `API_BASE_URL` | Оставить как есть | Утренний summary в Telegram — это короткий шаблон, а вся AI-проза, сгенерённая Claude+MCP, лежит в `wellness.ai_recommendation` и рендерится на `/coach`. Кнопка «Открыть отчёт» должна вести именно туда, иначе атлет тапает и попадает на metrics-tile экран без полной заметки. Date в URL пинит к нужному дню — Sunday-морнинг report, открытый в понедельник, всё равно покажет воскресный note (`/coach` дефолт «today» иначе уведёт). Weekly report кнопка не трогалась — она уже корректно ведёт на `/weekly/<iso_monday>`. Точки правки: `tasks/actors/reports.py:_actor_send_user_morning_report`; `tests/tasks/test_activity_actors.py::test_reply_markup_contains_webapp_link` усилен — теперь жёстко проверяет endswith `/coach?date={wellness.date}` |
| 2026-05-23 | **MetricDetail: «что это значит» card — rule-based pre-localized текст, не AI** (частичный реверс G3=(b)) | (a) полный AI per-metric (Claude generates per day per metric); (b) frontend rule-based в JS; (c) полный сохранить G3=(b) (только pointer на `/coach`, без factual card) | Дизайнер прислал макет с «ЧТО ЭТО ЗНАЧИТ» карточкой на `/wellness/:metric`. Текст в макете («3 утра подряд rMSSD выше 60-дневной базы — парасимпатика восстановлена. Можно по плану.») — НЕ AI-проза, а template assembly: streak count + status × delta phrase + recovery rec. Бэкенд продолжает паттерн `_cv_verdict` / `_swc_verdict` — pre-localized строка отдаётся в `HRVBlock.meaning` / `RHRBlock.meaning`, фронт рендерит verbatim. **One-voice rule сохраняется** — настоящая AI-проза по-прежнему живёт на `/coach`, pointer-card остаётся под meaning. Backend: `_HRV_MEANING_TPL`/`_RHR_MEANING_TPL` + `_hrv_meaning`/`_rhr_meaning` (status × streak → строка), `_hrv_streak_above_60d`/`_rhr_streak_below_30d` (join wellness×HrvAnalysis по последним 7 дням, считаем пока wellness.hrv>rmssd_60d / rhr_today<rhr_30d), `_morning_word_ru` (плюрал «утро/утра/утр»). HRV streak использует positive direction only (для green) — для yellow/red текст про «снизь нагрузку», streak не релевантен. Frontend: card между Stats и Coach-pointer (`bg-halo-brand-light` — lavender, читается как комментарий vs данные). i18n: только title-key `metric_detail.meaning_title`, текст сам — от бэка. Tests: 8 unit-тестов на шаблоны + 3 e2e на API |

### 10.4 Deferred backend stories (Halo-v3)

Продолжение §9.2; нумерация сквозная (#1–#3 уже там).

4. **Personal: manual-override columns + source enum + per-field PATCH** —
   `athlete_profile` (или новая `athlete_overrides`): для каждого editable
   поля (weight, max_hr per-sport, ftp, lthr_run, lthr_bike, css) колонки
   `*_manual` + `*_source` ('auto'/'manual'/'calculate'). PATCH endpoint
   с source-семантикой. Разблокирует прототип `direction-b-personal-edit.jsx`
   (Weight-override бейдж, HR-max popover/slider/source toggle, Thresholds
   edit form). UI-проект готов; сейчас Phase L+O.4 даёт только read-only +
   age-writable (G1=B precedent).
5. **HRV/RHR sparkline endpoint** — **✅ закрыто 2026-05-21**: вместо нового
   per-metric `/api/wellness/{metric}/series` расширили существующий
   `/api/recovery-trend?days=N` (+`rhr` поле, days-кап 90→180→365 для 1y),
   MetricDetail sparkline кушает его напрямую. Анти-паттерн «второй
   source-of-truth для той же серии» избегнут — см. decision log 2026-05-21.
   **Per-metric AI-interpretation НЕ строится** — закрыто one-voice
   решением 2026-05-23 (см. decision log): per-metric prose дублировал бы
   `/coach`, lavender «что это значит» card на MetricDetail закрывает
   «зачем сюда зашёл» через server-rendered rule-based `meaning`-поле
   (status × streak, `api/routers/wellness.py:_hrv_meaning`).
6. **Sleep 7-night series** — **✅ закрыто 2026-05-20**: `sleep.last_7_nights:
   (number|null)[]` добавлен в `/api/wellness-day` (через `Wellness.
   get_sleep_series` `@dual` classmethod, окно `[target-6, target]`, oldest
   first, missing days = None). SleepCard заменил misleading «один score
   circle» на 7-bar strip (прототип `BWellness` direction-b-halo.jsx:441-470):
   today выделен плотным fill своей категории, прошлые ночи — border + бледный
   fill, шкала цвета совпадает с Recovery gauge (40/70/85 → coral/amber/brand/
   status-green). Note: исходное предложение «7d avg score» снято — series
   полезнее (показывает variance, не один агрегат).
7. **Per-step plan-vs-actual breakdown на Activity detail** — прототип
   `BActivityWorkout` (direction-b-halo.jsx:2195) рисует пошаговую таблицу
   Plan/Actual/Δ × тон для каждого шага плана (Warmup / Interval 1 / ... /
   Cooldown). Текущая реализация на `webapp/src/pages/Activity.tsx` ограничена
   row-level `PlanVsActualMini` (Время + Нагрузка). Backend-блокировано:
   нужен per-step actuals split (что-то вроде `compute_step_metrics(activity,
   workout_doc.steps)` — выровнять фактические семплы по плановым step-границам
   с фолбэком на equal-time split, если step-time-bounds в FIT-стриме
   отсутствуют). Story не входила в spec-аудит до 2026-05-23 — `Activity.tsx`
   уже несёт inline-комментарий про backend-limit, но §10.4 пункта не было.
