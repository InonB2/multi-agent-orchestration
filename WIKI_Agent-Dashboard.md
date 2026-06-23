# Agent Dashboard

> Wiki draft — upload this page to the repository wiki as **Agent-Dashboard**.
> Source of truth for setup details: [`dashboard/README.md`](../blob/main/dashboard/README.md).

The Agent Dashboard is a single-file, zero-dependency view of a running MMOI
multi-engine agent team. It answers, at a glance:

- Which of the four orchestrators (Andy / Claude / Antigravity / Codex) is active?
- Which specialist agents are running right now, on which engine team?
- What tasks are in flight, and what model + effort was each dispatched with?
- How much rate-limit budget is left, where the engine actually reports it?

## Why no server / no `fetch`

The dashboard is meant to be opened straight off disk (`file://`). Browsers
block `fetch()` of local files, so instead of fetching JSON, each feed is a tiny
JavaScript file that assigns a single global. `index.html` includes them with
`<script src>` tags and renders from `window.ORCH_STATS`, `window.LIVE_TASKS`,
`window.AGENT_ACTIVITY`, and `window.MMOI_ANALYTICS`. To refresh: re-run the
producer script, then reload the page.

| Feed                      | Global                  | Producer                          |
|---------------------------|-------------------------|-----------------------------------|
| `orchestrator_stats.js`   | `window.ORCH_STATS`     | `scripts/orchestrator_stats.py`   |
| `live_tasks.js`           | `window.LIVE_TASKS`     | `scripts/dispatch_worker.py`      |
| `agent_activity.js`       | `window.AGENT_ACTIVITY` | `scripts/agent_activity.py`       |
| `analytics_data.js`       | `window.MMOI_ANALYTICS` | `scripts/build_analytics.py`      |

A `.json` twin is written next to each `.js` for non-browser tooling. Writes are
atomic and tolerate a file held open by the browser (no half-writes, no frozen
feed).

## The dispatch flow

1. **PTME** (`ptme.py`) — classify a task's complexity (S/M/L/XL) from
   deterministic signals and map it to a concrete model + effort from one
   capability table. Every choice is logged with its reasoning.
2. **Dispatch** (`dispatch_worker.py`) — `start` runs PTME, sets the agent
   running on the board, and opens a `live_tasks` row; `complete` records
   duration/usage and returns the agent to idle. Worker ids canonicalize to
   `<engine>-<role>` (e.g. `codex-qa`).
3. **Sub-orchestrator** (`sub_orchestrator.py`) — each engine team can run its
   own orchestrator: decompose a goal into 2–5 sub-tasks (rule-based), route
   each to a specialist role, run PTME per sub-task, and structurally enforce
   **worker ≠ tester** (the tester is always a different role).
4. **Rollups** — `orchestrator_stats.py` rebuilds the header cards;
   `build_analytics.py` aggregates the analytics strip.

```bash
# direct single-worker dispatch
python scripts/dispatch_worker.py start  --engine codex --role qa --task-id T-1 --text "Verify export"
python scripts/dispatch_worker.py complete --worker codex-qa --task-id T-1 --status done

# team-of-teams: let an engine plan its own sub-tasks
python scripts/sub_orchestrator.py plan --engine claude \
    --goal "Research the rate wall, design a watchdog, document the playbook" --dry-run

# refresh the board
python scripts/orchestrator_stats.py
python scripts/build_analytics.py
```

## Rate-wall watchdog

`rate_wall_watchdog.py` reads Codex's two rolling usage windows (primary 5-hour,
secondary weekly) and tells you whether it is safe to dispatch:

```bash
python scripts/rate_wall_watchdog.py check                       # %, binding window, reset time
python scripts/rate_wall_watchdog.py should-dispatch --engine codex  # exit 0 = safe
```

When a window hits 100%, in-flight jobs on that engine are **killed, not
paused** — re-dispatch fresh after the reset and rely on checkpoint/resume.
Engines without comparable local telemetry are treated as always-dispatchable
(fail-open). The Antigravity engine exposes only wall-clock duration, so its
usage% is reported as null rather than a fabricated number.

## Known limitations / Roadmap (read this)

Shipped honestly. **Solid and tested:** the UI, the script-global feed plumbing,
the dispatch path, PTME complexity classification, the sub-orchestrator
decomposition with structural worker≠tester pairing, and the rate-wall watchdog.

**Preview / v0 (data-integrity hardening in progress):**

- **Analytics aggregation** — counts, runtime rollups, and per-agent usage
  attribution have known gaps; treat numbers as indicative, not exact.
- **PTME model-vs-engine scoping** — attributing decisions cleanly to an engine
  team vs the top orchestrator is still being tightened.
- **Learning loop** — lesson capture works, but feeding lessons back into agent
  behavior is partial; counts may lag or double-count.

Build on the solid core today; read the analytics/learning numbers as v0 signal.
