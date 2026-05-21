# Weekly Changelog Spec

> Еженедельная авто-сводка «что нового» — собирается из merged PR'ов в `main` за неделю, переводится Claude'ом в athlete-friendly текст, публикуется как GitHub Discussion в категории `Announcements`. Webapp показывает ссылку «📝 What's new» в sidebar — атлет открывает когда сам захочет, никаких push-нотификаций.

**Status:** ✅ Phase 1 (PR1 + PR2 + idempotency) shipped 2026-05-10. Phase 2 deferred until first EN athlete arrives.

**Related:**

| Ссылка | Связь |
|---|---|
| `tasks/actors/changelog.py` | Dramatiq actor `actor_publish_weekly_changelog` |
| `api/routers/changelog.py` | REST `GET /api/changelog/latest` |
| `bot/scheduler.py:scheduler_publish_weekly_changelog_job` | Sun 15:00 Belgrade cron |
| `webapp/src/components/halo/HaloSidebar.tsx` | Unread-badge link (desktop sidebar) |
| `webapp/src/pages/Wellness.tsx` | Mobile unread teaser (после Halo-port карточка `BotChatBanner`-like) |
| `webapp/src/hooks/useChangelog.ts` | Singleton fetch hook (shared между HaloSidebar и Wellness teaser) |
| `data/github.py:LATEST_DISCUSSION_QUERY` | Shared GraphQL query (actor + endpoint) — single source of truth |
| `mcp_server/tools/github.py:create_github_issue` | Reference — паттерн использования `GITHUB_TOKEN` |

POC: Discussion #334 (2026-05-09 — manual через `gh api graphql` для проверки flow до spec'и).

---

## 1. Мотивация

Pull-модель через GitHub Discussion + sidebar link:
- Атлет сам решает когда смотреть → нет notification fatigue (morning/evening/weekly reports уже есть).
- GitHub-native: RSS feed, comments, reactions, search.
- Источник = merged PRs — PR descriptions уже пишем для ревью, переиспользуем.
- Owner side-effect: видит публичный список своих изменений за неделю → дисциплинирует scope creep.

Альтернативы (push в TG / page с manual changelog / per-release push) отвергнуты — см. Decisions log.

---

## 2. Scope

**Phase 1 (✅ done):** actor + cron + pre-filter + Claude rewrite + GitHub Discussion publish + REST endpoint + webapp sidebar link с unread-only indicator + weekly idempotency.

**Phase 2 (deferred until first EN athlete):** bilingual single Discussion (Russian + English секции в одном Discussion'е, не two-Discussions; webapp `User.language=="en"` добавляет якорь `#english` к URL). Email digest для opt-in (`User.email_digest_optin`). Markdown inline-рендер в UI вместо open-in-new-tab.

**Вне scope:**

- Push в Telegram (см. Decisions).
- Per-user changelog — требует PR↔user-story attribution, отдельная задача.
- Auto-detect breaking changes / migration warnings.
- Multi-repo агрегация.

---

## 3. Data flow

`Sun 15:00 Belgrade cron → actor_publish_weekly_changelog.send() (no-arg, не per-user) → 8 шагов`:

1. **Idempotency lookup** — `fetch_latest_discussion()` (`data/github.py`). Если последний Discussion создан в пределах текущей недели (`created_at ≥ week_start = now − 6d`) → `skipped_already_published`. Окно **строго короче** периода cron (7d), иначе прошлонедельный Discussion (~7d) подавлял бы текущий запуск — см. §14, инцидент 2026-05-17.
2. **Fetch merged PRs** — REST `pulls?state=closed&base=main`, paginated, stop когда `updated_at < since`.
3. **Pre-filter** (cheap, без Claude — §4).
4. **Empty?** → log + return (`skipped_no_prs` / `skipped_all_filtered`).
5. **Build Claude prompt** — title + URL + `body[:1500]` per PR, **top-50** cap (`MAX_PRS_FOR_CLAUDE`).
6. **Call Claude** (`claude-sonnet-4-6`, `max_tokens=800, temperature=0.3`); если sentinel `NO_USER_FACING_CHANGES` → log + return.
7. **Wrap output** в Discussion body (§6) → GraphQL `createDiscussion`.
8. **Log success** с URL + number; `skipped_error` + Sentry на любой ошибке.

Implementation: `tasks/actors/changelog.py:publish_weekly_changelog`. **Никогда не raises** — fail-soft на каждой ветке.

---

## 4. Pre-filter rules

Запускается ДО Claude — экономит токены на очевидно internal PR'ах.

- **Authors:** `SKIP_AUTHORS = {"dependabot[bot]", "github-actions[bot]", "renovate[bot]"}`.
- **Title regex hard-drop:** `^(chore|ci|build|test|docs):` (case-insensitive). `perf|style|refactor` **НЕ** в hard-drop — `perf:` обычно user-facing («дашборд в 3× быстрее»), `style:` чаще про UI Tailwind, `refactor:` иногда меняет UX. Trust Claude (+5-7k input tokens worst-case, ~$0.02/неделю).
- **Labels:** `skip-changelog` / `internal` / `dependencies` → drop.
- **Dedup key:** `(title.lower().strip(), sha1(body[:200])[:8])` — оставить newest по `merged_at`. **Не title-only** — stacked PRs могут иметь одинаковый title но разные bodies. POC observation: #318/#320 байт-идентичны (re-merge артефакт после force-push).
- **Branch filter:** `pr.base.ref == "main"` — игнорируем merge'ы в feature branches.

---

## 5. Claude prompt & output

Source of truth: `tasks/actors/changelog.py:PROMPT_TEMPLATE`. Rules:

- 3-7 буллетов на русском, активный залог («Теперь можно X»).
- Группировка по темам с emoji-заголовками (🎯 Цели, 🧪 Тесты, 📊 Отчёты, 🔌 Onboarding, 🐛 Багфиксы, 🌐 Webapp, 🤖 Чат).
- НЕ упоминать PR-номера, файлы, классы, миграции.
- Если все PR'ы internal → ровно строка `NO_USER_FACING_CHANGES` (sentinel, actor skip publish).
- Без введения/заключения.

**Body truncation rationale (deviation от initial 500-char):** 1500 chars + `"... [truncated]"` суффикс. Наши PR descriptions в среднем 800-1500 chars («What was done / How to verify»), 500 резали бы именно «How to verify» — ту часть откуда Claude видит user impact. **Worst case с top-50 cap:** 50×1500 ≈ 18.75k input tokens, ~$0.06/прогон. Реалистично 30-40 PR/неделю → ~$0.04/неделю.

Discussion body wrapper:

```markdown
> Сводка изменений за неделю. Сгенерирована автоматически из merged PR'ов
> в `main` (пропущены рефакторинги, миграции, обновления зависимостей).

{claude_output}

---

[Полный список merged PR'ов за неделю →](https://github.com/{owner}/{repo}/pulls?q=is%3Apr+merged%3A%3E%3D{since}+base%3Amain)
```

Title format: `✨ Что нового — неделя DD–DD MMM YYYY` (понедельник → воскресенье, месяц на русском).

---

## 6. Persistence

**GitHub Discussion в категории `Announcements`** — semantically: Issue = bug/task; Discussion = announcement. `Announcements` уже в дефолтном наборе при включении Discussions. RSS feed + comments/reactions + не засоряет Issue tracker.

**Cached IDs** в env vars (не дёргать GraphQL `repository.id` каждый раз):

```
CHANGELOG_REPO_ID=R_kgDORnuZCQ
CHANGELOG_DISCUSSION_CATEGORY_ID=DIC_kwDORnuZCc4C8reQ
```

**Opt-in defaults:** пустые в `config.py`, prod values в `.env.example`. Forks с `GITHUB_TOKEN` иначе случайно лезли бы в upstream Announcements — explicit copy-on-deploy (Copilot review #335).

---

## 7. APScheduler job

`scheduler_publish_weekly_changelog_job` — async, без `@with_athletes` (no per-user fan-out, просто `actor_publish_weekly_changelog.send()`). Cron: `day_of_week="sun", hour=15, timezone=settings.TIMEZONE, misfire_grace_time=7200, coalesce=True, max_retries=0`.

**Почему Sun 15:00 (не 19:30 после weekly):** 4-часовой buffer до weekly report (Sun 19:00) — успеть проверить Discussion глазами и поправить вручную если Claude выдал ересь, до того как weekly report уйдёт атлетам.

CLI: `python -m cli publish-changelog [--force]`. `--force` обходит idempotency для редких «второй digest за неделю»; cron всегда без force.

---

## 8. REST endpoint

`GET /api/changelog/latest` — `require_viewer` (demo тоже читает).

| Aspect | Detail |
|---|---|
| Response | `{url, title, published_at}` или 404 если Discussion'ов нет |
| Cache | 1h in-process для **обоих** 200 и 404 — fresh repo с нулём Discussion'ов иначе бил бы GitHub на каждом page load до первой публикации |
| Single-flight | `asyncio.Lock` против thundering herd на cache miss — concurrent requests делят один upstream fetch (Copilot review #335) |
| Failure | 503 с `Retry-After: 300` через `HTTPException(headers=...)` (не через `Response.headers` — FastAPI сбрасывает их при подмене body на error JSON) |
| GraphQL query | `data/github.py:LATEST_DISCUSSION_QUERY` — shared с actor'ом (idempotency lookup), single source of truth; плюс API-процесс не тянет `dramatiq`/`anthropic` ради импорта строки |
| Token leak guard | `GITHUB_TOKEN` идёт только в outbound `Authorization` header, never в response |

---

## 9. Webapp integration

### Unread-only link, dual viewport placement

Постоянная эмодзи-ссылка для атлета, который changelogs не читает = visual debt. Рендерим **только** если `cl.url !== localStorage["changelog.last_seen_url"]`. Клик → write localStorage + `setUnread(false)` → ссылка исчезает в этой же сессии.

**Halo redesign update (Phase J/M1/M2):** legacy `Sidebar.tsx` + emoji
`BottomTabs.tsx` More-menu удалены. Новые точки размещения:

| Где | Файл | Когда |
|---|---|---|
| Desktop sidebar (≥md, 240px) | `components/halo/HaloSidebar.tsx` | После `/plan` в основном nav, как было — `flatMap` injection сохранён byte-identical |
| Mobile Wellness teaser | `pages/Wellness.tsx` | Inline ink-card после `TopBar` (только сегодня, только при `unread && changelog`) — заменил мобильный More-menu, которого больше нет в Halo `HaloBottomTabs` (4-tab strip без More) |

### Singleton hook

`useChangelog.ts` — `{changelog, unread, markRead}`. Module-level singleton `_inFlight` Promise гарантирует один fetch на сессию: Sidebar и BottomTabs делят одну подписку. Без singleton'а получили бы `2× /api/changelog/latest` на page-mount + рассинхрон localStorage. Auth-gated через `useAuth().isAuthenticated` — без gate центральный `apiFetch` 401-handler редиректил бы `/login` на `/login`, ломая login flow.

Failure recovery: `_inFlight = null` сбрасывается при `.catch()`, чтобы один transient 503 не залипал хук до full-page reload (M3 fix).

### Storage key — URL, не timestamp

`localStorage["changelog.last_seen_url"]` хранит URL последнего просмотренного Discussion'а. Преимущества vs timestamp: устойчиво к смене URL Discussion'а; не зависит от часов клиента. Edge case: cleared localStorage → один раз покажется «непрочитан» актуальный changelog → атлет кликнет → запишется. Acceptable.

`setItem` обёрнут в `try/catch` — Safari private mode и переполненный quota бросают `QuotaExceededError`; глотаем (unread-state можно потерять, page не должна падать).

### i18n / types

`sidebar.whats_new` / `sidebar.unread` ru/en (для `aria-label`). `ChangelogLatest: {url, title, published_at}` (ISO).

---

## 10. Multi-tenant

Single Discussion per week — все атлеты видят одну ссылку. **НЕ** per-user. Auth для endpoint — `require_viewer`. Actor использует `GITHUB_TOKEN` от owner'а; PR list (public repo) и Discussion creation выполняются от owner'а независимо от того, кто триггернул.

### Phase 2 bilingual rollout (когда придёт первый EN athlete)

Single bilingual Discussion. Claude call с двойной инструкцией:

```
... (existing rules) ...
- Produce TWO versions in the same response: Russian first, then English.
- Separate them by the literal marker on its own line:
  <!--LANG-SEPARATOR-->
- English version follows the same rules (3-7 bullets, theme grouping, etc.).
- If both versions would be NO_USER_FACING_CHANGES, return NO_USER_FACING_CHANGES once.
```

Body wrapper становится `## 🇷🇺 Русский / <!--LANG-SEPARATOR--> / ## 🇬🇧 English`. Endpoint остаётся один; webapp при `User.language == "en"` добавляет якорь `#english` к URL (GitHub auto-генерит anchor из heading slug).

**Почему НЕ two Discussions:** ×2 Claude call + ×2 cache keys + endpoint dispatch по `?lang=` — overhead не оправдан экономией скролла.

---

## 11. Cost

~$0.04-0.06/неделю (Anthropic, sonnet-4-6). < $6/год worst case, ~$2/год realistic. GitHub API calls — 1 REST + 1 GraphQL/неделю, free tier (5000/час authenticated). Включается в `ApiUsageDaily.increment` через owner sentinel (POC показал что это owner-driven feature).

---

## 12. Edge cases

| Случай | Поведение |
|---|---|
| 0 merged PRs за неделю | Skip publish, log info |
| Все PR'ы отсеяны pre-filter'ом | Skip publish, log info |
| Claude вернул `NO_USER_FACING_CHANGES` | Skip publish, log info |
| Claude / GitHub API down | Try-except, retry on next week's run, no fallback |
| GitHub API down при createDiscussion | Try-except, log to Sentry, retry next week |
| Дубликаты PR title (same week) | Dedup, оставить newest |
| PR с пустым body | Use title only — Claude разберётся |
| >100 PRs за неделю | Top-50 по `merged_at desc` |
| Webapp: cache miss + GitHub down | 503, sidebar link скрыт |
| Cron misfired | `misfire_grace_time=7200` (2h grace), `coalesce=True` (no double-publish) |
| Manual `publish-changelog` Wed + Sun cron | Idempotency-by-week: actor дёргает `fetch_latest_discussion`; если Discussion создан **в пределах текущей недели** (`created_at ≥ now − 6d`) → `skipped_already_published`. `--force` обходит |
| Прошлонедельный Discussion vs текущий cron | Окно идемпотентности (`now − 6d`) **строго короче** периода cron (7d) → Discussion возрастом ~7d читается как «прошлая неделя», публикация **не** подавляется. Регресс при окне ≥ 7d: дайджест молча деградирует в biweekly (инцидент #338, см. §14) |
| `fetch_latest_discussion` упал (transient GraphQL 5xx) | Best-effort guard — log warning, продолжаем публикацию. Худший случай: дубль за неделю (поправляется руками через `gh`) |
| Discussion создан с дубликатом title | GitHub разрешает дубли — не блокируем; следующая неделя перезапишет в кэше |

---

## 13. Pending / open issues

- **Per-PR feedback loop.** Discussion comments не уведомляют subscribers — настроить email-notification в repo settings (vanilla GitHub feature, не требует кода).
- **Опциональные группировки.** Сейчас prompt просит группировать по темам — Claude выбирает свободно. После 3-4 публикаций возможно дать список разрешённых emoji-секций для consistency (🎯/🧪/📊/💪/🔌/🐛/🌐/🤖).
- **PR description quality.** Spec предполагает осмысленные descriptions; PR merged с empty body / `"misc fixes"` → Claude получит мусор. Опции: (a) обязать в `CONTRIBUTING.md` или PR template, (б) принять качественную деградацию.
- **Dedup across weeks.** Если PR смержен в субботу одной недели и в воскресенье попадает в обе раундировки — используем exclusive bound `merged:>last_sunday AND <=this_sunday`.

---

## 14. Decisions log

| Date | § | Decision | Why |
|---|---|---|---|
| 2026-05-09 | §1 | Pull-модель (Discussion + sidebar), не push в Telegram | Notification fatigue, morning/evening/weekly уже есть |
| 2026-05-09 | §3 | Источник = merged PRs, не git log | PR titles+bodies написаны для людей |
| 2026-05-09 | §6 | GitHub Discussion (Announcements), не Issue/Release/Markdown | Семантически правильное место + RSS + comments |
| 2026-05-09 | §5 | sonnet-4-6 (не Opus) для prompt | Задача простая (формат + фильтр), sonnet справится дешевле |
| 2026-05-09 | §10 | Single shared Discussion per week, не per-user | Per-user requires PR↔user-story attribution — отдельная задача |
| 2026-05-09 | §9 | Sidebar link, не inline-render Discussion | Inline требует GitHub Markdown→HTML на фронте — overkill |
| 2026-05-09 | §7 | Cron Sun 15:00 (не 19:30 после weekly) | Buffer 4h до weekly report — успеть глазами проверить Discussion |
| 2026-05-09 | §10 | Phase 1 = Russian-only, Phase 2 = bilingual single Discussion (не two-Discussions) | Two-Discussions добавляет ×2 cost + dispatch ради экономии скролла |
| 2026-05-09 | POC | Discussion #334 создан вручную через `gh api graphql` | Проверили flow end-to-end до написания spec'и |
| 2026-05-10 | §4 | Hard-drop regex: только `chore\|ci\|build\|test\|docs`, без `perf\|style\|refactor` | `perf:` user-facing; `style:` чаще про UI; `refactor:` иногда меняет UX. Trust Claude'у (+$0.02/неделю) |
| 2026-05-10 | §4 | Dedup ключ: `(title, sha1(body[:200])[:8])` вместо title-only | Stacked PRs могут иметь одинаковый title но разные bodies; POC observation #318/#320 |
| 2026-05-10 | §5 | Body truncation: 500 → 1500 chars + `"... [truncated]"` | Наши PR bodies 800-1500 chars; 500 резали именно «How to verify» |
| 2026-05-10 | §9 | Sidebar link — unread-only через localStorage, не permanent | Постоянная эмодзи-ссылка для not-readers = visual debt |
| 2026-05-10 | §12 | Weekly idempotency: actor смотрит latest Discussion; ≤ 7d 12h → skip | Manual `publish-changelog` Wed не должен ломать Sun cron |
| 2026-05-10 | §8 | Endpoint кэширует и 200, и 404 на 1h | Fresh repo с нулём Discussion'ов иначе бил бы GitHub на каждом page load |
| 2026-05-10 | §8 | `Retry-After: 300` отдаётся через `HTTPException(headers=...)`, не через `Response` | FastAPI сбрасывает `response.headers` при подмене body на error JSON |
| 2026-05-10 | §6 | Env defaults opt-in (пустые в `config.py`, prod values в `.env.example`) | Fork с `GITHUB_TOKEN` иначе случайно лез бы в upstream Announcements (Copilot review #335) |
| 2026-05-10 | §8 | `asyncio.Lock` single-flight вокруг GraphQL refresh | Под нагрузкой N parallel page-load'ов били GitHub N раз вместо 1 (Copilot review #335) |
| 2026-05-10 | §8 | `LATEST_DISCUSSION_QUERY` вынесен в `data/github.py` | Single source of truth между actor (idempotency) и endpoint; API-процесс не тянет `dramatiq`/`anthropic` ради импорта строки (Copilot review #335) |
| 2026-05-10 | §9 | Dual placement: Sidebar + BottomTabs More-menu, обе после `/plan` | Mobile-first атлеты живут в bottom-tabs; sidebar-only был desktop-bias и невидим в Telegram Mini App |
| 2026-05-10 | §9 | `useChangelog` singleton hook | Sidebar и BottomTabs читают changelog → без singleton'а двойной fetch + рассинхрон localStorage; `_inFlight` Promise + reset на `.catch()` (M3) для retry после transient 503 |
| 2026-05-19 | §9 | Halo-port: `Sidebar` → `HaloSidebar` (desktop), `BottomTabs` More-menu → Wellness inline teaser (mobile) | Halo `HaloBottomTabs` — 4-tab strip без More-меню (F1/F16 IA decision); мобильная changelog-ссылка переехала в inline teaser на Wellness home. Singleton hook + `flatMap` injection после `/plan` сохранены byte-identical |
| 2026-05-17 | §3/§12 | Idempotency window `7d 12h` → `week_start` (`now − 6d`) | **Инцидент:** `7d 12h` шире 7d-периода cron → каждый Sun ловил прошлый Sun Discussion (~7d) как «уже было» и скипал → дайджест де-факто biweekly. #338 создан Sun 07:06Z подавил следующий Sun 13:00Z (7d6h < 7d12h). Окно должно быть строго < периода cron; `now − 6d` даёт ~1 сутки запаса над джиттером и ловит внутринедельный ручной run. Тесты-регрессии: `test_consecutive_weekly_run_not_suppressed`, `test_idempotency_window_is_one_day_short_of_cron_period` |
