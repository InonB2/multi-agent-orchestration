# Multi-Agent Orchestration Framework

> Route every task to the right AI model. Checkpoint mid-task state. Resume after rate limits.

## The Problem

Most teams use one AI model for everything. That's expensive, slow, and wrong in three ways:

1. **Wrong model, wrong task** — Claude is expensive for isolated code generation. Codex burns context on complex reasoning. Antigravity gets lost without a long document to synthesize. Each model has a real strength; using the wrong one wastes tokens and produces worse output.

2. **Rate limit walls** — Any model can hit its limit mid-task and freeze with no recovery path. Work is lost or must restart from scratch.

3. **No shared state** — Three models can't share a context window. Without a canonical handoff format, task context is lost at every model boundary.

4. **No clear "done"** — Tasks need explicit success criteria, QA gates, and status tracking. Without this, "done" means "I think it works."

This framework solves all four.

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

# Mark complete
python scripts/coordinator.py complete --task TASK-001 --result-path output/task_001_result.md

# Check if a task has a pre-task spec
python scripts/task_spec.py validate --task TASK-001

# Show config for an agent
python scripts/agent_config.py show --agent codex
```

## Scripts

| Script | Purpose |
|--------|---------|
| `task_router.py` | Routes tasks to claude-code, codex, or antigravity using 8 capability-based rules |
| `checkpoint.py` | Saves mid-task state; appends to resume queue; marks resumed |
| `coordinator.py` | Task lifecycle: claim → update → checkpoint → complete |
| `task_spec.py` | Enforces pre-task specs for M/L/XL tasks before execution |
| `agent_config.py` | Loads per-agent TOML config with project-level deep-merge overrides |

## Task Schema

Tasks live in `tasks/active_tasks.json`. Each task looks like this:

```json
{
  "task_id": "TASK-001",
  "title": "Add POST /items route with Zod validation",
  "priority": "high",
  "assigned_to": "coder-agent",
  "tested_by": "qa-agent",
  "status": "backlog",
  "preferred_provider": "codex",
  "complexity": "S",
  "notes": ""
}
```

Status flow: `backlog → in_progress → tested → done` (or `blocked` on rate limit).

See `tasks/active_tasks.example.json` for a complete example.

## Routing Rules

Tasks are routed using keyword heuristics informed by benchmark-backed capability research — see [docs/model_capability_table.md](docs/model_capability_table.md) for the full routing rationale.

The 8 rules (in priority order):

1. Browser / E2E / scraping → **Antigravity**
2. Long-context research / synthesis → **Antigravity**
3. Architecture / complex reasoning / writing → **Claude**
4. Single-file isolated scripts at S/M complexity → **Codex**
5. Fast parallel execution (>5 independent tasks) → **Antigravity**
6. Economy routing (cost-sensitive, simple) → **Antigravity**
7. SWE-bench-style multi-file refactors → **Claude**
8. Default → **Claude**

See `docs/model_capability_table.md` for the full benchmark-backed capability table.

## Rate Limit Policy

**Never refuse a task because rate limits are low.** The right model takes the task regardless of remaining quota. Rate limits are a scheduling problem, not a routing problem.

When a model hits its limit mid-task:
1. Write a checkpoint with `checkpoint.py save`
2. The task is queued in `tasks/queue/resume_queue.json`
3. At the next session start, read the queue and re-dispatch

## Why not LangGraph / CrewAI / AutoGen?

Those are excellent frameworks — but they solve a different problem.

| Framework | Strength | Gap |
|-----------|----------|-----|
| LangGraph | Stateful graph workflows | No multi-model routing by capability |
| CrewAI | Role-based agent teams | No rate-limit checkpoint-resume |
| AutoGen | Conversational multi-agent | Assumes one LLM provider |
| Semantic Kernel | Enterprise .NET/Python | Complex setup, no routing table |

This framework is for the developer who **runs multiple AI providers daily** (Claude Code + Codex CLI + Gemini) and needs:
1. Tasks routed to the right model by actual benchmark strength
2. Work saved and resumed automatically when a model hits its rate limit
3. Zero new infrastructure — just Python scripts and JSON files

If you need graph-based workflows or conversational agents, use LangGraph or CrewAI. If you need multi-model routing with rate-limit resilience, this is for you.

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
