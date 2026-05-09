---
name: Triathlon Agent
description: Development of the triathlon-agent codebase — multi-tenant Ironman 70.3 coaching AI platform
slug: triathlon-agent
schema: agentcompanies/v1
owner: cto
---

This project covers all development work on the `radikkhaziev/triathlon-agent` repository. The product itself is described in `CLAUDE.md` and `docs/`; this project package describes the **team that develops it** and the workflow they follow.

## Workflow (within Endurai company)

```
Radik (board) ──message/issue──▶ CTO (Endurai company-wide intake)
                                    │
                                    ▼ delegates Triathlon-Agent task
                       ┌────────────┴────────────┐
                       ▼                         ▼
            triathlon-engineer          triathlon-tech-lead
       (project-locked, this project)  (project-locked, this project)
       spawns worktree, does           polls PRs via gh, drives
       implementation, runs            review chain through
       .claude/agents/ subagents       Copilot timing, tags Radik
       from inside worktree            via @-mention when ready
                       │                         │
                       └──── feat-PR in dev ─────┘
                                    │
                                    ▼
                Radik (final review, manual squash merge)
                                    │
                                    ▼
        Manual release: Radik opens dev → main PR himself,
        squash merges → existing .github/workflows/deploy.yml fires
```

## Owner & reporting

- `owner: cto` — strategic ownership of the project lives at the company-level CTO.
- Both project-locked agents (`triathlon-engineer`, `triathlon-tech-lead`) `reportsTo: cto` and have `project: triathlon-agent` set in `.paperclip.yaml`.

## References

- Architecture, git-flow, PR review chain, Phase plan: `../../docs/PAPERCLIP_SETUP_SPEC.md` (relative from this file) → `docs/PAPERCLIP_SETUP_SPEC.md` at repo root.
- Project context for the *product*: `CLAUDE.md`, `docs/OPERATIONS.md`.
- Agent skills (loaded by Claude Code at runtime): `triathlon-dev`, `spec`, `pr-review-chain`, `github-workflow`.
