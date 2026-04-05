# AI Recommendation — Morning Report

> Implementation: `ai/claude_agent.py`, `ai/gemini_agent.py`, `ai/prompts.py`

## When It Runs

- **Only for current date** — `run_ai=True` passed by scheduler when `dt == date.today()`
- **NOT during backfill** — backfill calls `save_wellness` with `run_ai=False` (default)
- Called at step 5 of recovery pipeline, after HRV/RHR/recovery are computed
- Result persisted to `wellness.ai_recommendation`, returned via `/api/report`
- Skipped if `ai_recommendation` is already set (idempotent)

## Data Contract — What Claude Receives

The `MORNING_REPORT_PROMPT` template in `ai/prompts.py` assembles:

| Block | Fields | Source |
|---|---|---|
| **Recovery** | `recovery_score`, `recovery_category`, `recovery_recommendation` | `WellnessRow` |
| **Sleep** | `sleep_score`, `sleep_duration` | `WellnessRow` |
| **HRV** | `hrv_today`, `hrv_7d`, `hrv_delta%`, both algorithm statuses, `cv_7d`, `swc_verdict` | `WellnessRow` + `HrvAnalysisRow` (both) |
| **RHR** | `rhr_today`, `rhr_30d`, `rhr_delta`, `rhr_status` | `RhrAnalysisRow` |
| **Training Load** | `ctl`, `atl`, `tsb`, `ramp_rate` | `WellnessRow` (from Intervals.icu) |
| **Per-Sport CTL** | `ctl_swim`, `ctl_bike`, `ctl_run` + targets from settings | `WellnessRow.sport_info` JSON → `_extract_sport_ctl()` |
| **Race Goal** | `goal_event`, `weeks_remaining`, `goal_pct`, `swim/bike/run_pct` | Calculated from settings + current CTL |
| **Planned Workouts** | `planned_workouts` (formatted text: type, name, duration, description with intervals) | `ScheduledWorkoutRow` for today |
| **Yesterday DFA** | `yesterday_dfa_summary` (Ra, Da, HRVT1, quality per activity) | `ActivityHrvRow` + `ActivityRow` for yesterday |

## System Prompt — Persona & Rules

Defined in `SYSTEM_PROMPT` (`ai/prompts.py`). Key constraints:

1. Persona: personal AI triathlon coach
2. Athlete profile: age (`ATHLETE_AGE`), target race (`GOAL_EVENT_NAME`)
3. Be specific — numbers, zones, durations
4. HRV >15% below baseline → reduce intensity
5. TSB < −25 → rest/recovery day
6. Max 250 words, language: Russian

## Expected Output — 4 Sections

```
1. Оценка готовности (🟢/🟡/🔴) + обоснование с цифрами
2. Оценка запланированной тренировки — подходит ли она текущему состоянию? Если нет — корректировка. Если тренировок нет — предложение своей.
3. Наблюдение о тренде нагрузки (CTL/ATL/TSB/ramp rate)
4. Заметка о прогрессе к цели
```

## Morning Report Format (bot/formatter.py)

Template structure (data from `WellnessRow`):

```
{emoji} {category_text}
Readiness: {score}/100
Rec: {recommendation_text}
Sleep: {sleep_score}/100
```

**Display mappings:**
- Categories: excellent→"ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ", good→"ГОТОВ К НАГРУЗКЕ", moderate→"УМЕРЕННАЯ НАГРУЗКА", low→"РЕКОМЕНДОВАН ОТДЫХ"
- Recommendations: zone2_ok→"тренировка Z2 — полный объём", zone1_long→"только аэробная база, Z1-Z2", zone1_short→"лёгкая активность, 30-45 мин", skip→"отдых — не тренироваться"

## Claude Implementation

- Model: `claude-sonnet-4-6`, max_tokens=1024
- Single API call per day to minimize costs
- On failure: logs exception, `ai_recommendation` stays `None`
- Prompt receives pre-interpreted deltas, not raw HRV bounds

## Gemini Second Opinion (optional)

Enabled when `GOOGLE_AI_API_KEY` is set in `.env`. Disabled otherwise — no Gemini code runs, no tab in webapp.

**Installation:** `google-genai` is an optional dependency — `pip install .[gemini]` or `poetry install -E gemini`. Not required for core functionality.

**Architecture:**
- Module: `ai/gemini_agent.py` — optional import of `google-genai` with `_HAS_GENAI` flag; `is_gemini_enabled()` checks both import and API key
- Prompt: `MORNING_REPORT_PROMPT_GEMINI` — dedicated template with stricter Markdown formatting (`##` headers, `---` separators), emphasis on interpreting data relationships rather than listing numbers, and explicit analysis instructions per section
- Prompt building: shared `build_morning_prompt(template=MORNING_REPORT_PROMPT_GEMINI)` from `claude_agent.py`
- Model: `gemini-2.5-flash`, max_output_tokens=8192, thinking_config with 4096 budget
- Streaming: `generate_content_stream` with chunk accumulation; detects `MAX_TOKENS` truncation
- Retry: 2 attempts with 5s backoff delay; raises on exhaustion (no silent fallback)
- Both AI calls run in parallel via `asyncio.gather(return_exceptions=True)` during `save_wellness(run_ai=True)`
- Each call is independent — if one fails, the other still saves
- Result persisted to `wellness.ai_recommendation_gemini`
- Skipped if `ai_recommendation_gemini` is already set (idempotent)

**Display rules:**
- **Telegram morning report**: only Claude recommendation (no change)
- **Webapp pages** (`report.html`, `wellness.html`): two tabs — Claude | Gemini (Gemini tab hidden if `ai_recommendation_gemini` is `null`)
- **`/api/report`** and **`/api/wellness-day`**: return both `ai_recommendation` and `ai_recommendation_gemini` (latter is `null` if disabled)
- **MCP**: `get_recovery` returns both fields
