#!/usr/bin/env python3
"""
model_supervisor.py — Team-of-Teams (ToT) per-model supervisor + worker pool.

One supervisor process owns a single model's partition of the task queue. It:

  1. SELECTS the pending tasks routed to its model (preferred_provider == model).
  2. CLAIMS each task via coordinator.py's CAS-guarded `claim` (so two supervisors
     can never double-run the same task).
  3. DISPATCHES the claimed tasks to a pool of same-model workers — sequentially
     (max_workers=1, Phase-1 MVP) or in parallel up to the model's concurrency cap
     (Phase-2). Each worker runs in its own isolated git worktree.
  4. AGGREGATES every worker's result back up into a single summary returned to the
     orchestrator (Andy), including per-task status and the deterministic result
     path written by worker_wrapper.py.

Rate-limit safety (plan §6): when a worker reports a rate-limit (non-zero exit +
'rate limit' / '429' / 'quota' in its output) the supervisor checkpoints nothing
itself but flags the task and runs a single pool-wide cool-down backoff, letting
unaffected channels keep running.

The execution seams (claimer / runner / sleep) are injectable so the pool logic is
unit-testable without spawning real CLIs or git worktrees.

Commands:
    python scripts/model_supervisor.py run --model codex [--max-workers N] [--dry-run]
    python scripts/model_supervisor.py select --model codex

Models / concurrency caps (plan §6 — protect CLI subscriptions / TPM limits):
    codex 3   antigravity/agy 2   claude-code 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import worktree_manager as wt
import worker_wrapper as ww

ROOT          = Path(__file__).resolve().parent.parent
TASKS_FILE    = ROOT / "tasks" / "active_tasks.json"
COORDINATOR   = Path(__file__).resolve().parent / "coordinator.py"
LLM_PROVIDER  = Path(__file__).resolve().parent / "llm_provider.py"

# Concurrency caps per model (plan §6). Falls back to 1 for unknown models.
MODEL_CONCURRENCY = {
    "codex":       3,
    "antigravity": 2,
    "agy":         2,
    "claude-code": 1,
}

# Statuses a supervisor is allowed to pick up (everything not yet owned).
CLAIMABLE_STATUSES = ("pending", "backlog", "")

# Cool-down window (seconds) applied once when any worker hits a rate limit.
RATE_LIMIT_COOLDOWN = 60

_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "quota exceeded", "quota")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_rate_limited(returncode: int, text: str) -> bool:
    """True if a finished worker looks rate-limited (non-zero exit + marker text)."""
    if returncode == 0:
        return False
    blob = (text or "").lower()
    return any(marker in blob for marker in _RATE_LIMIT_MARKERS)


def load_tasks(tasks_file=None) -> dict:
    """Load the task queue JSON. Returns {'tasks': []} if the file is missing."""
    path = Path(tasks_file) if tasks_file else TASKS_FILE
    if not path.exists():
        return {"tasks": []}
    return json.loads(path.read_text(encoding="utf-8"))


def select_tasks(data: dict, model: str, statuses=CLAIMABLE_STATUSES) -> list:
    """Return tasks routed to *model* that are still claimable.

    Routing is read from `preferred_provider` (written by task_router.py). Tasks
    already owned (in_progress / tested / done) are excluded so this is safe to
    re-run.
    """
    selected = []
    for task in data.get("tasks", []):
        if task.get("preferred_provider") != model:
            continue
        if (task.get("status", "") or "") in statuses:
            selected.append(task)
    return selected


def default_claimer(task_id: str, model: str) -> bool:
    """Claim via coordinator.py's CAS guard in a subprocess. Returns True on success.

    Using the real coordinator means the supervisor inherits the exact CAS + file
    lock semantics — no duplicated claim logic.
    """
    import subprocess  # local import keeps module import cheap for tests
    res = subprocess.run(
        [sys.executable, str(COORDINATOR), "claim", "--task", task_id, "--model", model],
        capture_output=True, text=True,
    )
    return res.returncode == 0


def default_runner(task: dict) -> dict:
    """Execute one task in an isolated worktree, write its result, then clean up.

    Returns a worker-result dict. The heavy work (CLI invocation) goes through
    llm_provider.py so model/effort PTME resolution is reused. Any failure is
    captured into the result rather than raised, so one bad worker never aborts
    the whole pool.
    """
    import subprocess  # local import — see default_claimer
    task_id = task.get("task_id", "unknown")
    model = task.get("preferred_provider", "claude-code")
    prompt = task.get("prompt") or task.get("title") or task_id

    result = {
        "task_id": task_id,
        "model": model,
        "status": "error",
        "returncode": None,
        "result_path": None,
        "rate_limited": False,
        "detail": "",
    }

    worktree = None
    try:
        worktree = wt.create_worktree(task_id)
        proc = subprocess.run(
            [
                sys.executable, str(LLM_PROVIDER), "run",
                "--agent", model, "--prompt", prompt, "--task-id", task_id,
            ],
            capture_output=True, text=True, cwd=str(worktree),
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        result["returncode"] = proc.returncode
        result["rate_limited"] = is_rate_limited(proc.returncode, combined)
        # Persist the worker's output deterministically.
        path = ww.write_result(task_id, combined)
        result["result_path"] = str(path)
        if result["rate_limited"]:
            result["status"] = "rate_limited"
        elif proc.returncode == 0:
            result["status"] = "ok"
        else:
            result["status"] = "error"
        result["detail"] = combined[-500:]
    except Exception as exc:  # noqa: BLE001 — isolate worker failures
        result["detail"] = "worker exception: {}".format(exc)
    finally:
        if worktree is not None:
            wt.destroy_worktree(task_id)

    return result


def run_pool(tasks, runner, max_workers=1, cooldown_seconds=RATE_LIMIT_COOLDOWN,
             sleep_fn=time.sleep) -> list:
    """Run *tasks* through *runner* with up to *max_workers* concurrent workers.

    Returns the list of worker-result dicts (one per task). When any worker reports
    a rate limit, a single cool-down backoff (sleep_fn) is applied after the batch
    — unaffected workers in the same batch are unhindered.
    """
    if not tasks:
        return []

    max_workers = max(1, int(max_workers))
    results = []
    if max_workers == 1:
        for task in tasks:
            results.append(runner(task))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # Preserve input order in the returned list.
            results = list(pool.map(runner, tasks))

    if any(r.get("rate_limited") for r in results) and cooldown_seconds:
        sleep_fn(cooldown_seconds)

    return results


def supervise(model, tasks_file=None, max_workers=None, runner=None,
              claimer=None, dry_run=False, sleep_fn=time.sleep) -> dict:
    """Top-level ToT flow for one model: select -> claim -> run pool -> aggregate.

    Returns an aggregate summary dict. `runner` and `claimer` default to the real
    subprocess-backed implementations but are injectable for tests.
    """
    runner = runner or default_runner
    claimer = claimer or default_claimer
    if max_workers is None:
        max_workers = MODEL_CONCURRENCY.get(model, 1)

    data = load_tasks(tasks_file)
    candidates = select_tasks(data, model)

    summary = {
        "model": model,
        "max_workers": max_workers,
        "candidates": [t.get("task_id") for t in candidates],
        "claimed": [],
        "skipped_claim": [],
        "results": [],
        "succeeded": 0,
        "failed": 0,
        "rate_limited": 0,
        "dry_run": bool(dry_run),
    }

    if dry_run:
        return summary

    # Claim under CAS; only run tasks we actually own.
    claimed_tasks = []
    for task in candidates:
        task_id = task.get("task_id")
        if claimer(task_id, model):
            summary["claimed"].append(task_id)
            claimed_tasks.append(task)
        else:
            summary["skipped_claim"].append(task_id)

    results = run_pool(claimed_tasks, runner, max_workers=max_workers, sleep_fn=sleep_fn)
    summary["results"] = results
    summary["succeeded"] = sum(1 for r in results if r.get("status") == "ok")
    summary["rate_limited"] = sum(1 for r in results if r.get("rate_limited"))
    summary["failed"] = sum(1 for r in results if r.get("status") == "error")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_select(args) -> None:
    data = load_tasks(args.tasks_file)
    tasks = select_tasks(data, args.model)
    print("Claimable tasks for model '{}': {}".format(args.model, len(tasks)))
    for t in tasks:
        print("  [{}] {}".format(t.get("task_id", "?"), (t.get("title") or "")[:70]))


def cmd_run(args) -> None:
    summary = supervise(
        args.model,
        tasks_file=args.tasks_file,
        max_workers=args.max_workers,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Team-of-Teams per-model supervisor + worker pool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Select, claim, and run this model's tasks")
    p_run.add_argument("--model", required=True, help="Model partition to supervise (e.g. codex)")
    p_run.add_argument("--max-workers", type=int, default=None,
                       help="Concurrent workers (default: model's MODEL_CONCURRENCY cap)")
    p_run.add_argument("--tasks-file", default=None, help="Override path to active_tasks.json")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Print the selection/plan without claiming or executing")

    p_sel = sub.add_parser("select", help="List claimable tasks for a model")
    p_sel.add_argument("--model", required=True)
    p_sel.add_argument("--tasks-file", default=None, help="Override path to active_tasks.json")

    args = parser.parse_args()
    {"run": cmd_run, "select": cmd_select}[args.command](args)


if __name__ == "__main__":
    main()
