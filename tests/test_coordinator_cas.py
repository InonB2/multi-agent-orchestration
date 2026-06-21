"""
tests/test_coordinator_cas.py — ToT Compare-and-Swap (CAS) claim guard.

Covers TEAM_OF_TEAMS_PLAN task 1.1 acceptance:
  * claim on an already in_progress task is rejected with exit code 1
  * two concurrent claims on the same task → exactly one success, one rejection
  * the .lock sidecar is always released
"""

import json
import sys
import threading
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import coordinator as co  # noqa: E402


def _make_tasks_file(tmp_path, tasks):
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")
    return tf


def test_cas_rejects_already_in_progress(tmp_path, monkeypatch):
    """Claiming a task already in_progress exits 1 (CAS guard)."""
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "CAS-1", "title": "t", "status": "in_progress", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    with pytest.raises(SystemExit) as exc:
        co.cmd_claim(["--task", "CAS-1", "--model", "codex"])
    assert exc.value.code == 1


def test_cas_allows_pending(tmp_path, monkeypatch):
    """A pending task is still claimable (backward compatible)."""
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "CAS-2", "title": "t", "status": "pending", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_claim(["--task", "CAS-2", "--model", "codex"])
    task = next(t for t in json.loads(tf.read_text())["tasks"] if t["task_id"] == "CAS-2")
    assert task["status"] == "in_progress"
    assert task["preferred_provider"] == "codex"


def test_cas_force_overrides(tmp_path, monkeypatch):
    """--force lets a re-claim through even when already in_progress."""
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "CAS-3", "title": "t", "status": "in_progress", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_claim(["--task", "CAS-3", "--model", "codex", "--force"])  # must not raise
    task = next(t for t in json.loads(tf.read_text())["tasks"] if t["task_id"] == "CAS-3")
    assert task["preferred_provider"] == "codex"


def test_cas_concurrent_claims_exactly_one_wins(tmp_path, monkeypatch):
    """Two concurrent claims on the same pending task → exactly one success."""
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "CAS-RACE", "title": "t", "status": "pending", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    successes = []
    failures = []
    barrier = threading.Barrier(2)

    def worker(model):
        barrier.wait()  # maximize overlap
        try:
            co.cmd_claim(["--task", "CAS-RACE", "--model", model])
            successes.append(model)
        except SystemExit as exc:
            failures.append(exc.code)

    threads = [threading.Thread(target=worker, args=("m{}".format(i),)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1, "exactly one claim must succeed"
    assert len(failures) == 1, "the other claim must be rejected"
    assert failures[0] == 1

    # The lock sidecar must be released regardless of who won.
    assert not Path(str(tf) + ".lock").exists()

    # Final state is owned by the single winner.
    task = next(t for t in json.loads(tf.read_text())["tasks"] if t["task_id"] == "CAS-RACE")
    assert task["status"] == "in_progress"
    assert task["preferred_provider"] == successes[0]
