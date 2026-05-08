# Paperclip Setup — Architecture & Workflow Spec

> **Цель:** описать, как paperclip-инстанс на сервере встраивается в уже-существующую дисциплину `.claude/` репо `triathlon-agent` (агенты, skills, hooks). Документ покрывает только то, чего сейчас в репо нет: git-флоу, PR-флоу, review chain, оркестрационный слой paperclip.
>
> **Что НЕ входит в этот документ:** правила разработки (см. `.claude/skills/triathlon-dev/SKILL.md`), spec-driven workflow внутри сессии (см. `.claude/skills/spec/SKILL.md`), закрытие issues (см. `.claude/skills/github-workflow/SKILL.md`), описание архитектуры проекта (см. `CLAUDE.md`).
>
> **Версия:** v2 — 2026-05-08. Изменения через PR.

---

## 1. Контекст

Paperclip — оркестратор, который:
- получает задачи от тебя через CEO-роль;
- декомпозирует их через Tech Lead-роль;
- запускает Claude Code в изолированном **git worktree** на отдельной ветке;
- собирает результаты, считает стоимость, ведёт аудит.

Worktree получает рабочую копию репо целиком — значит видит `.claude/agents/`, `.claude/skills/`, `.claude/hooks/`, `CLAUDE.md`, `docs/*_SPEC.md`, `docs/knowledge/`. Никакого дополнительного bootstrap'а контекста делать не надо: Claude Code в worktree поднимается с тем же набором, что и локально.

**Не-цели:**
- Не дублируем правила, уже описанные в `.claude/skills/triathlon-dev/SKILL.md` и `CLAUDE.md`.
- Не пытаемся вынести исполнение в author-агентов. В paperclip исполнитель — Claude Code в worktree; агенты в `.claude/agents/` остаются read-only structured-output (reviewer / advisor / curator).
- Не делаем paperclip полностью автономным. Human-in-the-loop остаётся на этапе spec OK (`/spec` skill), code review и merge.

---

## 2. Оркестрационный слой paperclip

| Роль | Назначение | Где живёт |
|---|---|---|
| **CEO** | Канал общения с тобой; принимает свободно-формулированные задачи; чейзит human-review queue. | `.paperclip/agents/ceo/AGENTS.md` |
| **Tech Lead** | Декомпозиция, открытие worktree, прогон review chain, cleanup. | `.paperclip/agents/tech-lead/AGENTS.md` |

Релиз `dev → main` — **не агентская роль**. Ты сам решаешь когда мерджить, открываешь release-PR одной командой `gh pr create --base main --head dev` (или из UI GitHub'а), мерджишь после своего review. Push в `main` триггерит существующий `.github/workflows/deploy.yml` без участия paperclip'а.

Внутри worktree эти роли невидимы — Claude Code просто получает задачу и инструкции от Tech Lead'а как initial prompt.

---

## 3. Существующие агенты в `.claude/agents/`

Все read-only, выдают structured punch-list. Модель и tool-set указаны в YAML frontmatter каждого агента (не в этом документе).

| Агент | Когда триггерится | Что проверяет |
|---|---|---|
| `code-reviewer` | После любого значимого изменения, перед commit/PR | Correctness, security, performance, style; шкала 🔴 Critical / 🟡 Warning / 🔵 Suggestion |
| `security-reviewer` | Diff трогает `api/`, `mcp_server/`, `bot/`, `data/`, миграции, OAuth, webhooks | Multi-tenant T1-T19 из `MULTI_TENANT_SECURITY_SPEC.md` |
| `migration-reviewer` | Появилась новая миграция в `migrations/versions/` | Revision id, round-trip, FK semantics, ORM-sync |
| `architecture-advisor` | Архитектурные обсуждения, рефакторинг | Simplicity / reliability / practicality фильтры |
| `spec-curator` | Spec corpus hygiene, knowledge currency, decisions index | 5 режимов работы со спеками и `docs/knowledge/` |
| `unit-test-writer` | После значимого кода без тестов | Тестовое покрытие, AAA структура |

Этот документ ничего к этим агентам не добавляет. Их расширяют через PR в их `.md` файлы; такие PR проходят review как обычный код.

---

## 4. Git flow

### 4.1 Базовая ветка — `dev`

Все ветки делаются от `dev`. Не от `main`. PR — в `dev`. Никаких автоматических релизов. Когда ты решаешь, что накопленное в `dev` пора в прод — открываешь release-PR `dev → main` сам (`gh pr create --base main --head dev` или через UI), смотришь diff, мерджишь.

`.github/workflows/deploy.yml` существует и триггерится на `push` в `main` (self-hosted runner → docker compose). **Этот workflow не меняется.** Меняется только привычка: feat-PR мержатся в `dev` без деплоя; релизный путь = ты вручную открываешь и мерджишь `dev → main` → push в `main` срабатывает существующий deploy. `triathlon-dev/SKILL.md` сейчас пишет «Deploy: push to main → GitHub Actions» — формально остаётся верным, но в Phase 0 нужно дополнить строкой про базовую ветку для feat-PR.

### 4.2 Naming веток

| Префикс | Что |
|---|---|
| `feat/<slug>` | Новая фича |
| `fix/<slug>` | Баг |
| `spec/<slug>` | Только `docs/<FEATURE>_SPEC.md` (см. §5) |
| `refactor/<slug>` | Рефакторинг без изменения поведения |
| `chore/<slug>` | Зависимости, конфиги |
| `docs/<slug>` | Только документация |
| `hotfix/<slug>` | От `main`, экстренные фиксы прод-инцидентов |

`<slug>` — kebab-case, желательно с номером issue (`feat/142-rampup-protocol`).

### 4.3 PR lifecycle

```
agent открывает draft PR в dev
     │
     ▼
CI зелёный (pytest, ruff, mypy, webapp build)
     │
     ▼
конвертация в Ready
     │
     ▼
main session invokes code-reviewer (всегда)
                  → migration-reviewer (если diff трогает migrations/versions/)
                  → security-reviewer (если diff трогает зоны §5)
     │
     ▼ замечания обработаны
     │
     ▼
gh pr edit <N> --add-reviewer copilot-pull-request-reviewer
     │
     ▼
Copilot review → обработка через skill pr-review-chain §6
     │
     ▼
@-mention тебя: "ready for human review"
     │
     ▼
ты → approve → squash merge → branch auto-delete
     │
     ▼
merge в dev: деплой не триггерится (deploy.yml слушает только main)
релиз: отдельный PR dev → main → squash merge → push в main → existing deploy.yml едет
```

### 4.4 Branch protection

Настроить в GitHub после Phase 0:

`dev`:
- Запрет force-push.
- Required status checks: `pytest`, `ruff`, `mypy`, `webapp-build`.
- Required reviewers: 1 (ты).
- Squash merge only, linear history.
- Auto-delete head branches.

`main`:
- То же + запрет прямых push.
- Merge только из `dev` через release-PR, который ты сам открываешь и сам мерджишь. Никаких автоматических release-PR.

### 4.5 PR template (`.github/pull_request_template.md`)

```markdown
## Spec
<!-- "Spec: docs/<FEATURE>_SPEC.md" если фича в spec-зонах риска (см. PAPERCLIP_SETUP_SPEC §5).
     Иначе "Spec: N/A — <причина>" -->

## Summary
<!-- 2-3 предложения: что и зачем -->

## Changes
- [ ] DB schema (migration)
- [ ] API endpoints
- [ ] MCP tools
- [ ] Frontend
- [ ] Tests
- [ ] i18n (RU + EN)
- [ ] `IMPLEMENTATION_STATUS.md` обновлён

## Risk / Rollout
<!-- feature flag? backfill? downtime? -->

## How to verify
<!-- См. формат в .claude/skills/github-workflow/SKILL.md -->
```

### 4.6 Hotfix

Прод-инцидент — исключение:
- Ветка `hotfix/<slug>` от `main`.
- Минимальный фикс + тест.
- code-reviewer обязателен; Copilot можно пропустить, если инцидент критичный (engineer объясняет это в PR description).
- После merge в `main` — отдельный backport PR в `dev`.

---

## 5. Spec-first — триггеры по зонам риска

`/spec` skill уже описывает дисциплину **внутри сессии** (audit → punch-list → gate). Этот раздел дополняет его на уровне **PR-флоу**: когда фича требует отдельного `spec/<slug>` PR с docs-only содержимым, мерженого до `feat/<slug>` PR.

Spec-PR обязателен, если фича попадает в одну из зон:

| Зона риска | Примеры |
|---|---|
| DB schema | новая таблица, ADD/DROP COLUMN, изменение FK, backfill |
| Public API | новый endpoint, breaking change на существующем, изменение DTO |
| Multi-tenant scope | любая работа с `user_id` scoping, новые tenant-границы |
| Security / auth | OAuth scopes, токены, шифрование, JWT, prompt-injection guardrails |
| Webhook contract | новый dispatcher, изменение event handler, signature verification |
| Cross-MCP контракт | новый MCP tool, изменение существующего DTO |

Размер фичи и время не учитываются — они искусственные триггеры. Маленький фикс в зоне риска требует spec-PR; большой рефакторинг вне зон не требует.

Spec-PR содержит только `docs/<FEATURE>_SPEC.md` и опц. обновление `IMPLEMENTATION_STATUS.md` со статусом `Planned`. Reviewer'ы — `spec-curator` (структура, согласованность с corpus) + `architecture-advisor` (если есть архитектурные решения) + ты. После merge spec'и в `dev` открывается feat-PR со ссылкой `Spec: docs/<FEATURE>_SPEC.md`.

Шаблон спеки и режимы работы со спеками — в `.claude/agents/spec-curator.md` и `.claude/skills/spec/SKILL.md`. Не дублирую.

---

## 6. Skill `pr-review-chain` (новый)

`.claude/skills/github-workflow/` сейчас покрывает только закрытие issues. Открытие PR, chain reviewer'ов и обработка Copilot feedback — не покрыты. Phase 0 добавляет отдельный skill `pr-review-chain` (имя выбрано так, чтобы не путалось с established `github-workflow`).

**Frontmatter:**
```yaml
---
name: pr-review-chain
description: |
  Open a PR against dev, drive the review chain (our reviewers → Copilot → human), handle Copilot feedback.
  Triggers: "open PR", "create PR", "request review", "Copilot left comments", "ready for review".
  Stops at "ready for human review" — never auto-merges.
---
```

**Workflow внутри skill'а (summary):**

1. Pre-PR: branch from `dev`, CI зелёный локально, PR description по шаблону §4.5, `Spec:` поле заполнено.
2. Draft PR через `gh pr create --draft --base dev`.
3. После зелёного CI — `gh pr ready <N>`.
4. Main session invokes reviewer'ов в порядке: `code-reviewer` всегда; `migration-reviewer` если diff трогает `migrations/versions/`; `security-reviewer` если diff трогает зоны §5. (Это вызовы Agent tool изнутри сессии Claude Code, не CI hook'и — никакого GitHub-app для этого нет.)
5. Обработка замечаний reviewer'ов: для каждого Critical/Warning — fix или явное обоснование почему false positive (с file:line).
6. Когда наши reviewer'ы happy: `gh pr edit <N> --add-reviewer copilot-pull-request-reviewer`.
7. Обработка Copilot feedback: каждый thread категоризируется (bug / style / suggestion / false-positive), для каждого — fix или ответ с обоснованием. Никаких пустых "thanks".
8. Когда оба контура чистые: `gh pr comment <N> --body "@<your-handle> ready for human review"`. STOP. Не пытаться merge.

Когда engineer и Copilot противоречат — engineer описывает противоречие в комментарии, тегает тебя. Никакого автоматического "победителя".

---

## 7. Guardrails и эскалация

Помимо `MULTI_TENANT_SECURITY_SPEC.md` и `CLAUDE.md`:

| Правило | Кто проверяет |
|---|---|
| Никаких секретов в коде/логах | code-reviewer + security-reviewer |
| `git push --force` на `dev` или `main` | branch protection (запрещено) |
| Drop таблицы / `DELETE FROM` без `WHERE` | требует human approval — engineer задаёт вопрос в PR |
| Редактирование прошедших миграций | migration-reviewer (Critical) |
| MCP tool принимает `user_id` параметром | code-reviewer (Critical, см. `triathlon-dev/SKILL.md`) |

**Эскалация.** Engineer, наткнувшийся на запрещённое действие, не пытается обойти. Он оставляет комментарий в PR с тегом `@<your-handle>` и ждёт решения.

---

## 8. Phase plan

**Phase 0 — Foundation (полдня):**
- [ ] PR template `.github/pull_request_template.md` (§4.5).
- [ ] Branch protection для `dev` и `main` (§4.4).
- [ ] Skill `.claude/skills/pr-review-chain/SKILL.md` (§6).
- [ ] Решение про деплой зафиксировано: `.github/workflows/deploy.yml` не меняем; релизный путь = PR `dev → main` → existing workflow триггерится на push в `main`.
- [ ] Дополнить `triathlon-dev/SKILL.md` строкой «PR base branch: `dev`. Push в `main` только через release-PR».
- [ ] Этот документ влит в `dev`.

**Phase 1 — Paperclip company import (≈час):**

Пакет компании уже описан в репо: `.paperclip/COMPANY.md` + `.paperclip/agents/{ceo,tech-lead}/AGENTS.md` + `.paperclip/.paperclip.yaml`. Формат — Agent Companies spec (`agentcompanies/v1`), импортируется через `paperclipai company import`. Release Manager-агента нет — релизы вручную (см. §4.1).

- [ ] (Опц.) Почистить дефолтную компанию, если paperclip создал её при `onboard` и она не нужна:
  ```bash
  npx paperclipai company list                 # увидеть id
  npx paperclipai company delete <selector>    # удалить по id или shortname
  ```
  Видеорендер-компания на этом сервере — отдельная, не трогаем.
- [ ] Импорт пакета. Headless server, поэтому через SSH-туннель сначала открываем CLI auth:
  ```bash
  # Локально на ноуте
  ssh -L 3100:localhost:3100 paperclip@<server>
  # На сервере в новой сессии
  npx paperclipai company list                 # триггерит auth, печатает URL
  # Открываем напечатанный http://localhost:3100/cli-auth/... в локальном браузере, аппрувим.
  ```
  Альтернатива без туннеля — создать board API key в UI и пробрасывать через `--api-key`.
- [ ] Применить компанию из git-репо:
  ```bash
  npx paperclipai company import \
    https://github.com/radikkhaziev/triathlon-agent/tree/dev/.paperclip \
    --target new \
    --new-company-name "Triathlon Agent" \
    --ref dev \
    --yes
  ```
  Или из локального чекаута на сервере, если он там есть:
  ```bash
  cd ~/triathlon-agent && git pull origin dev
  npx paperclipai company import ./.paperclip \
    --target new --new-company-name "Triathlon Agent" --yes
  ```
- [ ] Прописать секрет `GH_TOKEN` для **обоих агентов** в Paperclip UI (Agent → Inputs → Env) — required и для `ceo` (gh issue/pr CLI), и для `tech-lead` (PR-флоу).
- [ ] Настроить heartbeat cadence в UI (нет в `.paperclip.yaml`, теряется на re-import — см. README): CEO 30 мин, Tech Lead event-driven only. Если меняешь — обнови таблицу в `.paperclip/README.md` в том же коммите.
- [ ] Включить heartbeats обоих агентов.
- [ ] Bootstrap для worktree (выполнится автоматически при первом `worktree:make`, но проверить хотя бы раз руками):
  ```bash
  npx paperclipai worktree:make pilot-test --start-point dev
  cd ~/paperclip-pilot-test
  poetry install
  cd webapp && npm install   # lockfile = package-lock.json, не pnpm
  alembic upgrade head       # только если нужен dev DB
  ```
  После проверки — `npx paperclipai worktree:cleanup pilot-test --force`.

**Phase 2 — Pilot:**
- [ ] Выбрать одну XS-S фичу (не в зоне риска).
- [ ] Прогнать полный цикл: feat-PR → reviewer chain → Copilot → ты → merge в `dev`.
- [ ] Записать боли в этот документ или в `pr-review-chain/SKILL.md`.

**Phase 3 — Scale:**
- [ ] Параллелить 2-3 фичи в worktree'ах одновременно.
- [ ] Пилот фичи в зоне риска: spec-PR → feat-PR.
- [ ] Релизы остаются ручными — частоту определяешь сам по факту накопленного в `dev`.

**Phase 4 — Tuning:**
- [ ] Раз в месяц review этого документа.
- [ ] Метрики: средняя длина PR, среднее число review-циклов, доля PR с нарушением spec-first.

---

## 9. FAQ

**Почему `code-reviewer` → Copilot, а не наоборот?**
Наш reviewer видит spec, инварианты `CLAUDE.md` и `MULTI_TENANT_SECURITY_SPEC.md`. Он отсекает категориальные ошибки (нарушение архитектуры, забытый i18n, нарушенный multi-tenant контракт). Copilot видит только diff — он лучше ловит локальные баги. Грязный diff в Copilot — пустая трата сессий и шум, который наш reviewer всё равно отметит.

**Что если engineer и Copilot противоречат?**
Engineer описывает противоречие в комментарии PR, тегает тебя. Финальное слово за тобой.

**Один агент пишет и сам же ревьюит?**
Нет. Engineer (главная сессия Claude Code в worktree) и `code-reviewer` (subagent) — это разные сессии и разные `.md`. Code-review своего собственного кода в той же сессии запрещён.

**Spec-PR замедляет работу. Можно «как-нибудь по-быстрому»?**
Нет. Зоны риска (§5) выбраны так, чтобы цена ошибки оправдывала отдельный review-цикл. Вне зон spec-PR не нужен.

**Почему релизы ручные, а не автоматические?**
В соло-режиме автоматический релизный гейт ничего не добавляет к review chain. Если PR прошёл `code-reviewer` → Copilot → твой approve — он по определению готов к проду. Второй раз смотреть его в release-PR через несколько дней — лишняя церемония. Ты сам решаешь когда `dev` стабилен и пора в `main`. Открываешь release-PR одной командой `gh pr create --base main --head dev`, мерджишь. Push в `main` триггерит существующий `deploy.yml`. Конец.

---

## Changelog

- **2026-05-08 (v1):** первая версия. Slim изначально не получилась — заложил 12 author-агентов и Phase 0 на «заполнить весь `.claude/`». Не учитывала уже-существующую инфраструктуру.
- **2026-05-08 (v2):** переписано после ревью. Author-агенты убраны (исполнитель = Claude Code в worktree). Documented существующих 6 read-only агентов и 3 skills вместо изобретения новых. Spec-first порог переведён с размера на зоны риска. Phase 0 сокращена до полдня (PR template + branch protection + один skill). Модели — per-agent через YAML frontmatter (как уже работает), без глобального `settings.json`.
- **2026-05-08 (v2.4):** правки после третьего прохода ревью пакета. (a) `GH_TOKEN` для CEO теперь `required` — у него в обязанностях `gh issue/pr` CLI, optional не имел смысла. (b) Tech Lead больше не «invokes reviewer agents» — переписан на orchestration-only роль: спавнит worktree session с initial prompt, мониторит через `gh pr view --json`, добавляет Copilot, тегает Радика. Реальные `Agent` tool вызовы reviewer'ов делает worktree session, потому что только в её cwd видны `.claude/agents/*.md`. Это снимает противоречие с §6 спеки. (c) Cost watch (мониторинг 80% бюджета) выкинут из CEO — paperclip сам это делает (auto-pause на 100%). (d) В README добавлены явные секции про heartbeat cadence (источник правды — paperclip UI, не `.paperclip.yaml`; теряется на re-import) и про skills resolution (frontmatter `skills:` декоративен — реально skills грузит Claude Code из `.claude/skills/` cwd-сканом).
- **2026-05-08 (v2.3):** Release Manager выкинут целиком. В соло-режиме автоматический релизный гейт ничего не добавляет к review chain (PR прошёл reviewer → Copilot → approve = готов к проду по определению). Релизы `dev → main` теперь fully manual: ты сам решаешь когда, открываешь PR одной командой `gh pr create --base main --head dev`, мерджишь, существующий `deploy.yml` едет. Удалён `release-manager` агент из `.paperclip/agents/`, удалены `routines:` и его блок из `.paperclip.yaml`, обновлены §2 (только 2 paperclip-агента), §4.1, §4.4, Phase 1, Phase 3, FAQ. Это вариант B из обсуждения — все ветки от `dev`, все PR в `dev`, ручной промоушен в `main`.
- **2026-05-08 (v2.2):** Phase 1 переписана с конкретными командами `paperclipai company import` после прочтения paperclip docs (`docs/companies/companies-spec.md`, `.agents/skills/company-creator/`). Добавлена `.paperclip/` папка в репо — markdown company package по Agent Companies spec (`agentcompanies/v1`), три агента (CEO / Tech Lead / Release Manager), все на `claude_local`, weekly cron на `0 19 * * 0` Europe/Belgrade. Read-only reviewer-агенты (`code-reviewer`, `security-reviewer`, и т.д.) намеренно НЕ зарегистрированы в paperclip — остаются subagent'ами в `.claude/agents/`, вызываются изнутри worktree-сессии.
- **2026-05-08 (v2.1):** правки после второго прохода ревью. (a) Skill переименован `pr-workflow` → `pr-review-chain` чтобы не путался с established `github-workflow`. (b) §6 step 4 переформулирован: reviewer'ов вызывает main session через Agent tool, не CI. (c) §3 убрана строка про `model: opus` — модели per-agent в YAML frontmatter, не предмет этой спеки. (d) §4.1 и §4.3 уточнены: `deploy.yml` существует и не меняется, в `main` едут только релиз-PR из `dev`. (e) Phase 0 чеклист дополнен явным пунктом про deploy-decision и lockfile webapp (`package-lock.json`, не pnpm). (f) Исправлен self-contradicting `T1-T9 / T1-T13 / T1-T19` в `.claude/agents/security-reviewer.md` — теперь везде `T1-T19` (соответствует реальному `MULTI_TENANT_SECURITY_SPEC.md`).
