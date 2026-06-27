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
     orchestrator (Orchestrator), including per-task status and the deterministic result
     path written by worker_wrapper.py.

Orchestrator contract: this supervisor is a pure delegating orchestrator. It selects,
claims, dispatches, checkpoints worker state on interruptions, and aggregates
results; it never performs the worker's specialist task itself.

Rate-limit safety (plan §6): when a worker reports a rate-limit (non-zero exit +
'rate limit' / '429' / 'quota' in its output) the supervisor saves a resumability
checkpoint before worker cleanup, then runs a single pool-wide cool-down backoff,
letting unaffected channels keep running.

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

import checkpoint as cp
import llm_provider as lp
import task_spec as ts
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


def _spec_gate_error(task: dict):
    """Return a blocking spec-gate message for *task*, or None when runnable."""
    complexity = (task.get("complexity", "") or "").upper()
    if not ts.spec_required_for_complexity(complexity):
        return None

    task_id = task.get("task_id", "?")
    errors = ts.spec_validation_errors(task_id)
    if not errors:
        return None
    return (
        "Task '{}' is complexity {} and cannot run without a valid spec: {}"
    ).format(task_id, complexity, errors[0])


def _build_worker_prompt(task: dict) -> str:
    """Return the prompt for a worker, including checkpoint resume context."""
    task_id = task.get("task_id", "unknown")
    base_prompt = task.get("prompt") or task.get("title") or task_id
    resume_context = task.get("resume_context")
    if not resume_context:
        return base_prompt

    acceptance = resume_context.get("acceptance_criteria")
    if isinstance(acceptance, list):
        acceptance = "; ".join(str(item) for item in acceptance if str(item).strip())

    return "\n".join([
        "Resume the interrupted task using the checkpoint context below.",
        "Task ID: {}".format(task_id),
        "Completed so far: {}".format(resume_context.get("done", "")),
        "Remaining work: {}".format(resume_context.get("remaining", "")),
        "Exact next step: {}".format(resume_context.get("next_step", "")),
        "Acceptance criteria: {}".format(acceptance or ""),
        "",
        "Original task prompt:",
        base_prompt,
    ]).strip()


def _prepare_task_for_run(task: dict) -> dict:
    """Return a runnable task copy with any checkpoint resume context loaded."""
    prepared = dict(task)
    task_id = prepared.get("task_id", "")
    resume_context = cp.load_resume_context(task_id)
    if resume_context:
        prepared["resume_context"] = resume_context
        prepared["prompt"] = _build_worker_prompt(prepared)
        cp.mark_resumed(task_id)
    else:
        prepared["prompt"] = _build_worker_prompt(prepared)
    return prepared


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
    prompt = _build_worker_prompt(task)

    result = {
        "task_id": task_id,
        "model": model,
        "status": "error",
        "returncode": None,
        "result_path": None,
        "rate_limited": False,
        "checkpoint_saved": False,
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
            result["checkpoint_saved"] = checkpoint_rate_limited_task(task, result)
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


def checkpoint_rate_limited_task(task: dict, result: dict) -> bool:
    """Save a resumability checkpoint for a rate-limited worker via coordinator.py.

    This is intentionally a narrow supervisor-generated checkpoint: the worker's
    exact domain progress is opaque to the orchestrator, so it records the saved
    output path and enough context to resume the same task after the cooldown.
    """
    import subprocess  # local import keeps module import cheap for tests

    task_id = task.get("task_id", "unknown")
    model = task.get("preferred_provider", "unknown")
    result_path = result.get("result_path") or "(no result path)"
    done = "Saved partial worker output to {} before rate-limit cooldown.".format(result_path)
    remaining = "Complete the remaining task work after the provider cooldown expires."
    next_step = "Recreate the worker worktree and rerun task {} with model {}.".format(
        task_id, model
    )
    proc = subprocess.run(
        [
            sys.executable, str(COORDINATOR), "checkpoint",
            "--task", task_id,
            "--done", done,
            "--remaining", remaining,
            "--next", next_step,
            "--model", model,
            "--interrupted-by", "rate_limit",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode == 0


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


def orchestrate_worker_plan(parent_task, worker_specs, dispatcher, max_workers=None,
                            sleep_fn=time.sleep) -> dict:
    """Interface-only local-orchestrator path for specialized sub-agent dispatch.

    Each worker spec represents an isolated unit of work that the caller must run
    in its own worktree/context. This function decides PTME model+effort, records
    the decision log, and dispatches the worker specs in parallel via run_pool.
    """
    prepared_specs = []
    decisions = []
    for worker_spec in worker_specs:
        spec = dict(worker_spec)
        agent = spec.get("agent") or parent_task.get("preferred_provider") or \
            parent_task.get("assigned_to")
        profile = lp.resolve_execution_profile(
            agent,
            task_id=spec.get("task_id"),
            complexity=spec.get("complexity"),
            cli_model=spec.get("model"),
            cli_effort=spec.get("effort"),
            decided_by="model_supervisor.orchestrate_worker_plan",
        )
        spec["resolved_model"] = profile["model"]
        spec["resolved_effort"] = profile["effort"]
        spec["decision"] = profile["decision"]
        spec["parent_task_id"] = parent_task.get("task_id", "")
        prepared_specs.append(spec)
        decisions.append(profile["decision"])

    worker_count = max_workers if max_workers is not None else max(1, len(prepared_specs))
    results = run_pool(prepared_specs, dispatcher, max_workers=worker_count, sleep_fn=sleep_fn)
    return {
        "parent_task_id": parent_task.get("task_id", ""),
        "workers": [spec.get("task_id") for spec in prepared_specs],
        "decisions": decisions,
        "results": results,
        "interface_only": True,
    }


def supervise(model, tasks_file=None, max_workers=None, runner=None,
              claimer=None, dry_run=False, sleep_fn=time.sleep) -> dict:
    """Top-level Orchestrator flow for one model: select -> claim -> dispatch -> aggregate.

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
        "blocked_spec": [],
        "resumed": [],
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
        spec_gate_error = _spec_gate_error(task)
        if spec_gate_error:
            summary["blocked_spec"].append({
                "task_id": task_id,
                "reason": spec_gate_error,
            })
            continue
        if claimer(task_id, model):
            summary["claimed"].append(task_id)
            prepared_task = _prepare_task_for_run(task)
            if prepared_task.get("resume_context"):
                summary["resumed"].append(task_id)
            claimed_tasks.append(prepared_task)
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
