from __future__ import annotations

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
    engines = ("agy", "codex", "claude")
    for idx in range(6):
        for engine in engines:
            plan.append({"id": f"{engine.upper()}-{idx}", "text": f"Task {idx}", "engine": engine})

    state = {
        "current": {engine: 0 for engine in engines},
        "max_seen": {engine: 0 for engine in engines},
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
        workspace_roots={
            "claude": tmp_path / "claude-workers",
        },
    )

    assert len(results) == len(plan)
    assert len(_read_jsonl(log_file)) == len(plan)
    assert state["max_seen"]["agy"] == 3
    assert state["max_seen"]["codex"] == 3
    assert state["max_seen"]["claude"] == 1

    workspaces = [item["workspace"] for item in results]
    assert len(workspaces) == len(set(workspaces))
    assert set(workspaces) == set(state["workspaces"])

    for item in results:
        workspace = Path(item["workspace"])
        assert workspace.exists()
        if item["engine"] == "agy":
            assert workspace.parent == tmp_path / "agy-workers"
        elif item["engine"] == "codex":
            assert workspace.parent == tmp_path / "codex-workers"
        else:
            assert workspace.parent == tmp_path / f"{item['engine']}-workers"


def test_public_command_shape_never_contains_prompt(tmp_path):
    task = {"id": "CLAUDE-1", "text": "PRIVATE PROMPT", "engine": "claude"}
    command = pd.build_engine_command(task, tmp_path / "workspace with spaces")
    assert "PRIVATE PROMPT" not in command
    assert command[command.index("--engine") + 1] == "claude"
    assert "dispatch.py" in " ".join(command)


def test_default_launcher_uses_common_dispatch_contract(monkeypatch, tmp_path):
    seen = {}

    def fake_dispatch(request, **kwargs):
        seen["request"] = request
        seen["kwargs"] = kwargs
        return pd.aoa_dispatch.DispatchResult("claude", "success", 0, "ok", "", "", 0)
    monkeypatch.setattr(pd.aoa_dispatch, "dispatch_request", fake_dispatch)
    task = {"id": "CLAUDE-2", "text": "stdin-only", "engine": "claude", "role": "qa"}
    decision = {"decided_model": "model", "decided_effort": "low"}
    assert pd.subprocess_launcher(task, tmp_path, decision) == 0
    assert seen["request"].prompt == "stdin-only"
    assert seen["request"].workdir == tmp_path
    assert seen["kwargs"]["task_id"] == "CLAUDE-2"


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


def test_parallel_dispatch_rejects_disabled_adapter(tmp_path):
    plan = [{"id": "DISABLED-1", "text": "Task", "engine": "example_echo"}]
    try:
        pd.dispatch_tasks(
            plan,
            launcher=lambda task, workspace, decision: 0,
            decision_log_path=tmp_path / "ptme.jsonl",
        )
    except ValueError as exc:
        assert "disabled" in str(exc).lower()
    else:
        raise AssertionError("Expected disabled adapter to be rejected")
