---
name: Triathlon Agent
description: Development company for the triathlon-agent project — a personal Ironman 70.3 coaching AI agent
slug: triathlon-agent
schema: agentcompanies/v1
version: 0.1.0
license: MIT
authors:
  - name: Radik Khaziev
goals:
  - Ship features for triathlon-agent reliably and on time
  - Maintain multi-tenant security invariants (per docs/MULTI_TENANT_SECURITY_SPEC.md)
  - Keep CTL/ATL/TSB and HRV calculations deterministic and tested
  - Preserve spec corpus integrity in docs/ as the source of truth
requirements:
  secrets:
    - GH_TOKEN
---

The Triathlon Agent company develops and maintains the `triathlon-agent` project — a multi-tenant AI coaching platform for Ironman 70.3 athletes. The product itself is described in `CLAUDE.md` and `docs/`; this company describes the **team** that develops it.

## Workflow pattern

Pipeline. Work flows from idea → planning → implementation in worktree → review chain → merge to dev. Release to `main` is **fully manual**, owned by the board user.

```
Radik (board)
  → CEO (intake, prioritization)
  → Tech Lead (decomposition, worktree, /spec gate)
  → Claude Code in worktree (main session, executes work, calls existing reviewer agents)
  → Reviewer chain (code-reviewer, migration-reviewer, security-reviewer, then Copilot)
  → Radik (final human review, merge feat-PR into dev)

Release path (no paperclip involvement):
  → Radik decides timing → opens release-PR `dev → main` himself → merges
  → push to main triggers existing .github/workflows/deploy.yml → containers redeploy
```

The CEO and Tech Lead are paperclip-orchestration agents (running on heartbeats and event triggers). The actual implementation happens inside `worktree:make` instances where Claude Code is the executor — that session leverages the existing `.claude/agents/` (code-reviewer, security-reviewer, migration-reviewer, spec-curator, architecture-advisor, unit-test-writer) and `.claude/skills/` (triathlon-dev, spec, github-workflow, pr-review-chain) directly from the repo.

## Boundaries

- The company does **not** describe how triathlon-agent itself runs (Telegram bot, MCP server, Dramatiq workers) — that is the *product*, see `CLAUDE.md`.
- The company **only** describes the development team for triathlon-agent. The user's video-rendering project lives in its own paperclip company.
- Read-only reviewer agents (`code-reviewer`, `security-reviewer`, etc.) are **not** registered as paperclip employees. They are subagents invoked from inside the main worktree session via Claude Code's `Agent` tool. Bringing them up to paperclip's company level would duplicate the abstraction.

## References

- Architecture & operations: `docs/PAPERCLIP_SETUP_SPEC.md`
- Project context: `CLAUDE.md`, `docs/OPERATIONS.md`
- Existing skills referenced by agents below: `.claude/skills/{triathlon-dev,spec,github-workflow,pr-review-chain}/SKILL.md`

Generated with [Paperclip company-creator](https://github.com/paperclipai/paperclip) conventions; conforms to the [Agent Companies specification](https://agentcompanies.io/specification).
