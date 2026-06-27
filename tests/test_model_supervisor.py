"""
tests/test_model_supervisor.py — ToT per-model supervisor + worker pool.

Covers TEAM_OF_TEAMS_PLAN:
  * task 1.3 — supervisor selects its model's tasks, claims them, runs them
  * task 2.1 — parallel pool spawns up to N workers and aggregates results
  * task 2.3 — rate-limit triggers a cool-down backoff
The execution seams (claimer / runner / sleep) are injected so no real CLI,
git worktree, or subprocess is spawned.
"""

import json
import sys
import threading
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import model_supervisor as ms  # noqa: E402


# --- helpers ---------------------------------------------------------------

def _ok_runner(task):
    return {"task_id": task.get("task_id"), "status": "ok", "rate_limited": False}


def _tasks_doc():
    return {"tasks": [
        {"task_id": "C-1", "preferred_provider": "codex", "status": "pending"},
        {"task_id": "C-2", "preferred_provider": "codex", "status": "backlog"},
        {"task_id": "C-3", "preferred_provider": "codex", "status": "in_progress"},  # owned
        {"task_id": "A-1", "preferred_provider": "antigravity", "status": "pending"},
    ]}


# --- is_rate_limited -------------------------------------------------------

def test_is_rate_limited_detects_markers():
    assert ms.is_rate_limited(1, "Error: 429 Too Many Requests")
    assert ms.is_rate_limited(1, "quota exceeded for today")
    assert not ms.is_rate_limited(0, "429")          # zero exit → not limited
    assert not ms.is_rate_limited(1, "syntax error")  # no marker


# --- select_tasks ----------------------------------------------------------

def test_select_tasks_filters_model_and_status():
    selected = ms.select_tasks(_tasks_doc(), "codex")
    ids = [t["task_id"] for t in selected]
    assert ids == ["C-1", "C-2"]            # C-3 is in_progress (owned), A-1 is another model


def test_select_tasks_other_model():
    selected = ms.select_tasks(_tasks_doc(), "antigravity")
    assert [t["task_id"] for t in selected] == ["A-1"]


# --- run_pool --------------------------------------------------------------

def test_run_pool_sequential_runs_all_in_order():
    tasks = [{"task_id": "T{}".format(i)} for i in range(5)]
    results = ms.run_pool(tasks, _ok_runner, max_workers=1)
    assert [r["task_id"] for r in results] == ["T0", "T1", "T2", "T3", "T4"]


def test_run_pool_parallel_runs_all_and_preserves_order():
    tasks = [{"task_id": "T{}".format(i)} for i in range(6)]
    results = ms.run_pool(tasks, _ok_runner, max_workers=3)
    assert sorted(r["task_id"] for r in results) == ["T0", "T1", "T2", "T3", "T4", "T5"]
    # pool.map preserves input ordering
    assert [r["task_id"] for r in results] == ["T0", "T1", "T2", "T3", "T4", "T5"]


def test_run_pool_actually_parallel():
    """With max_workers=3 the three workers overlap (proven via a barrier)."""
    barrier = threading.Barrier(3, timeout=5)

    def barrier_runner(task):
        barrier.wait()  # raises BrokenBarrierError if they don't all arrive
        return _ok_runner(task)

    tasks = [{"task_id": "P{}".format(i)} for i in range(3)]
    results = ms.run_pool(tasks, barrier_runner, max_workers=3)
    assert len(results) == 3  # no BrokenBarrierError → genuine concurrency


def test_run_pool_rate_limit_triggers_cooldown():
    slept = []

    def limited_runner(task):
        return {"task_id": task["task_id"], "status": "rate_limited", "rate_limited": True}

    ms.run_pool([{"task_id": "X"}], limited_runner, max_workers=1,
                cooldown_seconds=60, sleep_fn=lambda s: slept.append(s))
    assert slept == [60]


def test_run_pool_rate_limit_checkpoints_before_destroy_and_cooldown(tmp_path, monkeypatch):
    events = []
    worktree = tmp_path / "WT-RL"
    worktree.mkdir()

    def fake_create(task_id):
        return worktree

    def fake_destroy(task_id):
        events.append(("destroy", task_id))
        return True

    def fake_checkpoint(task, result):
        events.append(("checkpoint", task["task_id"], result["result_path"]))
        return True

    def fake_write_result(task_id, content):
        path = tmp_path / "{}.md".format(task_id)
        path.write_text(content, encoding="utf-8")
        return path

    def fake_run(cmd, **kwargs):
        return type("R", (), {
            "stdout": "",
            "stderr": "Error: 429 Too Many Requests",
            "returncode": 1,
        })()

    monkeypatch.setattr(ms.wt, "create_worktree", fake_create)
    monkeypatch.setattr(ms.wt, "destroy_worktree", fake_destroy)
    monkeypatch.setattr(ms, "checkpoint_rate_limited_task", fake_checkpoint)
    monkeypatch.setattr(ms.ww, "write_result", fake_write_result)
    monkeypatch.setattr("subprocess.run", fake_run)

    results = ms.run_pool(
        [{"task_id": "RL-1", "preferred_provider": "codex", "prompt": "work"}],
        ms.default_runner,
        max_workers=1,
        cooldown_seconds=60,
        sleep_fn=lambda seconds: events.append(("sleep", seconds)),
    )

    assert results[0]["status"] == "rate_limited"
    assert [event[0] for event in events] == ["checkpoint", "destroy", "sleep"]
    assert events[0][1] == "RL-1"
    assert events[2][1] == 60


def test_run_pool_no_cooldown_when_clean():
    slept = []
    ms.run_pool([{"task_id": "X"}], _ok_runner, max_workers=1,
                cooldown_seconds=60, sleep_fn=lambda s: slept.append(s))
    assert slept == []


# --- supervise end-to-end --------------------------------------------------

def test_supervise_claims_and_aggregates(tmp_path):
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(_tasks_doc()), encoding="utf-8")

    claimed = []

    def fake_claimer(task_id, model):
        # Simulate CAS: C-2 already taken by a peer supervisor → claim fails.
        if task_id == "C-2":
            return False
        claimed.append(task_id)
        return True

    summary = ms.supervise(
        "codex", tasks_file=str(tf), max_workers=2,
        runner=_ok_runner, claimer=fake_claimer,
    )

    assert summary["candidates"] == ["C-1", "C-2"]
    assert summary["claimed"] == ["C-1"]
    assert summary["skipped_claim"] == ["C-2"]
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert summary["rate_limited"] == 0
    assert [r["task_id"] for r in summary["results"]] == ["C-1"]


def test_supervise_dry_run_does_not_claim(tmp_path):
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(_tasks_doc()), encoding="utf-8")

    def boom_claimer(task_id, model):
        raise AssertionError("dry-run must not claim")

    summary = ms.supervise(
        "codex", tasks_file=str(tf), runner=_ok_runner,
        claimer=boom_claimer, dry_run=True,
    )
    assert summary["dry_run"] is True
    assert summary["claimed"] == []
    assert summary["candidates"] == ["C-1", "C-2"]


def test_supervise_default_concurrency_cap(tmp_path):
    """When max_workers is None, the model's MODEL_CONCURRENCY cap is used."""
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    summary = ms.supervise(
        "codex", tasks_file=str(tf), runner=_ok_runner,
        claimer=lambda *a: True,
    )
    assert summary["max_workers"] == ms.MODEL_CONCURRENCY["codex"]
