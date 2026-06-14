"""
tests/test_status_transitions.py — ORCH-17 coordinator status-transition guard.

Covers:
  * the pure validators (validate_status_transition / validate_phase)
  * the mandatory 'tested' gate before 'done' (mark-done rejection)
  * the --force escape hatch
  * backward-compat: legacy/unknown current statuses WARN, never crash
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import coordinator as co  # noqa: E402
from coordinator import (  # noqa: E402
    validate_status_transition,
    validate_phase,
    VALID_STATUSES,
    ILLEGAL_STATUS_TRANSITIONS,
)


def _make_tasks_file(tmp_path, tasks):
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")
    return tf


# ---------------------------------------------------------------------------
# Pure validators
# ---------------------------------------------------------------------------

def test_validate_legal_transition_returns_none():
    assert validate_status_transition("tested", "done") is None
    assert validate_status_transition("in_progress", "tested") is None
    assert validate_status_transition("backlog", "in_progress") is None


def test_validate_illegal_transition_returns_message():
    for old, new in ILLEGAL_STATUS_TRANSITIONS:
        msg = validate_status_transition(old, new)
        assert msg is not None
        assert "tested" in msg


def test_validate_unknown_target_status_rejected():
    msg = validate_status_transition("in_progress", "bogus")
    assert msg is not None
    assert "Invalid target status" in msg


def test_validate_unknown_old_status_is_lenient():
    # legacy/unknown CURRENT status -> a valid status is not an illegal pair
    assert validate_status_transition("legacy-weird", "in_progress") is None


def test_validate_phase_known_and_unknown():
    assert validate_phase("implementing") is None
    assert validate_phase("not-a-phase") is not None
    # force silences the unknown-phase complaint
    assert validate_phase("not-a-phase", force=True) is None


# ---------------------------------------------------------------------------
# mark-done enforces the 'tested' gate
# ---------------------------------------------------------------------------

def test_mark_done_blocked_from_in_progress(tmp_path, monkeypatch):
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "GATE-001", "title": "t", "status": "in_progress"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    with pytest.raises(SystemExit) as exc:
        co.cmd_mark_done(["--task", "GATE-001"])
    assert exc.value.code == 1

    # status unchanged — still in_progress (atomic guard, no partial write)
    data = json.loads(tf.read_text(encoding="utf-8"))
    assert data["tasks"][0]["status"] == "in_progress"


def test_mark_done_allowed_from_tested(tmp_path, monkeypatch):
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "GATE-002", "title": "t", "status": "tested"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_mark_done(["--task", "GATE-002"])

    data = json.loads(tf.read_text(encoding="utf-8"))
    assert data["tasks"][0]["status"] == "done"


def test_mark_done_force_overrides_gate(tmp_path, monkeypatch):
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "GATE-003", "title": "t", "status": "in_progress"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    # --force lets it through despite skipping 'tested'
    co.cmd_mark_done(["--task", "GATE-003", "--force"])

    data = json.loads(tf.read_text(encoding="utf-8"))
    assert data["tasks"][0]["status"] == "done"


def test_mark_done_lock_released_on_rejection(tmp_path, monkeypatch):
    """A rejected transition must still release the sidecar lock (finally clause)."""
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "GATE-004", "title": "t", "status": "blocked"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    with pytest.raises(SystemExit):
        co.cmd_mark_done(["--task", "GATE-004"])

    lock_path = Path(str(tf) + ".lock")
    assert not lock_path.exists(), "lock must be released even when the guard rejects"


# ---------------------------------------------------------------------------
# Backward-compat: legacy/unknown current status WARNS but does not crash
# ---------------------------------------------------------------------------

def test_legacy_status_does_not_crash_claim(tmp_path, monkeypatch, capsys):
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "LEG-001", "title": "t", "status": "in-review-legacy"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_claim(["--task", "LEG-001", "--model", "codex"])

    data = json.loads(tf.read_text(encoding="utf-8"))
    assert data["tasks"][0]["status"] == "in_progress"
    err = capsys.readouterr().err
    assert "legacy/unknown current status" in err


def test_update_unknown_phase_warns_not_crashes(tmp_path, monkeypatch, capsys):
    tf = _make_tasks_file(tmp_path, [
        {"task_id": "LEG-002", "title": "t", "status": "in_progress"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    # 'in-review' is a legacy phase used in real files — must still be accepted
    co.cmd_update(["--task", "LEG-002", "--phase", "in-review"])

    data = json.loads(tf.read_text(encoding="utf-8"))
    assert data["tasks"][0]["phase"] == "in-review"
    assert "Unknown phase" in capsys.readouterr().err


def test_valid_statuses_cover_kanban():
    for s in ("in_progress", "blocked", "tested", "done"):
        assert s in VALID_STATUSES
