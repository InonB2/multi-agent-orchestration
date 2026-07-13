"""AOA first-run command line."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


TASK_ID = "HELLO-ORG-001"
CATCH_SENTINEL = "AOA_DEMO_CATCH: Unicode casefold defect"


def _root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / "aoa.config.json").exists():
        raise RuntimeError("AOA source checkout not found; install with: python -m pip install -e .")
    return root


def _load_config(root: Path) -> dict:
    local = root / "aoa.config.local.json"
    path = local if local.exists() else root / "aoa.config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_test(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, path.name], cwd=str(path.parent),
        capture_output=True, text=True,
    )


def _write_json_pair(json_path: Path, js_path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(text + "\n", encoding="utf-8")
    js_path.write_text("window.LIVE_TASKS = " + text + ";", encoding="utf-8")


def _dashboard_catch(root: Path, worker: str, tester: str, state: str,
                     evidence: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "_meta": {
            "schema": 1,
            "note": "Fresh AOA demo telemetry. No personal or pre-existing task data.",
            "updated_at": now,
            "generator": "aoa demo",
        },
        "entries": [{
            "task_id": TASK_ID,
            "worker_id": worker + "-worker",
            "worker_engine": worker,
            "tester_engine": tester,
            "status": "caught",
            "qa_verdict": "FAIL",
            "qa_evidence": evidence,
            "lifecycle_state": state,
            "started_at": now,
            "updated_at": now,
            "completed_at": now,
        }],
    }
    runtime_dashboard = root / ".aoa" / "dashboard"
    runtime_dashboard.mkdir(parents=True, exist_ok=True)
    _write_json_pair(runtime_dashboard / "live_tasks.json",
                     runtime_dashboard / "live_tasks.js", payload)


def _demo_state(root: Path, worker: str) -> tuple[Path, Path]:
    state = root / ".aoa" / "demo"
    if state.exists():
        shutil.rmtree(state)
    project = state / "hello-org"
    project.mkdir(parents=True)
    bundled = root / "examples" / "hello-org"
    for name in ("hello_org.py", "visible_test.py", "oracle_test.py", "worker_prompt.md", "tester_prompt.md"):
        shutil.copy2(bundled / name, project / name)
    tasks_file = state / "active_tasks.json"
    tasks_file.write_text(json.dumps({
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "tasks": [{
            "task_id": TASK_ID,
            "title": "Implement a canonical organization member handle",
            "assigned_to": worker,
            "preferred_provider": worker,
            "status": "pending",
            "phase": "queued",
            "complexity": "S",
            "notes": "Independent tester must verify Unicode normalization.",
        }],
    }, indent=2), encoding="utf-8")
    return project, tasks_file


def _dispatch(root: Path, engine: str, prompt_file: Path, project: Path,
              role: str) -> object:
    scripts = root / "scripts"
    sys.path.insert(0, str(scripts))
    import dispatch
    from adapters.base import DispatchRequest

    config = _load_config(root)
    # Demo state belongs in the install-local runtime dashboard, never shipped feeds.
    config.setdefault("dispatch", {})["dashboard_telemetry"] = False
    timeout = int(config.get("timeouts", {}).get(engine + "_dispatch_seconds", 600))
    request = DispatchRequest(
        engine=engine,
        prompt=prompt_file.read_text(encoding="utf-8"),
        workdir=project,
        timeout=timeout,
    )
    return dispatch.dispatch_request(
        request, task_id=TASK_ID, role=role, config=config,
        telemetry_path=root / ".aoa" / "demo" / "dispatch_telemetry.jsonl",
    )


def run_demo() -> int:
    root = _root()
    config = _load_config(root)
    demo = config.get("demo", {})
    worker = str(demo.get("worker_engine") or "").strip()
    tester = str(demo.get("tester_engine") or "").strip()
    if not worker or not tester:
        print("[ERROR] demo.worker_engine and demo.tester_engine must be configured", file=sys.stderr)
        return 2
    if worker == tester:
        print("[ERROR] worker engine and tester engine must differ", file=sys.stderr)
        return 2

    project, tasks_file = _demo_state(root, worker)
    scripts = root / "scripts"
    sys.path.insert(0, str(scripts))
    import coordinator
    coordinator.TASKS_FILE = tasks_file

    print("AOa hello-org demo")
    print("worker engine: {}".format(worker))
    print("tester engine: {}".format(tester))
    coordinator.cmd_claim(["--task", TASK_ID, "--model", worker])

    worker_result = _dispatch(root, worker, project / "worker_prompt.md", project, "worker")
    if worker_result.exit_code != 0:
        print("[ERROR] worker dispatch failed: {}".format(worker_result.status), file=sys.stderr)
        return worker_result.exit_code
    visible = _run_test(project / "visible_test.py")
    if visible.returncode != 0:
        print(visible.stdout + visible.stderr, file=sys.stderr)
        print("[ERROR] worker did not complete the visible contract", file=sys.stderr)
        return 1
    print("visible worker tests: PASS")

    try:
        coordinator.cmd_mark_done(["--task", TASK_ID])
    except SystemExit as exc:
        if exc.code != 1:
            raise
        print("lifecycle gate: BLOCKED false done (tester sign-off missing)")
    else:
        print("[ERROR] lifecycle gate allowed an untested done", file=sys.stderr)
        return 1

    tester_result = _dispatch(root, tester, project / "tester_prompt.md", project, "tester")
    oracle = _run_test(project / "oracle_test.py")
    caught = (
        tester_result.exit_code == 0
        and CATCH_SENTINEL in tester_result.message
        and oracle.returncode != 0
    )
    if not caught:
        print("[ERROR] tester did not independently confirm the planted defect", file=sys.stderr)
        print(tester_result.message, file=sys.stderr)
        print(oracle.stdout + oracle.stderr, file=sys.stderr)
        return 1

    state = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]["status"]
    evidence = "oracle_test.py rejects lower() for Straße; expected strasse"
    _dashboard_catch(root, worker, tester, state, evidence)
    print("independent tester: CAUGHT planted Unicode casefold defect")
    print("task state: {} (never advanced to tested/done)".format(state))
    print("dashboard entry: {}".format(root / ".aoa" / "dashboard" / "live_tasks.json"))
    print("dashboard: http://127.0.0.1:7780/")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aoa")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="run the worker-not-tester hello-org demo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        return run_demo()
    return 1
