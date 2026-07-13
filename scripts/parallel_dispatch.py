#!/usr/bin/env python3
"""Concurrent dispatch through AOA's configured agent adapters."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import agy_workspace
import dispatch as aoa_dispatch
import ptme
from adapters.base import DispatchRequest
from config_loader import load_aoa_config

ROOT = Path(__file__).resolve().parent.parent
AOA_CONFIG = load_aoa_config()
DEFAULT_AGY_ROOT = Path(AOA_CONFIG["paths"]["agy_workers"])
DEFAULT_CODEX_ROOT = Path(AOA_CONFIG["paths"]["codex_workers"])
DEFAULT_CLAUDE_ROOT = Path(AOA_CONFIG["paths"].get("claude_workers", ROOT / "workspaces" / "claude"))
_ADAPTERS = AOA_CONFIG.get("dispatch", {}).get("adapters", {})
ENGINE_LIMITS = {
    engine: int(AOA_CONFIG.get("engine_limits", {}).get(engine, entry.get("max_parallel", 1)))
    for engine, entry in _ADAPTERS.items()
}


def _task_recommendation(task: dict) -> tuple[str | None, str | None]:
    recommend = task.get("recommend") or {}
    model = task.get("recommended_model") or recommend.get("model")
    effort = task.get("recommended_effort") or recommend.get("effort")
    return model, effort


def _task_override(task: dict) -> tuple[str | None, str | None]:
    override = task.get("override") or {}
    model = task.get("override_model") or override.get("model")
    effort = task.get("override_effort") or override.get("effort")
    return model, effort


def _validate_task(task: dict) -> None:
    for required in ("id", "text", "engine"):
        if not task.get(required):
            raise ValueError("Task missing required field '{}'".format(required))
    if task["engine"] not in ENGINE_LIMITS:
        raise ValueError("Unsupported engine '{}'".format(task["engine"]))


def _codex_workspace(task_id: str, root: Path) -> Path:
    agy_workspace.validate_worker_id(task_id)
    workspace = root / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def assign_workspace(task: dict, agy_root: Path, codex_root: Path,
                     workspace_roots: dict[str, Path] | None = None) -> Path:
    if task["engine"] == "agy":
        return agy_workspace.provision_workspace(task["id"], root=agy_root)
    roots = {"codex": codex_root, "claude": DEFAULT_CLAUDE_ROOT}
    roots.update(workspace_roots or {})
    return _codex_workspace(task["id"], root=Path(roots.get(
        task["engine"], ROOT / "workspaces" / task["engine"]
    )))


def build_engine_command(task: dict, workspace: Path) -> list[str]:
    """Return the prompt-free public command shape for diagnostics/dry-runs."""
    return [
        AOA_CONFIG["cli"].get("python", sys.executable),
        str(ROOT / "scripts" / "dispatch.py"),
        "--engine", task["engine"],
        "--workdir", str(workspace),
        "--task-id", task["id"],
    ]


def subprocess_launcher(task: dict, workspace: Path, decision: dict) -> int:
    adapter_config = _ADAPTERS[task["engine"]]
    timeout = int(
        adapter_config.get("timeout_seconds")
        or AOA_CONFIG.get("timeouts", {}).get(f"{task['engine']}_dispatch_seconds", 600)
    )
    request = DispatchRequest(
        engine=task["engine"], prompt=task["text"], workdir=workspace,
        timeout=timeout, model=decision.get("decided_model"),
        effort=decision.get("decided_effort"),
    )
    result = aoa_dispatch.dispatch_request(
        request, task_id=task["id"], role=task.get("role", "worker"),
        config=AOA_CONFIG,
    )
    return result.exit_code


def _prepare_dispatch(
    tasks: list[dict],
    agy_root: Path,
    codex_root: Path,
    decided_by: str,
    workspace_roots: dict[str, Path] | None = None,
) -> list[dict]:
    prepared = []
    seen_workspaces = set()

    for index, task in enumerate(tasks):
        _validate_task(task)
        workspace = assign_workspace(
            task, agy_root=agy_root, codex_root=codex_root,
            workspace_roots=workspace_roots,
        )
        workspace_key = str(workspace.resolve()).lower()
        if workspace_key in seen_workspaces:
            raise ValueError(
                "Workspace collision for task '{}': {}".format(task["id"], workspace)
            )
        seen_workspaces.add(workspace_key)

        complexity = ptme.classify_complexity(task["text"])
        try:
            default_model, default_effort = ptme.recommend_for_complexity(
                complexity, family=task["engine"]
            )
        except ValueError:
            default_model, default_effort = ptme.recommend_for_complexity(complexity)
        recommended_model, recommended_effort = _task_recommendation(task)
        override_model, override_effort = _task_override(task)

        decision = ptme.decide(
            task_id=task["id"],
            task_text=task["text"],
            recommended_model=recommended_model or default_model,
            recommended_effort=recommended_effort or default_effort,
            override_model=override_model,
            override_effort=override_effort,
            decided_by=task.get("decided_by", decided_by),
            engine=task["engine"],
        )

        prepared.append({
            "index": index,
            "task": task,
            "workspace": workspace,
            "decision": decision,
        })

    return prepared


def dispatch_tasks(
    tasks: list[dict],
    launcher=None,
    decision_log_path: Path | None = None,
    agy_root: Path = DEFAULT_AGY_ROOT,
    codex_root: Path = DEFAULT_CODEX_ROOT,
    decided_by: str = "local_orchestrator",
    workspace_roots: dict[str, Path] | None = None,
) -> list[dict]:
    launcher = launcher or subprocess_launcher
    tasks = list(tasks)

    original_log_file = ptme.LOG_FILE
    if decision_log_path is not None:
        ptme.LOG_FILE = decision_log_path

    try:
        prepared = _prepare_dispatch(
            tasks,
            agy_root=agy_root,
            codex_root=codex_root,
            decided_by=decided_by,
            workspace_roots=workspace_roots,
        )
    finally:
        ptme.LOG_FILE = original_log_file

    results = []
    future_map = {}

    with ExitStack() as stack:
        pools = {
            engine: stack.enter_context(ThreadPoolExecutor(max_workers=ENGINE_LIMITS[engine]))
            for engine in sorted({item["task"]["engine"] for item in prepared})
        }
        for item in prepared:
            pool = pools[item["task"]["engine"]]
            future = pool.submit(launcher, item["task"], item["workspace"], item["decision"])
            future_map[future] = item

        for future in as_completed(future_map):
            item = future_map[future]
            exit_code = future.result()
            results.append({
                "id": item["task"]["id"],
                "engine": item["task"]["engine"],
                "workspace": str(item["workspace"]),
                "exit_code": exit_code,
                "decision": item["decision"],
            })

    return sorted(results, key=lambda item: item["id"])


def load_plan(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Plan must be a JSON list of task dicts")
    return payload


def format_summary(results: list[dict]) -> str:
    lines = [
        "{:<18} {:<8} {:<18} {:<8} {}".format(
            "TASK",
            "ENGINE",
            "MODEL",
            "EFFORT",
            "EXIT",
        )
    ]
    lines.append("-" * 72)
    for item in results:
        decision = item["decision"]
        lines.append(
            "{:<18} {:<8} {:<18} {:<8} {}".format(
                item["id"],
                item["engine"],
                decision["decided_model"],
                decision["decided_effort"],
                item["exit_code"],
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parallel configured-adapter coordinator")
    parser.add_argument("--plan", required=True, help="Path to a JSON plan file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    plan = load_plan(Path(args.plan))
    results = dispatch_tasks(plan)
    print(format_summary(results))
    return 0 if all(item["exit_code"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
