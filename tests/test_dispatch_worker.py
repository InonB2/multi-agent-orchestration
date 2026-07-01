from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_activity as aa  # noqa: E402
import dispatch_worker as dw  # noqa: E402
import ptme  # noqa: E402


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_start_logs_decision_sets_activity_and_writes_live_task(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    live_tasks_file = tmp_path / "live_tasks.json"
    live_tasks_js_file = tmp_path / "live_tasks.js"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"

    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", live_tasks_js_file)
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)

    started = dw.start(
        worker_id="legacy-id",
        engine="codex",
        role="qa",
        task_id="B5-07",
        task_text="Refactor dashboard render and verify UTC elapsed parsing.",
        recommend_model="gpt-5.5",
        recommend_effort="high",
    )

    assert started["task_id"] == "B5-07"
    assert started["worker_id"] == "codex-qa"
    assert started["engine"] == "codex"
    assert started["role"] == "qa"
    assert started["status"] == "running"
    assert started["model"] == "gpt-5.5"
    assert started["effort"] == "high"
    assert started["decision_ref"]

    activity_payload = _read_json(activity_file)
    activity_entry = next(item for item in activity_payload["entries"] if item["agent"] == "codex-qa")
    assert activity_entry["status"] == "running"
    assert activity_entry["task_id"] == "B5-07"
    assert activity_entry["model"] == "gpt-5.5"
    assert activity_entry["effort"] == "high"
    assert activity_entry["role"] == "qa"
    assert activity_entry["engine"] == "codex"

    live_tasks_payload = _read_json(live_tasks_file)
    live_entry = next(item for item in live_tasks_payload["entries"] if item["task_id"] == "B5-07")
    assert live_entry["worker_id"] == "codex-qa"
    assert live_entry["role"] == "qa"
    assert live_entry["decision_ref"] == started["decision_ref"]
    assert live_entry["recommended_model"] == "gpt-5.5"
    assert live_entry["recommended_effort"] == "high"

    ptme_rows = _read_jsonl(ptme_log_file)
    assert len(ptme_rows) == 1
    assert ptme_rows[0]["task_id"] == "B5-07"
    assert ptme_rows[0]["recommended_model"] == "gpt-5.5"
    assert ptme_rows[0]["role"] == "qa"
    assert ptme_rows[0]["worker_id"] == "codex-qa"
    assert ptme_rows[0]["engine"] == "codex"

    js_source = live_tasks_js_file.read_text(encoding="utf-8")
    assert js_source.startswith("window.LIVE_TASKS = ")


def test_complete_marks_task_done_clears_activity_and_records_explicit_usage(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    live_tasks_file = tmp_path / "live_tasks.json"
    live_tasks_js_file = tmp_path / "live_tasks.js"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"

    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", live_tasks_js_file)
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)

    timestamps = iter([
        "2026-06-23T10:00:00Z",
        "2026-06-23T10:00:00Z",
        "2026-06-23T10:05:00Z",
        "2026-06-23T10:05:00Z",
        "2026-06-23T10:05:00Z",
    ])
    monkeypatch.setattr(aa, "now_iso", lambda: next(timestamps))
    monkeypatch.setattr(dw, "now_iso", lambda: next(timestamps))

    dw.start(
        worker_id="claude-tester",
        engine="claude",
        role="qa",
        task_id="QA-101",
        task_text="QA the dashboard redesign and capture real token usage.",
        recommend_model="claude-sonnet-4.6",
        recommend_effort="medium",
    )
    completed = dw.complete(
        worker_id="claude-qa",
        task_id="QA-101",
        status="done",
        usage_tokens=1542,
        duration_ms=4200,
        window_pct=None,
    )

    assert completed["status"] == "done"
    assert completed["completed_at"] == "2026-06-23T10:05:00Z"
    assert completed["duration_seconds"] == 4
    assert completed["usage"]["tokens_used"] == 1542
    assert completed["usage"]["duration_ms"] == 4200
    assert completed["usage"]["label"] == "1,542 tokens"

    activity_payload = _read_json(activity_file)
    activity_entry = next(item for item in activity_payload["entries"] if item["agent"] == "claude-qa")
    assert activity_entry["status"] == "idle"
    assert activity_entry["task_id"] is None

    live_tasks_payload = _read_json(live_tasks_file)
    live_entry = next(item for item in live_tasks_payload["entries"] if item["task_id"] == "QA-101")
    assert live_entry["status"] == "done"
    assert live_entry["duration_seconds"] == 4
    assert live_entry["usage"]["tokens_used"] == 1542
    assert live_entry["usage"]["duration_ms"] == 4200
    assert live_entry["role"] == "qa"

    usage_rows = _read_jsonl(usage_log_file)
    assert usage_rows == [
        {
            "task_id": "QA-101",
            "worker": "claude-qa",
            "engine": "claude",
            "role": "qa",
            "tokens": 1542,
            "duration_ms": 4200,
            "window_pct": None,
            "ts": "2026-06-23T10:05:00Z",
        }
    ]


def test_complete_auto_pulls_codex_usage_when_not_provided(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    live_tasks_file = tmp_path / "live_tasks.json"
    live_tasks_js_file = tmp_path / "live_tasks.js"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"

    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", live_tasks_js_file)
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)

    timestamps = iter([
        "2026-06-23T11:00:00Z",
        "2026-06-23T11:00:00Z",
        "2026-06-23T11:00:20Z",
        "2026-06-23T11:00:20Z",
        "2026-06-23T11:00:20Z",
    ])
    monkeypatch.setattr(aa, "now_iso", lambda: next(timestamps))
    monkeypatch.setattr(dw, "now_iso", lambda: next(timestamps))
    monkeypatch.setattr(
        dw.codex_usage,
        "read_latest_usage",
        lambda sessions_root=None: {
            "tokens": 17960,
            "duration_ms": 14675,
            "window_pct": 39.0,
        },
    )

    dw.start(
        worker_id="codex-anything",
        engine="codex",
        role="coder",
        task_id="CODEX-201",
        task_text="Implement the live dashboard worker usage plumbing.",
        recommend_model="gpt-5.5",
        recommend_effort="high",
    )

    completed = dw.complete(
        worker_id="codex-coder",
        task_id="CODEX-201",
        status="done",
    )

    assert completed["duration_seconds"] == 14
    assert completed["usage"]["tokens_used"] == 17960
    assert completed["usage"]["duration_ms"] == 14675
    assert completed["usage"]["window_pct"] == 39.0

    usage_rows = _read_jsonl(usage_log_file)
    assert usage_rows == [
        {
            "task_id": "CODEX-201",
            "worker": "codex-coder",
            "engine": "codex",
            "role": "coder",
            "tokens": 17960,
            "duration_ms": 14675,
            "window_pct": 39.0,
            "ts": "2026-06-23T11:00:20Z",
        }
    ]


def _setup_paths(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    live_tasks_file = tmp_path / "live_tasks.json"
    live_tasks_js_file = tmp_path / "live_tasks.js"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", live_tasks_js_file)
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)
    return live_tasks_file


def test_complete_closes_live_task_and_sets_worker(tmp_path, monkeypatch):
    live_tasks_file = _setup_paths(tmp_path, monkeypatch)
    dw.start(
        worker_id="claude-coder",
        engine="claude",
        role="coder",
        task_id="CLOSE-1",
        task_text="Implement the close logic and verify it.",
    )
    # Before completion the record is running.
    pre = _read_json(live_tasks_file)
    entry = next(e for e in pre["entries"] if e["task_id"] == "CLOSE-1")
    assert entry["status"] == "running"

    completed = dw.complete(worker_id="claude-coder", task_id="CLOSE-1", status="done")
    assert completed["status"] == "done"
    # worker populated on BOTH worker and worker_id; record closed with real times.
    assert completed["worker"] == "claude-coder"
    assert completed["worker_id"] == "claude-coder"
    assert completed["completed_at"] is not None
    assert completed["finished_at"] == completed["completed_at"]

    after = _read_json(live_tasks_file)
    entry = next(e for e in after["entries"] if e["task_id"] == "CLOSE-1")
    assert entry["status"] == "done"
    assert entry["worker"] == "claude-coder"
    # no record left stuck at running
    assert not any(e["status"] == "running" for e in after["entries"])


def test_stale_running_record_is_expired(tmp_path, monkeypatch):
    live_tasks_file = _setup_paths(tmp_path, monkeypatch)
    # Seed a phantom running record whose worker is NOT running in activity.
    payload = dw.seed_live_tasks_data()
    payload["entries"].append(
        {
            "task_id": "PHANTOM-1",
            "worker_id": "agy-researcher",
            "worker": "agy-researcher",
            "engine": "agy",
            "role": "researcher",
            "status": "running",
            "started_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:00:00Z",
            "completed_at": None,
        }
    )
    dw.write_live_tasks(payload, live_tasks_file)

    # agent_activity has no running entry for agy-researcher -> stale -> done.
    expired = dw.expire_stale_live_tasks(payload, now="2026-06-23T01:00:00Z")
    assert expired == 1
    entry = next(e for e in payload["entries"] if e["task_id"] == "PHANTOM-1")
    assert entry["status"] == "done"
    assert entry["stale"] is True
    assert entry["completed_at"] is not None


def test_stale_guard_runs_on_start_closing_phantoms(tmp_path, monkeypatch):
    live_tasks_file = _setup_paths(tmp_path, monkeypatch)
    # Phantom from a dead worker.
    payload = dw.read_live_tasks(live_tasks_file)
    payload["entries"].append(
        {
            "task_id": "PHANTOM-2",
            "worker_id": "codex-coder",
            "worker": "codex-coder",
            "engine": "codex",
            "role": "coder",
            "status": "running",
            "started_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:00:00Z",
            "completed_at": None,
        }
    )
    dw.write_live_tasks(payload, live_tasks_file)

    # Starting a DIFFERENT real task must not leave the phantom running.
    dw.start(
        worker_id="claude-coder",
        engine="claude",
        role="coder",
        task_id="REAL-1",
        task_text="Do real work and verify.",
    )
    after = _read_json(live_tasks_file)
    phantom = next(e for e in after["entries"] if e["task_id"] == "PHANTOM-2")
    assert phantom["status"] == "done"
    real = next(e for e in after["entries"] if e["task_id"] == "REAL-1")
    assert real["status"] == "running"  # the freshly started one stays running
