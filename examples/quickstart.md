# Quickstart Guide

This guide walks through the complete workflow: create tasks, route them to providers,
write a spec, save a mid-task checkpoint, and list resumable checkpoints.

## Prerequisites

- Python 3.8+ (stdlib only — no pip installs required, except `tomli` on Python < 3.11)
- Clone the repo and `cd` into it

```bash
git clone https://github.com/InonB2/multi-agent-orchestration.git
cd multi-agent-orchestration
```

---

## Step 1 — Create a sample task file

Copy the example task list into place:

```bash
cp examples/sample_active_tasks.json tasks/active_tasks.json
```

Or create `tasks/active_tasks.json` manually with this structure:

```json
{
  "last_updated": "2026-01-01",
  "tasks": [
    {
      "task_id": "TASK-001",
      "title": "Implement user authentication module",
      "assigned_to": "codex",
      "status": "pending",
      "priority": "high",
      "complexity": "M"
    }
  ]
}
```

---

## Step 2 — Route tasks to the best provider

The router scores each task's title against keyword lists for `codex`, `antigravity`,
and `claude-code`, then assigns `preferred_provider`.

**Preview (dry run — no changes written):**

```bash
python scripts/task_router.py --dry-run
```

**Apply routing to all unrouted tasks:**

```bash
python scripts/task_router.py
```

**Route a single task by ID:**

```bash
python scripts/task_router.py --task-id TASK-001
```

Expected output:

```
  TASK-001: 'Implement user authentication module' -> codex  (scores: codex=1, ...)
Routed 1 tasks: 1 -> codex, 0 -> antigravity, 0 -> claude-code
[OK] Updated tasks/active_tasks.json
```

---

## Step 3 — Create a spec for a task

Specs are required for M/L/XL tasks before work begins. They capture what is done,
what remains, the exact next step, and acceptance criteria.

**Interactive (prompts you for each field):**

```bash
python scripts/task_spec.py create --task TASK-001
```

**Non-interactive (all fields via flags):**

```bash
python scripts/task_spec.py create \
  --task TASK-001 \
  --done "Repo scaffolded, DB schema designed" \
  --remaining "JWT middleware, OAuth integration, tests" \
  --next "Add POST /auth/login route with bcrypt password check" \
  --criteria "All auth tests pass, flake8 clean, JWT expiry enforced"
```

**Read the spec back:**

```bash
python scripts/task_spec.py read --task TASK-001
```

**Validate completeness:**

```bash
python scripts/task_spec.py validate --task TASK-001
```

**List M/L/XL tasks still missing a spec:**

```bash
python scripts/task_spec.py list-missing
```

---

## Step 4 — Save a mid-task checkpoint

If work is interrupted (rate limit hit, end of session), save a checkpoint so the
next session can resume exactly where you left off.

```bash
python scripts/checkpoint.py save \
  --task TASK-001 \
  --done "POST /auth/login route complete, JWT signing working" \
  --remaining "OAuth integration (Google), integration tests" \
  --next "Wire up Google OAuth callback at /auth/google/callback" \
  --interrupted-by rate-limit
```

**Read the checkpoint:**

```bash
python scripts/checkpoint.py read --task TASK-001
```

---

## Step 5 — List resumable checkpoints

At the start of a new session, check what tasks are queued for resume:

```bash
python scripts/checkpoint.py list-resumable
```

Expected output:

```
Resumable tasks (1):

  [TASK-001]  model=codex  interrupted_by=rate-limit  queued_at=2026-01-01T12:00:00Z
         checkpoint: tasks/snapshots/TASK-001_checkpoint.json
```

**After resuming a task, remove it from the queue:**

```bash
python scripts/checkpoint.py mark-resumed --task TASK-001
```

---

## Step 6 — Inspect agent configs

```bash
# List all configured agents
python scripts/agent_config.py list-agents

# Show full merged config for an agent
python scripts/agent_config.py show --agent codex

# Get a single config value
python scripts/agent_config.py get --agent codex --key agent.max_task_size
```

---

## Running the tests

```bash
python -m pytest tests/ -v
```

All tests run with Python stdlib only (plus `pytest`). No external services required.
