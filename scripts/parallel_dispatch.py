#!/usr/bin/env python3
"""
parallel_dispatch.py — coordinated multi-flight dispatch for agy and codex.

Claude-team workers are dispatched by Root directly, not through this script.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import agy_workspace
import ptme

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGY_ROOT = Path("D:/agy-workers")
DEFAULT_CODEX_ROOT = ROOT / "scratchpad" / "codex-workers"
ENGINE_LIMITS = {
    "agy": 3,
    "codex": 3,
}
ENGINE_FAMILIES = {
    "agy": "agy",
    "codex": "codex",
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


def assign_workspace(task: dict, agy_root: Path, codex_root: Path) -> Path:
    if task["engine"] == "agy":
        return agy_workspace.provision_workspace(task["id"], root=agy_root)
    return _codex_workspace(task["id"], root=codex_root)


def build_engine_command(task: dict, workspace: Path) -> list[str]:
    if task["engine"] == "agy":
        return [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "invoke_agy.ps1"),
            "-WorkspaceDir",
            str(workspace),
            "-Prompt",
            task["text"],
        ]

    return [
        "codex",
        "exec",
        "--cd",
        str(workspace),
        task["text"],
    ]


def subprocess_launcher(task: dict, workspace: Path, decision: dict) -> int:
    del decision
    command = build_engine_command(task, workspace)
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.returncode


def _prepare_dispatch(
    tasks: list[dict],
    agy_root: Path,
    codex_root: Path,
    decided_by: str,
) -> list[dict]:
    prepared = []
    seen_workspaces = set()

    for index, task in enumerate(tasks):
        _validate_task(task)
        workspace = assign_workspace(task, agy_root=agy_root, codex_root=codex_root)
        workspace_key = str(workspace.resolve()).lower()
        if workspace_key in seen_workspaces:
            raise ValueError(
                "Workspace collision for task '{}': {}".format(task["id"], workspace)
            )
        seen_workspaces.add(workspace_key)

        complexity = ptme.classify_complexity(task["text"])
        default_model, default_effort = ptme.recommend_for_complexity(
            complexity, family=ENGINE_FAMILIES[task["engine"]]
        )
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
        )
    finally:
        ptme.LOG_FILE = original_log_file

    results = []
    future_map = {}

    with ThreadPoolExecutor(max_workers=ENGINE_LIMITS["agy"]) as agy_pool, ThreadPoolExecutor(
        max_workers=ENGINE_LIMITS["codex"]
    ) as codex_pool:
        pools = {
            "agy": agy_pool,
            "codex": codex_pool,
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
    parser = argparse.ArgumentParser(description="Parallel agy/codex coordinator")
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
