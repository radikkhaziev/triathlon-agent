# Webapp Restructure: Navigation, Pages, Design

> Перестройка структуры страниц, добавление глобальной навигации, устранение дублирования.

---

## Проблемы текущей версии

### 1. Дублирование Report / Wellness / Dashboard-Today

Три разных URL показывают практически одни и те же данные:

| Данные | `/report` | `/wellness` | `/dashboard` (Today) |
|---|---|---|---|
| Recovery gauge | ✓ | ✓ | ✓ |
| HRV (dual algo) | ✓ | ✓ | delta only |
| RHR | ✓ | ✓ | value only |
| Sleep | ✓ | ✓ | score only |
| Training Load | ✓ | ✓ | CTL only |
| AI recommendation | ✓ | ✓ | ✓ |
| Body metrics | — | ✓ | — |
| ESS/Banister | — | ✓ | — |
| DayNav | — | ✓ | — |

Report — это Wellness без DayNav и body metrics. Dashboard Today — это Report с меньшей детализацией.

### 2. Нет глобальной навигации

Каждая страница — standalone с back-ссылкой на Landing. Пользователь вынужден возвращаться на Landing для перехода между разделами. На мобильном (Telegram Mini App) это 2 тапа вместо одного.

### 3. Landing — маркетинговая, не функциональная

Hero, features, "How It Works" — полезно при первом визите. Для ежедневного использования — пустая трата экрана. Авторизованный пользователь хочет видеть данные, а не промо.

### 4. Нет заглушек для будущих фич

Adaptation Log, Patterns (Gemini), Settings, Ramp Tests — нигде не представлены.

---

## Новая структура

### Страницы

| Route | Страница | Назначение | Статус |
|---|---|---|---|
| `/` | **Today / Landing** | Авторизованный → Today hub. Неавторизованный → Landing (промо + вход) | Новая Today + Landing остаётся |
| `/wellness` | **Wellness** | Полная аналитика дня с DayNav | Merge Report + Wellness |
| `/plan` | **Plan** | Недельный план тренировок (WeekNav) | Без изменений |
| `/activities` | **Activities** | Недельные активности (WeekNav) | Без изменений |
| `/activity/:id` | **Activity** | Детали активности + DFA | Без изменений |
| `/dashboard` | **Dashboard** | Аналитика: Load, Goal, Week | Убрать Today tab |
| `/login` | **Login** | Авторизация по коду | Без изменений |
| `/settings` | **Settings** | Профиль + пороги + конфиг + logout | Заглушка |

### Bottom Tabs

5 табов, всегда видны внизу экрана:

```
┌─────────────────────────────────────────┐
│                                         │
│           Page Content                  │
│                                         │
├─────┬─────┬─────┬─────┬────────────────┤
│ 🏠  │ 📋  │ 🏃  │ 💚  │ ⚙️            │
│Today│Plan │Act  │Well │More            │
└─────┴─────┴─────┴─────┴────────────────┘
```

| Tab | Icon | Route | Label |
|---|---|---|---|
| Today | 🏠 | `/` | Today |
| Plan | 📋 | `/plan` | Plan |
| Activities | 🏃 | `/activities` | Activities |
| Wellness | 💚 | `/wellness` | Wellness |
| More | ⚙️ | — | More (popup menu) |

**More menu** (popup/sheet):
- Dashboard → `/dashboard`
- Settings → `/settings`

**Active state:** подсвеченная иконка + label для текущей страницы.

**Telegram Mini App:** bottom tabs над системной клавиатурой. `position: fixed; bottom: 0`. Учитываем `safe-area-inset-bottom`.

**Activity Detail** (`/activity/:id`): bottom tabs скрыты — это drill-down страница с back-ссылкой на Activities.

---

## Детали по страницам

### `/` — Today / Landing

Для авторизованного пользователя — функциональный Today hub. Для неавторизованного — Landing (текущая промо-страница с кнопкой входа). Landing остаётся — без неё десктоп-пользователь без Telegram не поймёт что за сервис.

```
┌─────────────────────────────────┐
│  🟢 Recovery 82/100             │
│  Good — Z2-Z3, до 90 мин       │
├─────────────────────────────────┤
│  📋 Plan today                  │
│  🚴 Z2 Endurance 60min          │
│  🏊 Easy Swim 30min             │
│  [ADAPTED: Z2 Run 35min]       │
├─────────────────────────────────┤
│  🏃 Last activity               │
│  Yesterday — Run 45min, TSS 52  │
│  → Details                      │
├─────────────────────────────────┤
│  📊 Quick stats                 │
│  CTL 45 | TSB +3 | HRV ↑       │
├─────────────────────────────────┤
│  🤖 AI Recommendation           │
│  (collapsed, tap to expand)     │
└─────────────────────────────────┘
```

**Секции:**
1. **Recovery card** — gauge, score, category, emoji, краткая рекомендация
2. **Today's plan** — тренировки на сегодня из `/api/scheduled-workouts` (только сегодня). Включая `[AI]` и `[ADAPTED]` тренировки если есть
3. **Last activity** — последняя активность с ключевыми метриками, ссылка на details
4. **Quick stats** — CTL, TSB, HRV delta (одна строка)
5. **AI recommendation** — collapsed по умолчанию, tap to expand. Claude + Gemini tabs

**API:** `/api/report` + `/api/scheduled-workouts?week_offset=0` (два существующих endpoint, без нового `/api/today`).

**Auth:** Авторизованный → Today hub. Неавторизованный → Landing.

### `/wellness` — Wellness (merge Report + Wellness)

Объединённая страница с DayNav. Сегодня по умолчанию.

```
┌─────────────────────────────────┐
│  ← Today      28 мар 2026   →  │
├─────────────────────────────────┤
│  Recovery     82/100 🟢        │
│  ESS 45 | Banister 78%         │
├─────────────────────────────────┤
│  Sleep        75/100            │
│  7h 20m                        │
├─────────────────────────────────┤
│  HRV    [Flatt] [AIE]          │
│  48.2 ms  δ +5.2%  🟢         │
│  7d: 45.8 | 60d: 44.1          │
│  Bounds: 42.3 — 49.3           │
│  CV: 8.2% (stable)             │
├─────────────────────────────────┤
│  RHR         52 bpm  🟢        │
│  30d: 54 | δ -2                 │
├─────────────────────────────────┤
│  Training Load                  │
│  CTL 45 | ATL 38 | TSB +7      │
│  Ramp: 2.1                     │
│  🏊 12 | 🚴 22 | 🏃 11        │
├─────────────────────────────────┤
│  Body                           │
│  78.5 kg | BF 18% | VO2 48     │
├─────────────────────────────────┤
│  🤖 AI Recommendation           │
│  [Claude] [Gemini]              │
│  ...                            │
└─────────────────────────────────┘
```

**API:** `/api/wellness-day?date={date}` — уже содержит все нужные данные.

**Что меняется vs текущий Wellness:** ничего по данным. Просто Report (`/report`) удаляется, его роль берёт Today hub.

### `/plan` — Plan (без изменений)

WeekNav, дни с тренировками, expandable description. Без изменений.

Единственное дополнение: визуальная метка `[AI]` / `[ADAPTED]` для тренировок с `external_id` начинающимся на `tricoach:`.

### `/activities` — Activities (без изменений)

WeekNav, дни с активностями, inline expansion, link to detail. Без изменений.

### `/activity/:id` — Activity Detail (без изменений)

Full stats, zones, intervals, DFA. Bottom tabs скрыты.

### `/dashboard` — Dashboard (убрать Today tab)

**Было:** 4 таба — Today, Load, Goal, Week.
**Стало:** 3 таба — Load, Goal, Week.

Today tab дублировал информацию с Today hub страницы. Убираем.

Load tab по умолчанию при входе.

### `/training-log` — Training Log (заглушка)

Заглушка для Фазы 3 ATP. Показывает:

```
┌─────────────────────────────────┐
│  📋 Training Log                │
│                                 │
│  Coming soon                    │
│                                 │
│  История тренировок с полным    │
│  контекстом: состояние до,      │
│  нагрузка, восстановление       │
│  после. Основа для              │
│  персонализации.                │
│                                 │
│  Зависит от: ATP Фаза 3        │
└─────────────────────────────────┘
```

### `/patterns` — Patterns (заглушка)

Заглушка для Gemini Role Spec. Показывает:

```
┌─────────────────────────────────┐
│  🧠 Personal Patterns           │
│                                 │
│  Coming soon                    │
│                                 │
│  Еженедельный анализ            │
│  персональных паттернов         │
│  восстановления и адаптации.    │
│  Обновляется по понедельникам.  │
│                                 │
│  Зависит от: Training Log       │
│  (30+ записей)                  │
└─────────────────────────────────┘
```

### `/settings` — Settings (заглушка)

```
┌─────────────────────────────────┐
│  ⚙️ Settings                    │
│                                 │
│  Athlete Profile                │
│  Age: 43 | LTHR: 153           │
│  FTP: 233W | CSS: 141s         │
│                                 │
│  Race Goal                      │
│  Ironman 70.3 — Sep 15, 2026   │
│  CTL target: 75                 │
│                                 │
│  AI Workouts                    │
│  ☐ Auto-generate (coming soon) │
│  ☐ Auto-push to Garmin         │
│                                 │
│  Auth                           │
│  Role: owner                    │
│  [Logout]                       │
└─────────────────────────────────┘
```

Settings может отображать текущие значения из конфига (read-only на первом этапе). Logout — здесь, а не в More menu.

---

## Компонент BottomTabs

### Новый компонент `components/BottomTabs.tsx`

```tsx
// Sticky bottom navigation bar
// 5 tabs: Today, Plan, Activities, Dashboard, More
// Active state: highlighted icon + label
// More: opens popup with secondary pages
// Hidden on: /activity/:id, /login
```

### Интеграция в Layout

```tsx
// Layout.tsx — добавить BottomTabs
<div className="px-4 pb-20 mx-auto" style={{ maxWidth }}>
  {/* pb-20 — отступ для bottom tabs */}
  {backTo && <BackLink />}
  {title && <Title />}
  {children}
</div>
{showBottomTabs && <BottomTabs />}
```

`pb-20` (80px) — отступ снизу чтобы контент не прятался за табами.

### Props

```tsx
interface LayoutProps {
  children: ReactNode
  title?: string
  backTo?: string
  backLabel?: string
  maxWidth?: string
  hideBottomTabs?: boolean  // true для /activity/:id, /login
}
```

### Стили

```css
/* Bottom tabs container */
position: fixed;
bottom: 0;
left: 0;
right: 0;
height: 64px;
padding-bottom: env(safe-area-inset-bottom);
background: var(--surface);
border-top: 1px solid var(--border);
display: flex;
justify-content: space-around;
align-items: center;
z-index: 50;

/* Active tab */
color: var(--accent);

/* Inactive tab */
color: var(--text-dim);
```

### More Menu

Popup (sheet from bottom) при нажатии на More:

```tsx
// Overlay + sheet
// Links: Dashboard, Settings
// Tap outside to close
// Заглушки (Training Log, Patterns) добавляются при реализации фич
```

---

## Дизайн-токены

Текущая светлая тема (из `webapp/src/styles/index.css`):

```css
:root {
  --bg: #ffffff;
  --surface: #f5f5f7;
  --surface-2: #ebebf0;
  --border: #d4d4dc;
  --text: #1a1a2e;
  --text-dim: #6b6b80;
  --accent: #3b82f6;
  --accent-glow: #3b82f618;
  --green: #16a34a;
  --yellow: #d97706;
  --orange: #ea580c;
  --red: #dc2626;
  --button: var(--tg-theme-button-color, #3b82f6);
  --button-text: var(--tg-theme-button-text-color, #ffffff);
}
```

Telegram theme overrides убраны. Только `--button` и `--button-text` используют `--tg-theme-*` с fallback. Остальное — фиксированная светлая тема.

---

## Маршрутизация (обновлённая)

```tsx
// App.tsx
<Routes>
  <Route path="/" element={isAuthenticated ? <Today /> : <Landing />} />
  <Route path="/wellness" element={<Wellness />} />
  <Route path="/plan" element={<Plan />} />
  <Route path="/activities" element={<Activities />} />
  <Route path="/activity/:id" element={<Activity />} />
  <Route path="/dashboard" element={<Dashboard />} />
  <Route path="/login" element={<Login />} />
  <Route path="/settings" element={<Settings />} />
  <Route path="/report" element={<Navigate to="/wellness" />} />
  <Route path="*" element={<Navigate to="/" />} />
</Routes>
```

`/report` — redirect на `/wellness` для обратной совместимости.

---

## API изменения

Новые backend endpoints **не нужны**. Today hub использует два существующих запроса:
- `/api/report` — recovery, HRV, training load, AI recommendation
- `/api/scheduled-workouts?week_offset=0` — тренировки на сегодня

Если в будущем потребуется оптимизация — можно добавить агрегирующий `/api/today`, но начинаем без него.

---

## План реализации

### Этап 1: BottomTabs + Layout (1 день) — Done
- [x] Компонент `BottomTabs.tsx` — 5 табов: Today, Plan, Activities, Wellness, More
- [x] More menu (popup/sheet) — Dashboard, Settings
- [x] Обновить `Layout.tsx` — `hideBottomTabs` prop, bottom padding (`pb-20`)
- [x] Active state по текущему route
- [x] Скрывать на `/activity/:id` и `/login`

### Этап 2: Today hub (1 день) — Done
- [x] Новая страница `Today.tsx` — recovery card, plan today, quick stats, AI recommendation
- [x] `/` → авторизованный: Today, неавторизованный: Landing (Landing.tsx остаётся)
- [x] API: `/api/report` + `/api/scheduled-workouts?week_offset=0`
- [x] Bottom tabs видны на Today

### Этап 3: Dashboard — убрать Today tab (0.5 дня) — Done
- [x] Убрать Today tab из Dashboard
- [x] Load tab по умолчанию

### Этап 4: Merge Report → Wellness + redirect (0.5 дня) — Done
- [x] Wellness содержит всё что было в Report
- [x] Report.tsx удалён, `/report` → redirect `/wellness`
- [x] Bot URLs обновлены (`/` вместо `/report`)

### Этап 5: Settings (0.5 дня) — Done
- [x] `/settings` — read-only конфиг (атлет, цель, AI workout flags) + logout

### Этап 6: Plan — метки (generated)/(adapted) (0.5 дня) — Done
- [x] Суффиксы `(generated)` / `(adapted)` в name тренировки — рендерятся автоматически через `w.name`

---

## Критерии готовности

- [x] Bottom tabs видны на всех основных страницах (Today, Plan, Activities, Wellness, Dashboard, Settings)
- [x] More menu открывает Dashboard и Settings
- [x] `/` → авторизованный: Today hub, неавторизованный: Landing
- [x] `/report` редиректит на `/wellness`
- [x] Dashboard без Today tab, Load по умолчанию
- [x] Bottom tabs скрыты на `/activity/:id` и `/login`
- [x] `safe-area-inset-bottom` учтён для Telegram Mini App
- [x] Метки `(generated)` / `(adapted)` видны в Plan для AI-тренировок
