@AGENTS.md

# Claude Code specifics

## Subagents (`.claude/agents/`)
- `planner` (Opus, high): phase plans, task breakdown, eval methodology. Read-only.
- `architect` (Opus, medium): system/schema/API design, trade-offs, diagnosis when `coder` is stuck. Read-only.
- `coder` (Sonnet, medium): Python/Go implementation, tests, debugging, bench/eval scripts, phase reports.
- `frontend` (Haiku, medium): React/Tailwind UI work in `web/`.
Follow the escalation rule in AGENTS.md before switching a task to a bigger model.

## Skills and plugins to use
- `frontend-design` skill: load before building or restyling UI.
- `context7`: current docs for FastAPI, pgvector, fastembed, coder/websocket, DeepSeek API. Needs auth via `/mcp`.
- `pyright-lsp`, `gopls-lsp`: type errors and go-to-definition instead of grepping.
- `/code-review` at the end of each phase, before the report; `/security-review` before Phase 5 ships (prompt injection, secrets).
- Built-in browser pane to verify UI flows (diner order → kitchen board) instead of asking the user to check.

## Working agreements
- Track phase checklists with `work_state_write` (claude-mem), one list per phase.
- Ask before: adding dependencies outside the stack in `docs/PLAN.md`, running paid eval batches, committing.
- Concise chat output; detail goes in the phase report.
