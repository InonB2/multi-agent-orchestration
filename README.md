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

## Team-of-Teams (ToT) + Per-Task Model & Effort (PTME)

Two additive, backward-compatible upgrades on top of the core loop:

- **Per-Task Model & Effort (PTME)** — `llm_provider.py run` selects the model **and**
  reasoning-effort level per task, by a clear precedence: CLI flags → per-task
  overrides in `active_tasks.json` → complexity mapping (`S/M/L/XL`) in the agent
  TOML → agent default → legacy bare binary. Codex gets `-m <model> -c
  model_reasoning_effort="<effort>"`; `agy` gets `--model <slug>` with `TERM=xterm`.

  ```bash
  python scripts/llm_provider.py run --agent codex --task-id TASK-001 --prompt "..."
  python scripts/llm_provider.py run --agent codex --complexity L --prompt "..." --dry-run
  ```

- **Team-of-Teams (ToT)** — a per-model *supervisor + worker pool*
  ([`model_supervisor.py`](scripts/model_supervisor.py)) that selects its model's
  tasks, claims them via a CAS-guarded `coordinator.py claim` (no double execution),
  runs each worker in an isolated git worktree
  ([`worktree_manager.py`](scripts/worktree_manager.py)), writes deterministic
  results ([`worker_wrapper.py`](scripts/worker_wrapper.py)), and aggregates the
  outcome. Concurrency caps: codex 3, antigravity 2, claude-code 1.

  ```bash
  python scripts/model_supervisor.py run --model codex --dry-run
  scripts/unattended_loop.sh      # one route → supervise pass (cron/systemd-friendly)
  ```

Full guide, config keys, mapping table, and safety properties:
**[docs/tot-ptme.md](docs/tot-ptme.md)**. For where to run it (local vs. headless
VPS, with the reference `systemd` unit/timer under [`deploy/vps/`](deploy/vps/)), see
the deployment section below.

## Agent Dashboard

A zero-dependency, single-file dashboard for watching the team work in real
time: the four orchestrator cards, which specialist agents are running, the
in-flight task ledger, and the per-task model/effort each dispatch chose.

Open [`dashboard/index.html`](dashboard/index.html) directly in a browser — no
server, no build step. It reads live feeds as `window.*` globals written by the
producers in [`scripts/`](scripts) (`agent_activity.py`, `dispatch_worker.py`,
`orchestrator_stats.py`, `build_analytics.py`, `sub_orchestrator.py`,
`rate_wall_watchdog.py`, `ptme.py`, `codex_usage.py`). The shipped feeds are
seeded idle so the page renders out-of-the-box.

Setup, the dispatch → sub-orchestrator → PTME flow, the rate-wall watchdog, and
an honest **known-limitations / roadmap** (analytics aggregation, PTME
model-vs-engine scoping, and the learning loop are a hardening-in-progress
**v0/preview**) are documented in **[dashboard/README.md](dashboard/README.md)**.

## Deployment: Local / Self-Host vs. VPS / Always-On


The Multi-Agent Orchestration Framework (MMOI) orchestrates tasks across multiple AI CLI agents: **Antigravity** (`antigravity` via Gemini), **Codex** (`codex` via OpenAI), and **Claude Code** (`claude-code` via Anthropic). 

Unlike traditional frameworks designed for API-metered integration, MMOI executes tasks in **CLI mode** by default. By driving these official command-line tools, MMOI routes execution through your personal flat-rate consumer subscriptions (such as Claude Pro, Gemini Advanced, or ChatGPT Plus), avoiding expensive per-token metered API costs. 

Because task execution is coupled to authenticated CLI session states, choosing the right environment is crucial. This guide covers the two deployment options: **Local / Self-Host** on a workstation and **VPS / Always-On** headless servers.

---

### Task Queue Initialization (Required First Step)

A fresh clone of the repository does not ship with the live task queue file, [active_tasks.json](tasks/active_tasks.json). Running MMOI scripts without this file will result in errors. Before executing any tasks, initialize the task queue by copying the shipped sample file:

```bash
cp examples/sample_active_tasks.json tasks/active_tasks.json
```

Alternatively, you can manually create [active_tasks.json](tasks/active_tasks.json) following the structure outlined in [quickstart.md](examples/quickstart.md).

---

### Option A - Local / Self-Host

Running MMOI on a local development workstation is the most direct setup. Because a human desktop session exists, the underlying CLI tools can trigger interactive authentication prompts or launch browser-based OAuth redirect flows without issue.

#### Prerequisites
* **Python**: Version 3.8+ (stdlib only). Note that on Python versions below 3.11, the `tomli` package must be installed for TOML configuration parsing (see [scripts/config_loader.py](scripts/config_loader.py)).
* **AI CLIs on PATH**:
  * **Claude Code** (configured via [claude-code.toml](config/agents/claude-code.toml) as the CLI command `claude-code`)
  * **Codex CLI** (configured via [codex.toml](config/agents/codex.toml) as the CLI command `codex`)
  * **Antigravity CLI** (configured via [antigravity.toml](config/agents/antigravity.toml) as the CLI command `antigravity`)
* **Active CLI Logins**: Verify you are logged into each CLI. Note that only the `codex` CLI exposes a standard login subcommand:
  ```bash
  codex login
  ```
  For `claude-code` and `antigravity`, ensure they are authenticated according to their vendor flow (e.g. interactive authentication or API key environment variable) before running the orchestrator.

#### Workflow & Run Steps

1. **Route Queue**: Scan [active_tasks.json](tasks/active_tasks.json) and assign pending tasks. The router uses a hardcoded keyword scoring algorithm defined in [task_router.py](scripts/task_router.py) (specifically defined in `ROUTING_RULES`) to assign the `preferred_provider`. The `[task_types]` configuration blocks in individual agent TOML files are metadata-only and do not affect the router.
   ```bash
   python scripts/task_router.py
   ```
2. **Write Pre-Task Spec**: For tasks with complexity `M`, `L`, or `XL`, draft an execution plan using [task_spec.py](scripts/task_spec.py):
   ```bash
   python scripts/task_spec.py create --task TASK-001 --done "Initial setup completed" --remaining "API handlers implementation" --next "Write core controllers" --criteria "Passes local unit tests"
   ```
   *Note: Creating a spec does not validate it. To check for completeness and validate required fields, run the separate `validate` subcommand:*
   ```bash
   python scripts/task_spec.py validate --task TASK-001
   ```
3. **Claim Task**: Update the task status to `in_progress` under the assigned agent configuration using [coordinator.py](scripts/coordinator.py):
   ```bash
   python scripts/coordinator.py claim --task TASK-001 --model codex
   ```
4. **Execute CLI Run**: Invoke task execution via the [llm_provider.py](scripts/llm_provider.py) script:
   ```bash
   python scripts/llm_provider.py run --agent codex --prompt "Build the endpoint with Zod schema validation."
   ```
5. **Mid-Task Checkpoints**: If you hit rate limits or need to hand off the work, save a checkpoint using [checkpoint.py](scripts/checkpoint.py):
   ```bash
   python scripts/checkpoint.py save --task TASK-001 --done "Database models defined" --remaining "Routing controllers" --next "Implement CRUD endpoints"
   ```
   *Note: The resume queue is handled in a runtime-generated, gitignored path at `tasks/queue/resume_queue.json`. You can list resumable checkpoints using:*
   ```bash
   python scripts/checkpoint.py list-resumable
   ```
   *After resuming a task, remove it from the resume queue:*
   ```bash
   python scripts/checkpoint.py mark-resumed --task TASK-001
   ```
6. **Complete & Sign-Off**: Run tests and record the output report's metadata path in the task's notes (note: `mark-tested` does not write the report file itself, it only records the string path in the task's notes):
   ```bash
   python scripts/coordinator.py mark-tested --task TASK-001 --result-path owner_inbox/task_001_result.md
   ```
   Next, mark the task as done. Task completion is guarded: tasks must pass through `tested` status before they can be marked `done` (unless the `--force` flag is used):
   ```bash
   python scripts/coordinator.py mark-done --task TASK-001
   ```

#### When to Use
Choose this option for active pair-programming sessions during work hours, where you need immediate feedback, are making frequent edits, and are present to bypass occasional CLI interactive prompts.

#### Pros & Cons
* **Pros**:
  * **Flat subscription cost**: Leverages existing consumer flat fees (zero per-token charges).
  * **Simple OAuth**: Browser redirects and interactive MFA prompts are handled natively on your OS.
  * **Zero sync delay**: State files and workspace directories reside locally.
* **Cons**:
  * **Workstation lock-up**: Subprocesses run locally, drawing system resources and command shells.
  * **Not always-on**: Execution terminates if the workstation goes to sleep or is shut down.

---

### Option B - VPS / Always-On

Deploying MMOI to a headless server (e.g., Hostinger VPS, DigitalOcean Droplet, or AWS EC2) allows agents to run continuously. However, because a headless Linux instance lacks a web browser for OAuth redirects, authenticating the CLI agents requires manual setup.

#### Prerequisites
* **Server**: A headless Linux server (Ubuntu 22.04 LTS or similar).
* **Python**: Version 3.8+ (stdlib only; `tomli` required on Python versions < 3.11).
* **AI CLIs on PATH**: Installed in the server environment.

#### The Headless Authentication Caveat
Headless execution of interactive desktop CLIs fails when they attempt to prompt for interactive logins or launch desktop web browsers. You must configure authentication using one of two strategies:

##### Approach 1: Transplanted/Persistent Authenticated Sessions (Maintains Flat Subscription)
Copy the logged-in workstation session cookies, credentials, and caches to the server's user home directory:
* **Claude Code**: Copy the local config directory `~/.config/claude-code/` (Linux/Mac) or `%APPDATA%\claude-code\` (Windows) to `~/.config/claude-code/` on the VPS.
* **Antigravity**: Copy `~/.gemini/antigravity-cli/` or equivalent gcloud auth configurations to the server.
* **Codex**: Transplant `~/.config/openai/` or relevant credentials storage to the server.
* *Note: CLI session tokens expire periodically. When they do, re-run login locally on your workstation and copy the updated folders back to the VPS.*

##### Approach 2: Direct API Authorization (Metered Billing)
Edit the agent's TOML files inside `config/agents/` to switch the provider type from `cli` to `api`, bypassing the CLI client entirely and calling REST endpoints directly. The framework base defaults in [_defaults.toml](config/agents/_defaults.toml) define the following `[provider]` schema keys for API mode:
```toml
[provider]
type = "api"
api_base_url = "https://api.openai.com/v1"   # OpenAI-compatible base URL
api_key_env_var = "OPENAI_API_KEY"            # Env var holding the API key
model_id = "gpt-4o"                            # Model identifier
```
The repository ships with working examples of API configuration:
* [openai_agent.toml](config/agents/openai_agent.toml)
* [anthropic_api.toml](config/agents/anthropic_api.toml)

*Note: [codex.toml](config/agents/codex.toml) and [antigravity.toml](config/agents/antigravity.toml) do not contain a `[provider]` block by default and inherit `type = "cli"` from `_defaults.toml`. To configure them for API mode, add a `[provider]` block overriding these keys.*

Export the required API key in your server environment:
```bash
export OPENAI_API_KEY="sk-..."
```
Directly invoking APIs via [llm_provider.py](scripts/llm_provider.py) shifts billing from your flat consumer subscription to metered API token usage.

#### Process Supervision & Automation

> [!IMPORTANT]
> **Orchestration Execution Model**
> The [task_router.py](scripts/task_router.py) script is a **one-shot execution script**. It processes the task queue once and immediately exits; it is **not** a resident daemon/worker loop and does not perform active polling.

Because the router is one-shot, the correct way to run MMOI "always-on" is to **invoke it on a schedule** — not to launch a long-lived process and expect it to poll.

##### 1. Cron Scheduling (Recommended)
Run the one-shot router on a periodic schedule (e.g. every 5 minutes) via system cron. This is the simplest and most robust always-on pattern: each tick processes the queue once and exits cleanly.
```cron
*/5 * * * * cd /opt/multi-agent-orchestration && python scripts/task_router.py >> /var/log/mmoi_router.log 2>&1
```
A `systemd` timer (a `.timer` + `.service` pair) achieves the same scheduled-invocation model if you prefer systemd over crontab. Either way, the unit of work is a single one-shot run per tick — there is no resident daemon in the shipped repo.

##### 2. Managing Checkpoints
When tasks are interrupted by rate limits, they are queued for resume in the runtime-generated, gitignored file `tasks/queue/resume_queue.json`. Operators can periodically view resumable tasks:
```bash
python scripts/checkpoint.py list-resumable
```
And once a task is resumed, mark it as resumed:
```bash
python scripts/checkpoint.py mark-resumed --task TASK-001
```

#### State-File Syncing (VPS ↔ Workstation)
Because MMOI saves task states in local JSON files, updates [active_tasks.json](tasks/active_tasks.json), and writes checkpoints under `tasks/snapshots/`, you must sync these directories between your workstation and the VPS:
* **Mutagen**: Real-time bi-directional directory sync:
  ```bash
  mutagen sync create --name=mmoi-vps ./local_workspace user@vps_ip:/opt/multi-agent-orchestration
  ```
* **rsync Cron Job**: One-way pull to download updated task states from the VPS output folder:
  ```bash
  rsync -avz user@vps_ip:/opt/multi-agent-orchestration/tasks/ ./tasks/
  ```

#### When to Use
Ideal for complex, multi-agent pipelines with long-running research or development cycles that can run overnight without local intervention.

#### Pros & Cons
* **Pros**:
  * **Background execution**: Tasks continue processing even when your workstation is shut down.
  * **Offloaded performance**: Resource-heavy execution happens on remote compute.
* **Cons**:
  * **Fragile authentication**: Migrated CLI session files expire periodically and require manual refresh.
  * **API token costs**: Utilizing API mode to avoid authentication maintenance results in metered charges.
  * **Sync overhead**: Requires setting up folder synchronization systems.

---

### Comparison Summary

| Feature | Option A: Local / Self-Host | Option B: VPS / Always-On |
| :--- | :--- | :--- |
| **Uptime** | ❌ Workstation must remain active | ✅ 24/7 background runner |
| **Billing Type** | 💳 Flat consumer subscription | 💰 Subscription + VPS host cost (or Metered API) |
| **Token Cost** | 💵 Free (Included in CLI subscriptions) | 🔄 Subscription (with sync) OR Metered API usage |
| **Setup Complexity** | ⚡ Simple (Local commands + login) | 🛠️ Moderate (Session migration, cron/scheduled setup) |
| **Best For** | Desktop pair-programming sessions | Overnight autonomous tasks, queue-based jobs |

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
