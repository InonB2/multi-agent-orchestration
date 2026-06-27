import argparse
import json
import sys
import threading
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import checkpoint as cp  # noqa: E402
import coordinator as co  # noqa: E402
import llm_provider as lp  # noqa: E402
import model_supervisor as ms  # noqa: E402
import task_spec as ts  # noqa: E402


def _write_tasks(path, tasks):
    path.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")


def _valid_spec(task_id, complexity="M", assigned_to="codex"):
    return {
        "task_id": task_id,
        "title": "Spec task",
        "complexity": complexity,
        "created_at": "2026-06-22T00:00:00+00:00",
        "created_by": "orchestrator",
        "what_is_done": "Initial analysis completed.",
        "what_remains": "Implement the runtime changes.",
        "exact_next_step": "Wire the validation into the supervisor.",
        "acceptance_criteria": ["Tests pass"],
        "assigned_to": assigned_to,
        "spec_version": 1,
    }


def _config_defaults():
    return """\
[agent]
preferred_model = "codex"
max_task_size = "L"

[provider]
type = "cli"
model = "gpt-5"
effort = "medium"

[provider.complexity_mapping.M]
model = "gpt-5-high"
effort = "high"
"""


def _config_agent():
    return """\
[agent]
name = "testcli"
preferred_model = "codex"
max_task_size = "L"

[provider]
type = "cli"
cli_exec_args = ["exec"]
"""


def test_mark_tested_rejects_same_tester_as_assignee(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "QA-SELF-1",
        "title": "Runtime task",
        "status": "in_progress",
        "assigned_to": "codex",
    }])
    monkeypatch.setattr(co, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(co, "_run_checkpoint", lambda *args, **kwargs: 0)

    with pytest.raises(SystemExit) as exc:
        co.cmd_mark_tested(["--task", "QA-SELF-1", "--tested-by", "codex"])

    assert exc.value.code == 1
    task = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]
    assert task["status"] == "in_progress"


def test_mark_tested_requires_non_empty_tester_identity(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "QA-SELF-0",
        "title": "Runtime task",
        "status": "in_progress",
        "assigned_to": "codex",
    }])
    monkeypatch.setattr(co, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(co, "_run_checkpoint", lambda *args, **kwargs: 0)

    with pytest.raises(SystemExit) as exc:
        co.cmd_mark_tested(["--task", "QA-SELF-0"])

    assert exc.value.code == 1
    assert "--tested-by" in capsys.readouterr().err
    task = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]
    assert task["status"] == "in_progress"
    assert "tested_by" not in task


def test_mark_tested_allows_independent_tester(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "QA-SELF-2",
        "title": "Runtime task",
        "status": "in_progress",
        "assigned_to": "codex",
    }])
    monkeypatch.setattr(co, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(co, "_run_checkpoint", lambda *args, **kwargs: 0)

    co.cmd_mark_tested([
        "--task", "QA-SELF-2",
        "--tested-by", "agy",
        "--result-path", "owner_inbox/result.md",
    ])

    task = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]
    assert task["status"] == "tested"
    assert task["tested_by"] == "agy"


def test_mark_done_requires_recorded_tester_identity(tmp_path, monkeypatch, capsys):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "QA-DONE-1",
        "title": "Runtime task",
        "status": "tested",
        "assigned_to": "codex",
        "tested_by": "",
    }])
    monkeypatch.setattr(co, "TASKS_FILE", tasks_file)

    with pytest.raises(SystemExit) as exc:
        co.cmd_mark_done(["--task", "QA-DONE-1"])

    assert exc.value.code == 1
    assert "tested_by" in capsys.readouterr().err
    task = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]
    assert task["status"] == "tested"
    assert "closed_at" not in task


def test_mark_done_force_overrides_missing_tester_identity(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "QA-DONE-2",
        "title": "Runtime task",
        "status": "tested",
        "assigned_to": "codex",
        "tested_by": "",
    }])
    monkeypatch.setattr(co, "TASKS_FILE", tasks_file)

    co.cmd_mark_done(["--task", "QA-DONE-2", "--force"])

    task = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][0]
    assert task["status"] == "done"
    assert task["closed_at"]


def test_supervise_blocks_complex_task_without_spec(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    _write_tasks(tasks_file, [{
        "task_id": "SPEC-BLOCK-1",
        "title": "Complex task",
        "status": "pending",
        "complexity": "M",
        "preferred_provider": "codex",
        "assigned_to": "codex",
    }])
    monkeypatch.setattr(ts, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ts, "SPECS_DIR", tmp_path / "specs")

    claimed = []
    summary = ms.supervise(
        "codex",
        tasks_file=str(tasks_file),
        runner=lambda task: {"task_id": task["task_id"], "status": "ok", "rate_limited": False},
        claimer=lambda task_id, model: claimed.append(task_id) or True,
    )

    assert claimed == []
    assert summary["blocked_spec"][0]["task_id"] == "SPEC-BLOCK-1"
    assert "spec" in summary["blocked_spec"][0]["reason"].lower()


def test_supervise_allows_valid_spec_and_small_task_without_spec(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    _write_tasks(tasks_file, [
        {
            "task_id": "SPEC-ALLOW-1",
            "title": "Complex task",
            "status": "pending",
            "complexity": "M",
            "preferred_provider": "codex",
            "assigned_to": "codex",
        },
        {
            "task_id": "SPEC-ALLOW-2",
            "title": "Small task",
            "status": "pending",
            "complexity": "S",
            "preferred_provider": "codex",
            "assigned_to": "codex",
        },
    ])
    (specs_dir / "SPEC-ALLOW-1.json").write_text(
        json.dumps(_valid_spec("SPEC-ALLOW-1"), indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(ts, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ts, "SPECS_DIR", specs_dir)

    summary = ms.supervise(
        "codex",
        tasks_file=str(tasks_file),
        runner=lambda task: {"task_id": task["task_id"], "status": "ok", "rate_limited": False},
        claimer=lambda *args: True,
    )

    assert summary["claimed"] == ["SPEC-ALLOW-1", "SPEC-ALLOW-2"]
    assert summary["succeeded"] == 2


def test_supervise_reloads_resume_context_before_running(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    snapshots_dir = tmp_path / "snapshots"
    queue_dir = tmp_path / "queue"
    _write_tasks(tasks_file, [{
        "task_id": "RESUME-1",
        "title": "Resume task",
        "status": "pending",
        "complexity": "S",
        "preferred_provider": "codex",
        "assigned_to": "codex",
    }])

    monkeypatch.setattr(cp, "ROOT", tmp_path)
    monkeypatch.setattr(cp, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(cp, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(cp, "QUEUE_DIR", queue_dir)
    monkeypatch.setattr(cp, "QUEUE_FILE", queue_dir / "resume_queue.json")
    monkeypatch.setattr(ts, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ts, "SPECS_DIR", tmp_path / "specs")

    cp.cmd_save([
        "--task", "RESUME-1",
        "--done", "Saved work",
        "--remaining", "Finish the implementation",
        "--next", "Run the focused tests",
    ])

    seen = {}

    def runner(task):
        seen["resume_context"] = task.get("resume_context")
        seen["prompt"] = task.get("prompt")
        return {"task_id": task["task_id"], "status": "ok", "rate_limited": False}

    summary = ms.supervise(
        "codex",
        tasks_file=str(tasks_file),
        runner=runner,
        claimer=lambda *args: True,
    )

    assert summary["succeeded"] == 1
    assert seen["resume_context"]["done"] == "Saved work"
    assert "Finish the implementation" in seen["prompt"]
    queue = json.loads((queue_dir / "resume_queue.json").read_text(encoding="utf-8"))
    assert not any(entry["task_id"] == "RESUME-1" for entry in queue)


def test_cmd_run_dry_run_writes_decision_log(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "agents"
    config_dir.mkdir()
    (config_dir / "_defaults.toml").write_text(_config_defaults(), encoding="utf-8")
    (config_dir / "testcli.toml").write_text(_config_agent(), encoding="utf-8")

    decision_log = tmp_path / "logs" / "ptme_decisions.jsonl"
    monkeypatch.setattr(lp, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(lp, "DEFAULTS", config_dir / "_defaults.toml")
    monkeypatch.setattr(lp, "DECISION_LOG", decision_log, raising=False)

    lp.cmd_run(argparse.Namespace(
        agent="testcli",
        prompt="Implement the runtime change",
        dry_run=True,
        task_id="PTME-1",
        model=None,
        effort=None,
        complexity="M",
    ))

    records = decision_log.read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["task_id"] == "PTME-1"
    assert record["recommended_model"] == "gpt-5-high"
    assert record["decided_model"] == "gpt-5-high"
    assert record["decided_by"] == "llm_provider.cmd_run"
    assert record["reason"]
    assert "gpt-5-high" in capsys.readouterr().out


def test_orchestrate_worker_plan_dispatches_parallel_stub_workers(tmp_path, monkeypatch):
    config_dir = tmp_path / "agents"
    config_dir.mkdir()
    (config_dir / "_defaults.toml").write_text(_config_defaults(), encoding="utf-8")
    (config_dir / "testcli.toml").write_text(_config_agent(), encoding="utf-8")

    decision_log = tmp_path / "logs" / "ptme_decisions.jsonl"
    monkeypatch.setattr(lp, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(lp, "DEFAULTS", config_dir / "_defaults.toml")
    monkeypatch.setattr(lp, "DECISION_LOG", decision_log, raising=False)

    barrier = threading.Barrier(2, timeout=5)

    def dispatcher(worker_spec):
        barrier.wait()
        return {
            "task_id": worker_spec["task_id"],
            "status": "ok",
            "resolved_model": worker_spec["resolved_model"],
            "resolved_effort": worker_spec["resolved_effort"],
        }

    summary = ms.orchestrate_worker_plan(
        parent_task={"task_id": "PARENT-1", "assigned_to": "claude-code"},
        worker_specs=[
            {
                "task_id": "SUB-1",
                "agent": "testcli",
                "prompt": "Implement runtime guard A",
                "complexity": "M",
            },
            {
                "task_id": "SUB-2",
                "agent": "testcli",
                "prompt": "Implement runtime guard B",
                "complexity": "M",
            },
        ],
        dispatcher=dispatcher,
        max_workers=2,
    )

    assert [result["task_id"] for result in summary["results"]] == ["SUB-1", "SUB-2"]
    assert all(result["status"] == "ok" for result in summary["results"])
    assert all(
        decision["decided_model"] == "gpt-5-high" for decision in summary["decisions"]
    )
    records = decision_log.read_text(encoding="utf-8").splitlines()
    assert len(records) == 2
