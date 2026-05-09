---
name: Endurai
description: Personal AI development company — currently runs the triathlon-agent project; other projects (Endurai Shorts, etc.) are added separately later
slug: endurai
schema: agentcompanies/v1
version: 0.1.0
license: MIT
authors:
  - name: Radik Khaziev
goals:
  - Develop and maintain the triathlon-agent project (multi-tenant Ironman 70.3 coaching AI)
  - Add other product projects under the same company as needs arise (Endurai Shorts, etc.)
requirements:
  secrets:
    - GH_TOKEN
---

The Endurai company is Radik's personal AI dev shop. It contains one or more projects, each developed by project-locked agents reporting to a single company-wide CTO.

## Org chart

```
Radik (board user, sole owner)
   │
   ▼
CTO (company-wide, reportsTo: null)
   │
   ▼ owns projects
   │
   ├── Project: Triathlon Agent
   │     ├── triathlon-engineer  (reportsTo: cto, project: triathlon-agent)
   │     └── triathlon-tech-lead (reportsTo: cto, project: triathlon-agent)
   │
   └── (future projects added separately)
```

No CEO role — CTO does both intake (from Radik) and tech leadership directly. Simplicity over ceremony for solo ops.

## Workflow pattern

Pipeline with manual release gate:

```
Radik ──message/issue──▶ CTO (intake, triage, risk-zone check, delegation)
                            │
                            ▼ delegates Triathlon-Agent task
                  ┌─────────┴─────────┐
                  ▼                   ▼
       triathlon-engineer    triathlon-tech-lead
   (worktree implementation,  (gh CLI lifecycle,
    runs .claude/agents/        Copilot timing,
    subagents inside session)   tags Radik when ready)
                  │                   │
                  └── feat-PR in dev ─┘
                            │
                            ▼
                Radik (final review, manual squash merge)
                            │
                            ▼
       Manual release: Radik opens dev → main PR himself,
       merges → existing .github/workflows/deploy.yml fires
```

## Boundaries

- **No CEO** — intake responsibility folded into CTO.
- **No Release Manager** — release to `main` is fully manual, owned by Radik.
- **Read-only review subagents** (`code-reviewer`, `security-reviewer`, `migration-reviewer`, `architecture-advisor`, `spec-curator`, `unit-test-writer`) are NOT registered as paperclip employees. They live in `.claude/agents/` and are invoked from inside `triathlon-engineer`'s worktree session via Claude Code's `Agent` tool.
- **Other projects** (Endurai Shorts, Onboarding, Personal) are not in this package — they are added separately through paperclip UI or separate package imports later.

## References

- Architecture, git-flow, PR review chain, Phase plan: `docs/PAPERCLIP_SETUP_SPEC.md`
- Project context for the *product*: `CLAUDE.md`, `docs/OPERATIONS.md`
- Agent skills (loaded by Claude Code at runtime via `.claude/skills/` cwd-scan): `triathlon-dev`, `spec`, `pr-review-chain`, `github-workflow`

Generated with [Paperclip company-creator](https://github.com/paperclipai/paperclip) conventions; conforms to the [Agent Companies specification](https://agentcompanies.io/specification).
