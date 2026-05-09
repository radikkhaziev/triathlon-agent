# Endurai — Paperclip Company Package

Markdown package describing the **Endurai** AI development company. Contains the company definition, one CTO agent (company-wide), and one project (`triathlon-agent`) with two project-locked agents. Conformant to the [Agent Companies specification](https://agentcompanies.io/specification) (`agentcompanies/v1`).

This package creates a fresh Endurai company. Recommended path: wipe any existing Endurai data via `paperclipai company delete <id>` first, then import this package as a new company.

## Org chart

```
Radik (board user)
   │
   ▼
CTO (company-wide, reportsTo: null)
   │
   ▼ owns project
   │
   └── Triathlon Agent (project)
         ├── triathlon-engineer  (reportsTo: cto, project-locked)
         └── triathlon-tech-lead (reportsTo: cto, project-locked)
```

| Agent | Title | Reports to | Adapter | Skills | Trigger |
|---|---|---|---|---|---|
| `cto` | Chief Technology Officer | — (board) | `claude_local` | `triathlon-dev`, `spec`, `github-workflow` | Heartbeat + board messages |
| `triathlon-engineer` | Software Engineer — triathlon-agent | `cto` | `claude_local` | `triathlon-dev`, `spec`, `pr-review-chain`, `github-workflow` | Event: CTO delegation; reactive: tech-lead handoff after Copilot review |
| `triathlon-tech-lead` | Tech Lead — triathlon-agent | `cto` | `claude_local` | `triathlon-dev`, `pr-review-chain`, `github-workflow` | Heartbeat: every 30 min; webhook: GitHub `Pull request reviews` event (when configured) |

Read-only review subagents (`code-reviewer`, `security-reviewer`, `migration-reviewer`, `architecture-advisor`, `spec-curator`, `unit-test-writer`) are NOT registered as paperclip employees. They live in `.claude/agents/` and are invoked from inside `triathlon-engineer`'s worktree session via Claude Code's `Agent` tool.

## Workflow

```
Radik (board) ──message/issue──▶ CTO (intake — single channel from user)
                                    │
                                    ▼ delegates Triathlon-Agent task
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
          triathlon-engineer              triathlon-tech-lead
       spawns worktree, does           polls PRs via gh, drives
       implementation + invokes        review chain through Copilot
       .claude/agents/ subagents       timing, writes timing markers,
       inside worktree                 tags Radik when ready
                     │                             │
                     └────── feat-PR in dev ───────┘
                                    │
                                    ▼
                Radik (final review, manual squash merge)
                                    │
                                    ▼
        Manual release: Radik opens dev → main PR himself,
        squash merges → existing .github/workflows/deploy.yml fires
```

Full architectural rationale: [`docs/PAPERCLIP_SETUP_SPEC.md`](../docs/PAPERCLIP_SETUP_SPEC.md) at repo root.

## Prerequisites on the Paperclip host

- **`gh` CLI installed and authenticated** on the host where paperclip runs. Both CTO and `triathlon-tech-lead` call `gh pr view / edit / comment` on every heartbeat from their orchestration sessions (not just inside worktrees). If `gh` is missing, those agents silently break on first heartbeat.
- **Node.js 20+, paperclip configured**, `claude` CLI in `$PATH` (used by `claude_local` adapter).

## Getting Started — wipe + fresh import

```bash
# 1. List existing companies on the paperclip host
npx paperclipai company list

# 2. If an Endurai company already exists, delete it (destructive — exports first if needed)
npx paperclipai company delete <endurai-id>
# or by prefix:
# npx paperclipai company delete endurai

# 3. Import this package as a new company
npx paperclipai company import \
  https://github.com/radikkhaziev/triathlon-agent/tree/dev/.paperclip \
  --target new \
  --new-company-name Endurai \
  --ref dev \
  --yes
```

Or from a local checkout on the server:

```bash
cd ~/triathlon-agent && git pull origin dev
npx paperclipai company import ./.paperclip \
  --target new --new-company-name Endurai --yes
```

CLI auth: `paperclipai company *` commands require board access. If the server is headless, tunnel `:3100` (`ssh -L 3100:localhost:3100 paperclip@<server>`) and approve the printed CLI auth URL in your local browser, or pass `--api-key <board-token>` directly.

## Setup after import

1. **Verify org chart in UI** — should show CTO at top, Triathlon Agent project under it, two project-locked agents (`triathlon-engineer`, `triathlon-tech-lead`) with project-lock badges.
2. **Set `GH_TOKEN` secret** for all three agents (Agent → Inputs → Env). All three required (CTO for triage CLI, engineer for worktree-PR creation, tech-lead for PR lifecycle ops).
3. **Set heartbeat cadence** in UI (see "Heartbeat cadence" below — values not in `.paperclip.yaml`, lost on re-import).
4. **Enable agents.**

## Heartbeat cadence (UI-only, document any changes here)

The Agent Companies spec doesn't carry a standard per-agent heartbeat field, and `.paperclip.yaml` in this package doesn't pin one. Cadence lives **in the Paperclip UI** and is **lost on re-import** unless you record it. Current values:

| Agent | Cadence | Why |
|---|---|---|
| `cto` | Every 30 min | Frequent enough to chase the human-review queue across projects; cheap enough not to burn budget on no-op heartbeats |
| `triathlon-engineer` | Event-driven only (no fixed interval) | Wakes when CTO delegates a task or `triathlon-tech-lead` hands back a PR after Copilot review |
| `triathlon-tech-lead` | Every 30 min | Polls all open project PRs; webhook triggers (Phase 3) drop latency to seconds for Copilot reviews |

If you change values in the UI, update this table in the same commit so re-import after a wipe doesn't drift the contract.

## Skills

Per `agentcompanies/v1` §8, paperclip's importer tries to resolve `skills:` shortnames against `skills/<shortname>/SKILL.md` inside the package — and **this package intentionally vendors nothing**. Skills load through Claude Code's native `.claude/skills/` cwd-scan: every session (paperclip-orchestration sessions for CTO and tech-lead, worktree sessions spawned by the engineer) runs inside a checkout of this repo, so `.claude/skills/{triathlon-dev,spec,pr-review-chain,github-workflow}` is found and loaded by Claude Code itself.

Net effect: the `skills:` frontmatter is **documentation of intent**; actual loading is Claude Code's job, not paperclip's. Same logic for `.claude/agents/` reviewers — they resolve only inside sessions whose cwd contains `.claude/agents/` (i.e., worktree sessions invoked by the engineer).

## Directory layout

```
.paperclip/
├── COMPANY.md                                  ← package root, schema=agentcompanies/v1
├── agents/
│   ├── cto/AGENTS.md                           ← company-wide CTO (the only top-level agent)
│   ├── triathlon-engineer/AGENTS.md            ← project-locked engineer
│   └── triathlon-tech-lead/AGENTS.md           ← project-locked PR orchestrator
├── projects/
│   └── triathlon-agent/PROJECT.md              ← Triathlon Agent project, owner: cto
├── .paperclip.yaml                             ← Paperclip vendor extension (adapter + env + project lock)
├── README.md                                   ← this file
└── LICENSE
```

## References

- [`docs/PAPERCLIP_SETUP_SPEC.md`](../docs/PAPERCLIP_SETUP_SPEC.md) — full architecture, git-flow, review chain, phase plan
- [Agent Companies specification](https://agentcompanies.io/specification) — base format
- [Paperclip](https://github.com/paperclipai/paperclip) — orchestrator runtime
- [`CLAUDE.md`](../CLAUDE.md) — project context for the *product* (not this package)
