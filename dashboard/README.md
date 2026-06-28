# MMOI Agent Orchestration Dashboard

A zero-dependency, single-file dashboard for watching a multi-engine agent
team work: which orchestrator is active, which specialist agents are running,
what tasks are in flight, and what model/effort each task was dispatched with.

It is part of the **Multi-Agent Orchestration Infrastructure (MMOI)** and reads
the live feeds produced by the Python scripts in [`../scripts/`](../scripts).

```
dashboard/
├── index.html               # the dashboard UI (open directly, no server)
├── agent_activity.json/.js  # per-agent live overlay (status, model, task)
├── orchestrator_stats.json/.js  # the 4 orchestrator header cards
├── live_tasks.json/.js      # dispatched-task ledger
├── analytics_data.js        # aggregated analytics (preview / v0)
└── README.md                # this file
```

## Run it (30 seconds, no install)

1. Clone the repo.
2. Open `dashboard/index.html` in any modern browser — **double-click it**, or
   `file:///…/dashboard/index.html`. No web server, no build step, no Node.

The shipped feed files are seeded to a clean **idle** state, so the page renders
out-of-the-box: four orchestrator cards (Root / Claude / Antigravity / Codex),
idle specialist slots grouped by engine team, an empty live-tasks list, and a
zeroed analytics strip. Wire in the producers below to make it come alive.

## How the live feeds work (script globals, not `fetch`)

The dashboard does **not** call `fetch()` for its data, on purpose: that lets it
run from `file://` with no server and no CORS friction. Instead each feed is a
tiny JavaScript file that assigns one global:

| Feed file                 | Global               | Produced by                       |
|---------------------------|----------------------|-----------------------------------|
| `orchestrator_stats.js`   | `window.ORCH_STATS`  | `scripts/orchestrator_stats.py`   |
| `live_tasks.js`           | `window.LIVE_TASKS`  | `scripts/dispatch_worker.py`      |
| `agent_activity.js`       | `window.AGENT_ACTIVITY` | `scripts/agent_activity.py`    |
| `analytics_data.js`       | `window.MMOI_ANALYTICS` | `scripts/build_analytics.py`   |

`index.html` includes them with plain `<script src="…">` tags and renders from
the globals. A matching `.json` file is written alongside each `.js` for tools
that prefer JSON. To update the board, re-run the producer and **refresh the
page**. The producers write atomically (and survive a Windows share-read lock on
the file while a browser holds it open), so the feed never half-writes or
silently freezes.

## The dispatch / sub-orchestrator / PTME flow

The interesting part is *how a task becomes a running agent on the board*:

1. **PTME — Per-Task Model & Effort** (`scripts/ptme.py`). Given a task's text,
   PTME classifies its complexity (S / M / L / XL) from deterministic, rule-based
   signals and maps that to a concrete `model` + `effort` from a single
   capability table. No task gets a hand-waved model — every choice is logged
   with its reason to `logs/ptme_decisions.jsonl`.

2. **Dispatch a worker** (`scripts/dispatch_worker.py`). This is the direct,
   single-worker path the top orchestrator uses:
   ```bash
   python scripts/dispatch_worker.py start \
       --engine codex --role qa --task-id T-1 --text "Verify the export job"
   # …agent now shows as running on the board…
   python scripts/dispatch_worker.py complete \
       --worker codex-qa --task-id T-1 --status done
   ```
   `start` runs PTME, sets the agent active in `agent_activity`, and appends a
   row to `live_tasks`. `complete` records duration/usage and returns the agent
   to idle. Worker ids are canonicalized to `<engine>-<role>` (e.g. `codex-qa`).

3. **Sub-orchestrator** (`scripts/sub_orchestrator.py`). Instead of the top
   orchestrator reaching in and dispatching one worker, each engine team can run
   its **own** orchestrator that behaves like the top one: take a goal,
   decompose it into 2–5 concrete sub-tasks (rule-based, no LLM call), route each
   to the right specialist role, run PTME per sub-task, and — importantly —
   structurally enforce **worker ≠ tester** (the tester is always a *different*
   role, per the team quality rubric). Each decision is logged with an
   `orchestrator` tag so `orchestrator_stats.py` can attribute it.
   ```bash
   python scripts/sub_orchestrator.py plan --engine claude \
       --goal "Research the rate wall, design a watchdog, document the playbook"
   # add --dry-run to plan without writing logs/activity
   ```

4. **Stats + analytics** roll these up: `orchestrator_stats.py` rebuilds the four
   header cards (team size, running-now, tasks-done, learning-loops, usage%) and
   `build_analytics.py` aggregates tasks/decisions/usage into `analytics_data.js`.

## The rate-wall watchdog

`scripts/rate_wall_watchdog.py` guards against dispatching to an engine that has
hit its usage wall. Codex enforces two rolling windows — a **primary 5-hour**
window and a **secondary weekly** window — reported in its session telemetry.
When a window reaches 100% the CLI starts returning rate-limit errors and any
in-flight sub-agent job is **killed, not paused**.

```bash
python scripts/rate_wall_watchdog.py check
# prints each window's % used and, for anything ≥ 90%, the local reset time

python scripts/rate_wall_watchdog.py should-dispatch --engine codex
# exit 0 = safe; non-zero + reset time = walled, skip this engine for now
```

Engines that expose no comparable local telemetry (Antigravity, Claude here) are
treated as always-dispatchable by the watchdog — it fails *open* rather than
blocking on missing data. Usage telemetry for the engine teams is read from the
Codex session artifacts under `~/.codex/sessions` when present; the Antigravity
engine exposes only wall-clock duration (no token/usage %), and the dashboard
reports that honestly rather than inventing a number.

## Customizing the roster

The shipped seed roster in `scripts/agent_activity.py` (`SEED_AGENT_IDS`) is
generic and role-based. The producers and the dashboard key off the **engine
prefix** (`claude-` / `agy- ` / `codex-`) and the **role suffix**, not on any
specific names — rename `root` to your own orchestrator id and add your own
`<engine>-<role>` worker ids and everything still wires up.

## Dashboard UX behaviors (2026-06-24 upgrade)

The single-file board ports the high-value interaction behaviors from the
internal console, against the **real** telemetry shape the producers emit — no
mocked numbers:

- **Dual usage rings per orchestrator** — each orchestrator card renders two
  rings: the **primary 5-hour** window and the **secondary weekly** window
  (`usage_pct_primary` / `usage_pct_weekly` from `orchestrator_stats.py`). An
  engine that exposes no usage telemetry (e.g. Antigravity) shows `—` and the
  honest source caption rather than a fabricated percentage.
- **Expand-state persistence** — collapsible panels (e.g. a team's *idle seed
  slots*) remember their open/closed state across re-render and page refresh via
  `localStorage` (`OPEN_PANELS` / `restoreOpenPanels`). Any `<details>` tagged
  with `data-panel="…"` participates automatically.
- **Running-vs-idle split** — each engine team separates running agents from idle
  seed slots, with the idle slots tucked into a collapsible panel.
- **Live learning-loop / lesson counts** — orchestrator cards surface
  `learning_loops_total`; the analytics strip surfaces `lessons logged` from the
  learning-loop payload.

### Dashboard UI port: follow-up

A few behaviors from the internal console are **intentionally not ported here**
to keep this public board self-contained and free of any private console code or
PII:

- **Team-size drill-down** (click an orchestrator's `agents` count to expand the
  full per-role membership) — the public board shows the count and the
  running/idle split, but not the click-through roster drill-down.
- **Full live learning-loop panel** (a scrolling feed of individual lesson
  entries with source/engine attribution) — the public board shows counts only,
  not the per-lesson stream, because the internal feed embeds private source
  paths. Re-add it here only against a sanitized lessons feed.

These are tracked as a follow-up; the ported behaviors above are complete and
PII-free.

## Known limitations / Roadmap

This dashboard is shipped honestly. The **UI, the live-feed plumbing
(script-global model), the dispatch path, PTME complexity classification, the
sub-orchestrator decomposition with structural worker≠tester pairing, and the
rate-wall watchdog are solid and tested** — they are the productized core.

The **data-aggregation and learning layers are a preview (v0)** and are being
hardened separately. Treat the following as not-yet-trustworthy for decisions:

- **Analytics aggregation** (`build_analytics.py` → `analytics_data.js`):
  counts, runtime rollups, and per-agent usage attribution have known
  data-integrity gaps (e.g. assignee parsing, duplicate/partial usage rows,
  cross-source reconciliation). Use the numbers as indicative, not exact.
- **PTME model-vs-engine scoping**: the capability table and complexity ladders
  are sound, but attributing decisions cleanly to the right *engine team* vs the
  top orchestrator (the `orchestrator` tagging) is still being tightened; some
  historical rows are attributed by prefix heuristics.
- **Learning loop** (`learning_loop` in analytics, `orchestrator_lessons.jsonl`):
  lesson capture and per-orchestrator counting work, but the loop that feeds
  lessons back into agent behavior is partial — counts may lag or double-count.

These are tracked as work-in-progress. Everything in the "solid core" above is
safe to build on today; the analytics/learning numbers should be read as v0
signal, not ground truth.
