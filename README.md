# Triathlon AI Agent

A personal AI assistant for triathlon training. It connects to your fitness data, monitors your recovery, evaluates your training plan, and tells you each morning whether to push hard or take it easy.

## What It Does

Every morning you get a message in Telegram with a simple traffic light: green (full load), yellow (be careful), or red (rest day). Behind that signal is a system that pulls together your heart rate variability, resting heart rate, sleep quality, training load, and recent workout history to figure out how ready your body is to train.

### Daily Recovery Assessment

The agent tracks two independent HRV algorithms simultaneously. It watches for trends -- not just today's number, but how it compares to your 7-day and 60-day baselines. Same for resting heart rate (where higher means worse, unlike HRV). It combines everything into a recovery score from 0 to 100, then translates that into a plain recommendation: which heart rate zones are safe today, and for how long.

### Training Plan Awareness

If you have workouts scheduled in Intervals.icu, the agent sees them. It checks whether today's planned session matches your recovery state. If you're supposed to do threshold intervals but your HRV has been declining for three days, it'll say so. If nothing is planned but you're feeling great, it suggests what to do.

### Workout Adaptation

When your recovery doesn't match the plan, the system doesn't just warn you -- it adapts. Planned workouts can be automatically adjusted: intensity capped, duration shortened, or the session replaced entirely based on your current state. These adapted workouts are pushed directly to your Intervals.icu calendar.

### Post-Activity Analysis

After each workout, the system processes the results. It looks at which heart rate or power zone you actually spent the most time in, checks your aerobic decoupling, and logs the outcome. The next morning, it can tell you how yesterday's session affected your recovery -- building a personal database of how your body responds to different training loads at different recovery levels.

### Race Goal Tracking

The agent knows your target event (date, distance, required fitness). It tracks per-sport training load (swim/bike/run separately) against your goals and flags if you're falling behind in any discipline.

## How You Interact With It

**Telegram Bot** -- your daily touchpoint. Morning reports arrive automatically. You can ask questions in free text and the AI responds with context from all your data. There's also a `/stick` command for tracking daily habits.

**Telegram Mini App** -- a mobile dashboard inside Telegram. Shows today's status, weekly training plan, activity history, detailed workout analytics with zone breakdowns, and a dashboard with training load charts and goal progress.

**Desktop Web** -- the same dashboard accessible from a browser, authenticated via a one-time code from the bot.

**MCP Server** -- for AI assistants like Claude. Exposes 29 tools covering wellness, HRV, training load, recovery, workouts, activities, mood tracking, and more. This is how Claude in desktop mode can query your training data directly.

## The AI Layer

Two AI models work together:

**Claude** handles the daily morning analysis. Using tool-use, it pulls exactly the data it needs -- recovery score, HRV trends, scheduled workouts, recent activities -- and produces a concise assessment in Russian. It also powers free-form chat: you can ask anything about your training and it will query the relevant data before answering.

**Gemini** (planned) will run weekly pattern analysis. With enough training history, it will find personal patterns like "after Zone 2 rides when recovery is above 70, you bounce back in one day -- but Zone 3 runs below recovery 60 take two days to recover from." These patterns feed back into Claude's daily recommendations.

## Exercise Library

The agent includes a visual exercise library with animated stick-figure cards for warm-up routines, strength work, and stretching. Workouts can be composed from these cards and pushed to Intervals.icu with the correct sport type.

## Mood Tracking

Claude silently tracks emotional state during conversations -- energy, mood, anxiety, and social connection on a 1-5 scale. Over time, this builds a dataset that correlates emotional patterns with HRV, sleep, and training data. No manual input required; the AI picks up signals from natural conversation.

## Data Flow

```
Intervals.icu  ──sync──>  PostgreSQL  ──analyze──>  AI (Claude / Gemini)
                               |                          |
                               v                          v
                     Telegram Mini App             Telegram Bot
                     (dashboard, charts)        (morning reports, chat)
                               |
                               v
                       MCP Server (29 tools)
                  (for AI assistants like Claude Desktop)
```

All fitness data originates from Intervals.icu, which aggregates from Garmin, Strava, or direct uploads. The agent syncs wellness data every 10 minutes, workouts hourly, and activities at the half-hour mark. Everything is stored locally in PostgreSQL -- no third-party analytics platforms.

## Project Status

The core system is fully operational: daily syncing, dual-HRV analysis, recovery scoring, morning reports, workout adaptation, training log with compliance detection, post-activity zone analysis, Telegram bot with AI chat, web dashboard, and MCP server.

Currently waiting on data accumulation (~30 days of training log entries) to enable Gemini weekly pattern analysis. Next planned feature: efficiency tracking (EF trends for bike/run, SWOLF for swimming) to visualize aerobic fitness progress over time.

## Tech Stack

Python 3.12, FastAPI, PostgreSQL, React + TypeScript (Vite), python-telegram-bot, Anthropic Claude API, Google Gemini API, Docker Compose.
