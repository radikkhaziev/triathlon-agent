---
name: spec
description: |
  Disciplined workflow for moving a `docs/*_SPEC.md` forward — forces audit → phased plan → confirmation gate → implementation. Triggers when the user invokes `/spec`, `/spec <path>`, or `/spec <path> phase=<N>`. Use this instead of jumping straight into implementation when the user references a spec document.

  Without arguments: list all specs with phase-status snapshot (which phases done / pending / in-progress).

  With a spec path: audit existing implementation in code → present phase plan from the spec → identify the first incomplete phase → propose its scope as a punch-list → **STOP and ask user for explicit OK before writing any code**.

  Mandatory stop point: never start implementing before the user confirms. Even if the punch-list seems obvious. The forced gate is the whole point — `/spec` exists because plain "implement WEBHOOK_DATA_CAPTURE Phase 1" loses the audit step.
---

# /spec — Spec-Driven Implementation Workflow

You are running the project's spec-driven workflow. The user invoked `/spec` because they want a disciplined fan-out: read the spec → audit code → propose first phase → wait for OK → implement. Skipping the audit and proposal steps defeats the purpose.

## When invoked without arguments

User typed just `/spec`. They want a status sweep of the spec corpus.

1. Glob `docs/*_SPEC.md` plus the one non-`*_SPEC.md` design doc (`INTERVALS_WEBHOOKS_RESEARCH.md`). Do NOT include `BUSINESS_RULES.md`, `DATABASE_SCHEMA.md`, `IMPLEMENTATION_STATUS.md`, `OPERATIONS.md`, `intervals_icu_openapi.json` (reference docs, not specs).
2. For each spec: extract Phase headings, count `[x]` vs `[ ]` checkboxes, find any "Pending" / "Deferred" / "TODO" markers.
3. Cross-reference `CLAUDE.md` "Implementation Status" paragraph (line ~69) — it's the canonical "what's done" summary.
4. Output a compact table:

   ```
   Spec                              | Phase | Status         | Pending
   --------------------------------- | ----- | -------------- | -------
   WEBHOOK_DATA_CAPTURE_SPEC.md      | 1     | done           | backfill CLI, ML cross-spec cleanup
   WEBHOOK_DATA_CAPTURE_SPEC.md      | 2     | not started    | warmup/cooldown/polarization cols
   RACE_PLAN_SPEC.md                 | 1     | branched       | merge feat/END-100 + private-beta gate
   ADAPTIVE_TRAINING_PLAN_SPEC.md    | 3     | partial        | compute_personal_patterns cron + prompt enrichment
   ...
   ```

5. Stop. Don't propose anything. The user got the snapshot — they'll pick the next spec to drive.

## When invoked with a spec path

User typed `/spec docs/RACE_PLAN_SPEC.md` (optionally `phase=2`). Run the four steps below in order. Do not skip ahead, do not summarize past the stop point.

### Step 1 — Read the spec

`Read` the file in full. Note the phases (usually a §2 Scope or §10 Acceptance criteria), data model section, dispatcher / API / DTO sections. If `phase=N` was given, focus on that phase; otherwise pick the first phase that's not entirely `[x]`.

### Step 2 — Audit existing implementation

Before proposing anything, find what already exists. Each spec describes concrete artifacts (table names, ORM classes, MCP tool names, API endpoints, DTO fields). Cross-reference them against the codebase:

- **Tables:** `grep -rn "<table_name>" data/db/ migrations/versions/` — does the table exist? Which migration introduced it?
- **ORM classes:** `grep -n "class <Name>" data/db/*.py` — Mapped columns matching the spec?
- **DTO fields:** `grep -n "<field>" data/intervals/dto.py data/dto.py` — already on the DTO?
- **MCP tools:** `grep -rn "def <tool_name>\|@mcp.tool" mcp_server/tools/` — registered?
- **API endpoints:** `grep -rn "<path>" api/routers/` — handler exists?
- **Dispatcher wiring:** look at `api/routers/intervals/webhook.py` for the relevant `_dispatch_*` function.
- **CLAUDE.md mentions:** spec features often get a paragraph in the "Implementation Status" line of `CLAUDE.md` — read it.

This step is **not optional**. The audit is what `/spec` adds over a raw "implement Phase X" — the spec might say `trimp` is needed, but the audit reveals it already exists at `data/db/activity.py:497` (real example from WEBHOOK_DATA_CAPTURE Phase 1). Without the audit, you'd write a duplicate migration.

You may delegate this audit to the `Explore` subagent if it's broad — give it a precise list of artifacts to verify, ask for "exists / missing / partial" per item.

### Step 3 — Build the phase plan

Map the spec's phases onto the project's actual state:

```
Phase 1 (MVP) — done / partial / not started
  - [x] artifact A — exists at file:line
  - [ ] artifact B — missing
  - [~] artifact C — partial (column exists but dispatcher doesn't write it)

Phase 2 — not started
  ...
```

Use `[x]` / `[ ]` / `[~]` (partial) marks. Cite file:line for everything that exists. The user reads this to decide whether your audit caught everything before they OK'd implementation.

### Step 4 — Propose the first incomplete phase as a punch-list

Pick the first phase that's not fully `[x]`. List the concrete work items needed to close its acceptance criteria:

```
## Proposed scope: WEBHOOK_DATA_CAPTURE Phase 1

1. Migration: ADD COLUMN carbs_used / rolling_ftp / ... (7 cols), CREATE TABLE activity_weather, ALTER athlete_settings (4 MMP cols)
2. DTO: ActivityDTO + 22 optional fields, MmpModelDTO new, SportSettingsDTO.mmp_model
3. ORM: ActivityWeather class + upsert_from_dto, ActivityDetail.patch (@dual + _UNSET sentinel), AthleteSettings MMP cols + upsert kwargs
4. Dispatchers: _dispatch_activity_uploaded → weather/trimp persist, _dispatch_achievements → rolling/snapshot/carbs patch, actor_sync_athlete_settings → MMP for Ride
5. Tests: ActivityDetail.patch sentinel semantics, ActivityWeather upsert round-trip, dispatcher persistence + cross-tenant guard regression
6. Docs: CLAUDE.md update, spec §10 acceptance ticks, deviations section

Skip from spec: trimp column (already exists at data/db/activity.py:497), achievements_json column (redundant with activity_achievements table from migration u1b2c3d4e5f6).
```

Include explicit **Skip from spec** items where the audit revealed redundancy or pre-existing work. This is the single most valuable output of `/spec`.

### Step 5 — STOP. Ask the user for OK.

End with exactly:

> Ready to start implementing the first phase. Go ahead? (if you want to adjust scope — say what to remove / add)

Do not write any code, do not start any TaskCreate beyond planning. Wait for explicit "yes", "go", "do it", or a corrected scope. If the user says "do another audit on X" — go back to Step 2 with that focus, do not advance past Step 5.

After the user confirms, you may begin implementation in the main conversation with full tools (Edit, Write, Bash, Agent for code-reviewer / Explore where useful). Implementation work is normal main-Claude work — `/spec`'s job ends at the gate.

## What `/spec` does NOT do

- Does not duplicate the global `Plan` agent — `Plan` is general-purpose; `/spec` adds project-specific discipline (always audit existing impl first, always cite file:line, always identify "skip from spec" items).
- Does not write code itself. Implementation runs in the main conversation after the gate.
- Does not fix the spec — if the spec is out of date with reality (e.g. claims a column doesn't exist when audit shows it does), report that as a deviation in Step 3 but don't edit the spec mid-workflow. A separate spec-update step is the user's call.
- Does not blow past Step 5 even if the punch-list is "obviously fine". The forced gate is the value.

## Output style

- Match the language of the user's last message.
- Markdown tables / bullet lists. Cite `file:line` for every existing artifact.
- Keep audit output ≤ 400 words; punch-list ≤ 200 words. If the spec is huge, say "first phase only — call `/spec docs/X.md phase=2` for the next" rather than dumping everything.
