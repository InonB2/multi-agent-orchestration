# Multi-Agent Orchestration Framework

> Route every task to the right AI model. Checkpoint mid-task state. Resume after rate limits.

[![CI](https://github.com/InonB2/multi-agent-orchestration/actions/workflows/ci.yml/badge.svg)](https://github.com/InonB2/multi-agent-orchestration/actions/workflows/ci.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](scripts/)

## The Problem

Most teams use one AI model for everything. That's expensive, slow, and wrong in three ways:

1. **Wrong model, wrong task** — Claude is expensive for isolated code generation. Codex burns context on complex reasoning. Antigravity gets lost without a long document to synthesize. Each model has a real strength; using the wrong one wastes tokens and produces worse output.

2. **Rate limit walls** — Any model can hit its limit mid-task and freeze with no recovery path. Work is lost or must restart from scratch.

3. **No shared state** — Three models can't share a context window. Without a canonical handoff format, task context is lost at every model boundary.

4. **No clear "done"** — Tasks need explicit success criteria, QA gates, and status tracking. Without this, "done" means "I think it works."

This framework solves all four.

## Requirements

- Python 3.8+ (no external dependencies — stdlib only; Python <3.11 requires `pip install tomli`)
- For API providers: set the relevant env var (e.g., `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)

## Installation

```bash
git clone https://github.com/InonB2/multi-agent-orchestration.git
cd multi-agent-orchestration
# No pip install needed — pure Python stdlib
```

## Architecture

```
Project → Orchestrator (Andy / your orchestrator)
         ├─ Decomposes project into tasks
         ├─ Estimates complexity (S/M/L/XL)
         ├─ Routes each task to the best model via capability table
         └─ Writes pre-task spec (what/remaining/next step/criteria)
                │
                ▼
         Per-model Coordinator
         ├─ Model claims task
         ├─ Executes with mid-task checkpoints
         ├─ On rate limit: writes checkpoint → queues resume
         └─ On completion: QA gate → return to orchestrator
```

## Quick Start

```bash
# Route all unrouted tasks in active_tasks.json
python scripts/task_router.py

# Preview routing without writing
python scripts/task_router.py --dry-run

# Save a mid-task checkpoint
python scripts/checkpoint.py save \
  --task TASK-001 \
  --done "Completed schema migration" \
  --remaining "API routes + types" \
  --next "Write POST /items route with Zod validation"

# See what's queued to resume
python scripts/checkpoint.py list-resumable

# Claim a task as a model
python scripts/coordinator.py claim --task TASK-001 --model claude-code

# Mark tested (QA sign-off), then mark done (final)
python scripts/coordinator.py mark-tested --task TASK-001 --result-path output/task_001_result.md
python scripts/coordinator.py mark-done --task TASK-001

# Check if a task has a pre-task spec
python scripts/task_spec.py validate --task TASK-001

# Show config for an agent
python scripts/agent_config.py show --agent codex
```

## Scripts

| Script | Purpose |
|--------|---------|
| `task_router.py` | Routes tasks to claude-code, codex, or antigravity via keyword-heuristic scoring across three providers |
| `checkpoint.py` | Saves mid-task state; appends to resume queue; marks resumed |
| `coordinator.py` | Task lifecycle: claim → update → checkpoint → mark-tested → mark-done |
| `task_spec.py` | Enforces pre-task specs for M/L/XL tasks before execution |
| `agent_config.py` | Loads per-agent TOML config with project-level deep-merge overrides |
| `llm_provider.py` | LLM-agnostic provider abstraction — CLI tools or direct API endpoints |

## Task Schema

Each task in `active_tasks.json` follows this structure:

```json
{
  "task_id": "TASK-001",
  "title": "Implement user authentication module",
  "assigned_to": "codex",
  "status": "pending",
  "priority": "high",
  "complexity": "M",
  "preferred_provider": null,
  "notes": "Optional context for the agent"
}
```

| Field | Required | Values | Description |
|-------|----------|--------|-------------|
| `task_id` | Yes | `[A-Za-z0-9_-]+` | Unique identifier |
| `title` | Yes | string | Plain-language task description — used by the router |
| `assigned_to` | Yes | agent name | Which agent owns this task |
| `status` | Yes | `backlog`, `pending`, `in_progress`, `blocked`, `tested`, `done` | Current state (`tested` = QA-signed-off, set by `coordinator.py mark-tested`) |
| `priority` | No | `high`, `medium`, `low` | Used for ordering |
| `complexity` | No | `S`, `M`, `L`, `XL` | Used to decide if a spec is required |
| `preferred_provider` | No | provider name or `null` | Set by the router; null means unrouted |
| `notes` | No | string | Free-text context |

Status flow: `backlog → in_progress → tested → done` (or `blocked` on rate limit).

See `tasks/active_tasks.example.json` for a complete example.

## LLM Provider Configuration

Each agent can be configured to use either a **CLI tool** or a **direct API endpoint** — you are not locked into any specific AI product.

### CLI mode (default)

The default. The framework routes tasks to agents who execute them via CLI tools (Claude Code, Codex CLI, etc.).

```toml
# config/agents/my_agent.toml
[provider]
type = "cli"
```

**Non-interactive CLIs (`cli_exec_args`).** Some CLIs open an interactive UI unless you pass a subcommand. The framework runs `<cli> <cli_exec_args...> "<prompt>"` and always closes stdin. The most important case is the **OpenAI Codex CLI**: running `codex "<prompt>"` opens an interactive TUI that **hangs in automation** — so `codex.toml` sets `cli_exec_args = ["exec"]` to run it headless:

```toml
# config/agents/codex.toml
[provider]
type = "cli"
cli_exec_args = ["exec"]   # -> runs `codex exec "<prompt>"` non-interactively
```

> Windows note: if Codex's built-in sandbox fails to spawn subprocesses (`windows sandbox: spawn setup refresh`), append `"--dangerously-bypass-approvals-and-sandbox"` to `cli_exec_args` for trusted repos.

### API mode — OpenAI-compatible

For any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Groq, Together AI, Ollama):

```toml
# config/agents/my_api_agent.toml
[provider]
type = "api"
api_base_url = "https://api.openai.com/v1"  # or your custom endpoint
api_key_env_var = "OPENAI_API_KEY"
model_id = "gpt-4o"
```

### API mode — Anthropic

```toml
# config/agents/anthropic_agent.toml
[provider]
type = "api"
api_base_url = "https://api.anthropic.com/v1"
api_key_env_var = "ANTHROPIC_API_KEY"
model_id = "claude-sonnet-4-6"
```

Auth format is auto-detected: `api.anthropic.com` → `x-api-key` header; all other endpoints → `Authorization: Bearer`.

### Inspecting provider config

```bash
python scripts/llm_provider.py info --agent my_agent
python scripts/llm_provider.py list  # show all agents + provider types
python scripts/llm_provider.py run --agent my_agent --prompt "Hello" --dry-run
```

See `config/agents/openai_agent.toml` and `config/agents/anthropic_api.toml` for full examples.

## Routing Rules

`task_router.py` scores each task's `title` + `notes` against three hardcoded keyword
lists — one per provider — using word-boundary matching, and assigns the highest-scoring
provider. The keyword lists capture the capability rationale documented in
[docs/model_capability_table.md](docs/model_capability_table.md), but the router itself is a
simple keyword heuristic, **not** a rules engine that evaluates conditions like complexity,
subtask count, or rate-limit state.

How a provider is chosen:

1. Each provider's keyword list is matched against the task text; each hit scores +1.
2. The highest-scoring provider wins.
3. On a tie, priority order is **codex → antigravity → claude-code**.
4. If no keyword matches (score 0 for all), the task falls back to the default provider, **claude-code**.

The three keyword lists (see `ROUTING_RULES` in `scripts/task_router.py` for the full set):

- **codex** — `implement`, `refactor`, `api`, `endpoint`, `migration`, `schema`, `fix`, `bug`, `code review`, `lint`, …
- **antigravity** — `research`, `summarize`, `analyze`, `design`, `ui`, `browser`, `e2e`, `document`, `report`, `scrape`, …
- **claude-code** — `orchestrate`, `delegate`, `architect`, `coordinate`, `multi-file`, `debug`, `subagent`, `workflow`, …

The capability table in `docs/model_capability_table.md` is the **rationale** behind these
keyword choices, not an executed rule set. To change routing, edit the keyword lists in
`scripts/task_router.py`.

## Rate Limit Policy

**Never refuse a task because rate limits are low.** The right model takes the task regardless of remaining quota. Rate limits are a scheduling problem, not a routing problem.

When a model hits its limit mid-task:
1. Write a checkpoint with `checkpoint.py save`
2. The task is queued in `tasks/queue/resume_queue.json`
3. At the next session start, read the queue and re-dispatch

## Why not LangGraph or CrewAI?

| | LangGraph | CrewAI | This framework |
|---|---|---|---|
| Multi-model routing | ❌ Single model per graph | ❌ Multi-LLM but no dynamic routing | ✅ Keyword-heuristic routing across providers |
| Rate-limit checkpointing | ⚠️ RetryPolicy (fault tolerance, not quota scheduling) | ❌ Not addressed | ✅ Quota-exhaustion treated as scheduling |
| Pre-task spec gate | ❌ | ❌ Task descriptions, no validation | ✅ Enforced for M/L/XL tasks |
| External dependencies | pip install langgraph + LangSmith | pip install crewai (standalone since v1.14) | ✅ Python stdlib only |
| CLI-native | ❌ | ❌ | ✅ Built for Claude Code, Codex CLI, etc. |
| API-native | ✅ Any LLM | ✅ Any LLM | ✅ OpenAI-compatible + Anthropic |

This framework is not competing with LangGraph for enterprise workflow orchestration. It targets the developer who runs Claude Code + Codex CLI daily and has hit rate limits mid-task. For that use case, nothing in this table solves the problem as directly.

## Agent Config

Each agent has a TOML file in `config/agents/`. `_defaults.toml` defines base values for all agents; per-agent files override specific keys via deep-merge.

```toml
# config/agents/_defaults.toml (excerpt)
[agent]
max_task_size = "M"
preferred_model = "claude-code"
qa_gate = "—"

[rate_limit]
notify_at_pct = 85
resume_queue = true
```

## Pre-Task Specs

Before starting any M, L, or XL task, write a spec:

```bash
python scripts/task_spec.py create \
  --task TASK-001 \
  --done "Schema migration applied" \
  --remaining "API routes, types, tests" \
  --next "Write POST /items route" \
  --criteria "All routes return correct status codes, Zod validates input, 100% test coverage on new routes"
```

This enforces clarity before execution and makes handoffs between models deterministic.

## License

MIT — see [LICENSE](LICENSE)
