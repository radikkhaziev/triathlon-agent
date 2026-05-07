---
name: github-workflow
description: Close GitHub issues with a structured comment (What was done, How to verify). Use after fixing a bug or completing a task linked to a GitHub issue.
---

## GitHub Issues Workflow

When working on a task that references a GitHub Issue:

### Before starting
- Read the issue, understand acceptance criteria
- Check related docs/specs linked in the issue

### After implementation
Add a closing comment to the issue before closing it. This serves as release notes — other agents and the user should understand what changed without reading the full diff.

Use this template:

```
## Done

**What was done:**
Brief summary of changes — 2-4 sentences. Mention key decisions made during implementation.

**Files changed:**
- `path/to/file.py` — what changed
- `path/to/other.py` — what changed

**Deploy / run:**
- `docker compose up -d --build`
- `docker compose run --rm api python -m bot.cli <command>`
- `alembic upgrade head` (if migration added)

**How to verify:**
- "Open the Mini App → Activities → tap any swim activity → check SWOLF value"
- "Run MCP tool `get_efficiency_trend` with sport=bike, days=90 — should return weekly EF data"
- "Send /morning to the bot — recommendation should mention zone suggestions"
- "Check `GET /api/progress?sport=swim&days=90` — response should include swolf field"

**Related:**
- Links to updated docs/specs
- Follow-up issues if any
```

Not every section is needed. Bug fixes might skip "Deploy" if it's just a code change. But **"What was done"** and **"How to verify"** are always required.

### Close the issue

```bash
gh issue close <number> --comment "$(cat <<'EOF'
## Done
...paste closing comment...
EOF
)" --repo radikkhaziev/triathlon-agent
```

Or add comment first, then close:
```bash
gh issue comment <number> --body "..." --repo radikkhaziev/triathlon-agent
gh issue close <number> --repo radikkhaziev/triathlon-agent
```
