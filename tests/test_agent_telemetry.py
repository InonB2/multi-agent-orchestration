import json
import multiprocessing
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_activity as aa  # noqa: E402
import agent_telemetry as at  # noqa: E402
import telemetry_refresh as tr  # noqa: E402


def _read_doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _start_agent_process(activity_path: str, agent: str) -> None:
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import agent_telemetry as worker_at

    json_path = Path(activity_path)
    worker_at.JSON_PATH = json_path
    worker_at.JS_PATH = json_path.with_suffix(".js")
    worker_at.cmd_start(
        SimpleNamespace(
            agent=agent,
            task=f"TASK-{agent}",
            desc=f"Run {agent}",
            model="gpt-5.4",
            effort="high",
            role=agent.split("-", 1)[1],
            reason="dispatched",
        )
    )


def _stop_agent_process(activity_path: str, agent: str) -> None:
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import agent_telemetry as worker_at

    json_path = Path(activity_path)
    worker_at.JSON_PATH = json_path
    worker_at.JS_PATH = json_path.with_suffix(".js")
    worker_at.cmd_stop(SimpleNamespace(agent=agent, status="done", reason=None))


def test_agent_telemetry_start_uses_sidecar_lock(tmp_path, monkeypatch):
    json_path = tmp_path / "agent_activity.json"
    js_path = tmp_path / "agent_activity.js"
    monkeypatch.setattr(at, "JSON_PATH", str(json_path))
    monkeypatch.setattr(at, "JS_PATH", str(js_path))

    call_count = 0

    def fake_locked_update(path, mutate_fn, *, load_fn, save_fn, timeout=10, ttl=300):
        nonlocal call_count
        del timeout, ttl
        call_count += 1
        payload = load_fn(Path(path))
        updated = mutate_fn(payload)
        if updated is not None:
            save_fn(updated)
        return updated

    assert hasattr(
        at, "sidecar_lock"
    ), "agent_telemetry must use sidecar_lock.locked_update for whole-file updates"
    monkeypatch.setattr(at.sidecar_lock, "locked_update", fake_locked_update)

    at.cmd_start(
        SimpleNamespace(
            agent="codex-w1",
            task="TASK-1",
            desc="Telemetry test",
            model="gpt-5.4",
            effort="high",
            role="w1",
            reason="dispatched",
        )
    )

    assert call_count == 1
    doc = _read_doc(json_path)
    entry = next(item for item in doc["entries"] if item["agent"] == "codex-w1")
    assert entry["status"] == "running"


def test_agent_telemetry_concurrent_burst_preserves_valid_json_and_rows(tmp_path, monkeypatch):
    json_path = tmp_path / "agent_activity.json"
    js_path = tmp_path / "agent_activity.js"
    monkeypatch.setattr(at, "JSON_PATH", str(json_path))
    monkeypatch.setattr(at, "JS_PATH", str(js_path))

    aa.write_activity(aa.seed_activity_data(), json_path)

    agents = ["codex-w1", "codex-w2", "agy-w1", "agy-w2", "agy-w3"]
    failures: list[str] = []
    stop_reader = threading.Event()

    def reader() -> None:
        while not stop_reader.is_set():
            if json_path.exists():
                try:
                    _read_doc(json_path)
                except json.JSONDecodeError as exc:
                    failures.append(str(exc))
                    stop_reader.set()
                    return
            time.sleep(0.005)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    with ProcessPoolExecutor(
        max_workers=len(agents),
        mp_context=multiprocessing.get_context("spawn"),
    ) as pool:
        futures = [
            pool.submit(_start_agent_process, str(json_path), agent)
            for agent in agents
        ]
        for future in futures:
            future.result(timeout=30)

    running_doc = _read_doc(json_path)
    running_entries = {
        entry["agent"]: entry
        for entry in running_doc["entries"]
        if entry["agent"] in agents
    }
    assert set(running_entries) == set(agents)
    assert all(entry["status"] == "running" for entry in running_entries.values())

    with ProcessPoolExecutor(
        max_workers=len(agents),
        mp_context=multiprocessing.get_context("spawn"),
    ) as pool:
        futures = [
            pool.submit(_stop_agent_process, str(json_path), agent)
            for agent in agents
        ]
        for future in futures:
            future.result(timeout=30)

    stop_reader.set()
    reader_thread.join()

    assert failures == []

    idle_doc = _read_doc(json_path)
    idle_entries = {
        entry["agent"]: entry
        for entry in idle_doc["entries"]
        if entry["agent"] in agents
    }
    assert set(idle_entries) == set(agents)
    assert all(entry["status"] == "idle" for entry in idle_entries.values())
    assert all(entry["task_id"] is None for entry in idle_entries.values())


def test_agent_telemetry_heartbeat_unknown_agent_exits_cleanly(tmp_path, monkeypatch, capsys):
    json_path = tmp_path / "agent_activity.json"
    js_path = tmp_path / "agent_activity.js"
    monkeypatch.setattr(at, "JSON_PATH", json_path)
    monkeypatch.setattr(at, "JS_PATH", js_path)
    aa.write_activity(aa.seed_activity_data(), json_path)

    with pytest.raises(SystemExit) as exc:
        at.cmd_heartbeat(SimpleNamespace(agent="codex-missing", reason=None))

    assert exc.value.code == 1
    assert "use start first" in capsys.readouterr().err.lower()


def test_telemetry_refresh_uses_sidecar_lock_for_agent_activity(tmp_path, monkeypatch, capsys):
    json_path = tmp_path / "agent_activity.json"
    js_path = tmp_path / "agent_activity.js"
    monkeypatch.setattr(tr, "JSON_PATH", str(json_path))
    monkeypatch.setattr(tr, "JS_PATH", str(js_path))
    monkeypatch.setattr(tr, "CANONICAL", {"codex-qa"})
    monkeypatch.setattr(tr, "LEGACY", set())
    monkeypatch.setattr(tr, "_usage_by_engine", lambda: {})
    monkeypatch.setattr(tr, "_codex_usage_pct", lambda: None)
    monkeypatch.setattr(tr, "_refresh_secondary_feeds", lambda: None)

    payload = {
        "_meta": {"updated_at": "2000-01-01T00:00:00Z"},
        "entries": [
            {
                **aa.idle_entry("codex-qa", updated_at="2000-01-01T00:00:00Z"),
                "status": "running",
                "current_task": "Old task",
                "task_id": "TASK-1",
            }
        ],
    }
    aa.write_activity(payload, json_path)

    call_count = 0

    def fake_locked_update(path, mutate_fn, *, load_fn, save_fn, timeout=10, ttl=300):
        nonlocal call_count
        del timeout, ttl
        call_count += 1
        current = load_fn(Path(path))
        updated = mutate_fn(current)
        if updated is not None:
            save_fn(updated, Path(path))
        return updated

    monkeypatch.setattr(tr.sidecar_lock, "locked_update", fake_locked_update)

    tr.main()

    assert call_count == 1
    refreshed = _read_doc(json_path)
    entry = refreshed["entries"][0]
    assert entry["agent"] == "codex-qa"
    assert entry["status"] == "idle"
    assert entry["current_task"] is None
    assert "telemetry refreshed" in capsys.readouterr().out.lower()
