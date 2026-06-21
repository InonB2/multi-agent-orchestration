# Team-of-Teams (ToT) + Per-Task Model & Effort (PTME)

Two additive upgrades to the orchestration framework:

- **ToT** — run tasks through a per-model *supervisor + worker pool* instead of one
  task at a time. Each worker runs in an isolated git worktree; results aggregate
  back to the orchestrator.
- **PTME** — pick the model **and** reasoning-effort level for each task from its
  capability + complexity, with a clear override precedence.

Both are pure-Python (stdlib only) and backward compatible: existing commands,
configs, and tests keep working unchanged. For where these run (local workstation
vs. headless VPS), see the
[Deployment: Local / Self-Host vs. VPS / Always-On](../README.md#deployment-local--self-host-vs-vps--always-on)
section of the README — this page does not duplicate it.

---

## 1. Per-Task Model & Effort (PTME)

### What it does

`scripts/llm_provider.py run` now resolves, per task, **which internal model slug**
and **which reasoning effort** to use, then assembles the correct CLI flags:

- **Codex**: `codex exec -m <model> -c model_reasoning_effort="<effort>" "<prompt>"`
- **Antigravity (`agy`)**: `agy --model <model> "<prompt>"` with `TERM=xterm` injected
  into the environment (prevents the headless no-TTY hang). `agy` has no effort flag —
  the model slug (Flash vs Pro) carries the tier.

### Resolution precedence (highest → lowest)

1. **CLI flags** — `--model` / `--effort`
2. **Per-task overrides** — `provider_model` / `provider_effort` in `tasks/active_tasks.json`
   (looked up by `--task-id`)
3. **Complexity mapping** — `[provider.complexity_mapping.<S|M|L|XL>]` in the agent TOML
   (resolved from `--complexity` or the task's `complexity`)
4. **Agent default** — `provider.model` / `provider.effort` in the agent TOML
5. **Legacy fallback** — bare CLI binary with no model/effort flags

Model and effort resolve **independently**, so a task can take its model from one
tier and its effort from another. The core selector is
`llm_provider.resolve_model_effort(...)`.

### Config keys (all optional / additive)

Documented in [`config/agents/_defaults.toml`](../config/agents/_defaults.toml); live
mappings in [`codex.toml`](../config/agents/codex.toml) and
[`antigravity.toml`](../config/agents/antigravity.toml):

```toml
[provider]
cli_cmd = "agy"          # override the executable name (else falls back to preferred_model)
model   = "gpt-default"  # tier-4 default model
effort  = "medium"       # tier-4 default effort

[provider.complexity_mapping.L]
model  = "gpt-5.5"
effort = "high"
```

> **`cli_cmd`** resolves the binary identically across `info`, `list`, and `run`.
> It exists because the logical label (`preferred_model = "antigravity"`) differs
> from the executable on PATH (`agy`).

### Run it

```bash
# Resolve model+effort from the task's complexity in active_tasks.json
python scripts/llm_provider.py run --agent codex --task-id TASK-001 --prompt "..."

# Force a specific tier (CLI flags win over everything)
python scripts/llm_provider.py run --agent codex --model gpt-5.5 --effort high --prompt "..."

# Ad-hoc complexity without a task file
python scripts/llm_provider.py run --agent codex --complexity L --prompt "..."

# Inspect what would run, no execution
python scripts/llm_provider.py run --agent antigravity --complexity L --prompt "..." --dry-run
```

> The task queue file (`tasks/active_tasks.json`) is not shipped in a fresh clone —
> see the README's *Task Queue Initialization* step first.

### Model + effort mapping (summary)

| Complexity | Codex model / effort | Antigravity model (effort hint) |
| :--------: | :------------------- | :------------------------------ |
| S          | `gpt-5.4-mini` / low | `gemini-3.5-flash` (low)        |
| M          | `gpt-5.4` / medium   | `gemini-3.5-flash` (medium)     |
| L          | `gpt-5.5` / high     | `gemini-3.1-pro` (high)         |
| XL         | `gpt-5.5` / xhigh    | `gemini-3.1-pro` (high)         |

Model slugs for `agy` are assumptions pending live CLI verification. Orchestration
and security audits are never routed to `agy` (charter limits).

---

## 2. Team-of-Teams (ToT)

### Architecture

```
Orchestrator (Andy)
   │  populates tasks/active_tasks.json, triggers supervisors
   ▼
model_supervisor.py  (one per model: codex, antigravity, claude-code)
   │  1. select  — tasks where preferred_provider == <model> and still claimable
   │  2. claim   — coordinator.py CAS claim (atomic, lock-guarded)
   │  3. dispatch— ThreadPool of N workers (N = model's concurrency cap)
   │  4. aggregate— per-task status + result path returned as one summary
   ▼
worker (per task)
   ├─ worktree_manager.create_worktree(task_id)   # isolated branch + dir
   ├─ llm_provider.py run --agent <model> --task-id <id>   # PTME-resolved
   ├─ worker_wrapper.write_result(task_id, output)  # owner_inbox/TASK-<id>_result.md
   └─ worktree_manager.destroy_worktree(task_id)    # cleanup
```

### Components

| File | Role |
| :--- | :--- |
| `scripts/coordinator.py` | **CAS claim guard** — claiming an already `in_progress`/`tested`/`done` task is rejected (exit 1) under the file lock. Two concurrent claims → exactly one winner. `--force` overrides. |
| `scripts/worktree_manager.py` | Create/destroy isolated git worktrees + temp branches (`worker/<task-id>`) from the current HEAD. Worktrees live in a sibling dir (`../mmoi-worktrees`, override with `MMOI_WORKTREES_DIR`) so the repo's git status stays clean. |
| `scripts/worker_wrapper.py` | Deterministic, atomic result writeback to `owner_inbox/TASK-<id>_result.md`. Path-traversal guarded. |
| `scripts/model_supervisor.py` | Select → claim → run pool → aggregate. Concurrency caps: codex 3, antigravity/agy 2, claude-code 1. Rate-limit (`429`/`quota`) triggers a pool cool-down. |
| `scripts/preflight_auth.py` | Sequential CLI auth warm-up before the parallel pool (avoids concurrent token-cache corruption). |

### Run it

```bash
# List what a model's supervisor would pick up
python scripts/model_supervisor.py select --model codex

# Plan only — select but do not claim or execute
python scripts/model_supervisor.py run --model codex --dry-run

# Warm up auth once, then run the pool (default concurrency cap for the model)
python scripts/preflight_auth.py --models codex antigravity
python scripts/model_supervisor.py run --model codex

# One full pass over all models (route → supervise) — used by cron/systemd
scripts/unattended_loop.sh                 # Linux / macOS
powershell -ExecutionPolicy Bypass -File scripts\unattended_loop.ps1   # Windows
```

### Safety properties

- **No double execution** — CAS claim under a cross-process file lock.
- **No cross-task contamination** — every worker has its own worktree (separate git
  index + working tree); spaces in paths are safe (git invoked via argv lists).
- **No result collisions** — deterministic per-task output filenames.
- **Rate-limit aware** — pool cool-down on `429`/quota markers.
- **No secrets in the repo** — scripts read env-var *names* only; the systemd unit
  and `run_agy_headless.sh` reference credentials out of band (CLI login state or a
  gitignored `EnvironmentFile`).

### VPS / always-on

The reference systemd unit + timer ([`deploy/vps/`](../deploy/vps/)) run one
supervisor pass every 5 minutes; `scripts/run_agy_headless.sh` boots a virtual
D-Bus/keyring session so `agy` does not hang headless. Install steps and the broader
local-vs-VPS trade-offs live in the README deployment section linked at the top of
this page.

---

## Tests

```bash
python -m pytest -q
```

- `tests/test_llm_provider.py` — PTME precedence (CLI > task > complexity > default >
  legacy), `cli_cmd` consistency, `TERM=xterm` injection, codex flag assembly.
- `tests/test_coordinator_cas.py` — CAS reject + concurrent-claim single-winner.
- `tests/test_worktree_manager.py` — create/destroy without index corruption.
- `tests/test_worker_wrapper.py` — deterministic atomic writeback + traversal guard.
- `tests/test_model_supervisor.py` — selection, sequential + parallel pool,
  aggregation, rate-limit cool-down.
