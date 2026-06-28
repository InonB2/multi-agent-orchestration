import json
import sys
import threading
import time
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import parallel_dispatch as pd  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_dispatch_logs_decisions_and_respects_engine_caps(tmp_path):
    plan = []
    for idx in range(6):
        plan.append({"id": f"AGY-{idx}", "text": f"Research task {idx}", "engine": "agy"})
        plan.append({"id": f"CODEX-{idx}", "text": f"Implement task {idx}", "engine": "codex"})

    state = {
        "current": {"agy": 0, "codex": 0},
        "max_seen": {"agy": 0, "codex": 0},
        "workspaces": [],
    }
    lock = threading.Lock()

    def fake_launcher(task, workspace, decision):
        engine = task["engine"]
        with lock:
            state["current"][engine] += 1
            state["max_seen"][engine] = max(state["max_seen"][engine], state["current"][engine])
            state["workspaces"].append(str(workspace))
        time.sleep(0.05)
        with lock:
            state["current"][engine] -= 1
        return 0

    log_file = tmp_path / "ptme_decisions.jsonl"
    results = pd.dispatch_tasks(
        plan,
        launcher=fake_launcher,
        decision_log_path=log_file,
        agy_root=tmp_path / "agy-workers",
        codex_root=tmp_path / "codex-workers",
    )

    assert len(results) == len(plan)
    assert len(_read_jsonl(log_file)) == len(plan)
    assert state["max_seen"]["agy"] == 3
    assert state["max_seen"]["codex"] == 3

    workspaces = [item["workspace"] for item in results]
    assert len(workspaces) == len(set(workspaces))
    assert set(workspaces) == set(state["workspaces"])

    for item in results:
        workspace = Path(item["workspace"])
        assert workspace.exists()
        if item["engine"] == "agy":
            assert workspace.parent == tmp_path / "agy-workers"
        else:
            assert workspace.parent == tmp_path / "codex-workers"


def test_dispatch_rejects_duplicate_output_dirs(tmp_path):
    plan = [
        {"id": "DUPLICATE", "text": "Task one", "engine": "agy"},
        {"id": "DUPLICATE", "text": "Task two", "engine": "agy"},
    ]

    try:
        pd.dispatch_tasks(
            plan,
            launcher=lambda task, workspace, decision: 0,
            decision_log_path=tmp_path / "ptme_decisions.jsonl",
            agy_root=tmp_path / "agy-workers",
            codex_root=tmp_path / "codex-workers",
        )
    except ValueError as exc:
        assert "workspace" in str(exc).lower()
    else:
        raise AssertionError("Expected duplicate task IDs to be rejected")
