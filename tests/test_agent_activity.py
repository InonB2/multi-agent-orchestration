import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_activity as aa  # noqa: E402


def _read_activity(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_activity_js(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_set_marks_agent_running_and_preserves_seed_file_shape(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)

    aa.write_activity(aa.seed_activity_data(), activity_file)
    aa.main([
        "set",
        "--agent", "codex",
        "--model", "gpt-5",
        "--effort", "high",
        "--task", "Wire live overlay",
        "--status", "running",
        "--reason", "Implementing dashboard status feed",
    ])

    payload = _read_activity(activity_file)
    entry = next(item for item in payload["entries"] if item["agent"] == "codex")

    assert entry["status"] == "running"
    assert entry["model"] == "gpt-5"
    assert entry["effort"] == "high"
    assert entry["current_task"] == "Wire live overlay"
    assert entry["reason"] == "Implementing dashboard status feed"
    assert entry["started_at"] is not None
    assert entry["updated_at"] == entry["started_at"]
    assert entry["session_usage_pct"] == 0

    mirrored = _read_activity_js(activity_js_file)
    expected = "window.AGENT_ACTIVITY = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";"
    assert mirrored == expected


def test_clear_resets_agent_to_idle_defaults(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)

    aa.write_activity(aa.seed_activity_data(), activity_file)
    aa.main([
        "set",
        "--agent", "orchestrator",
        "--model", "claude-opus-4.8",
        "--effort", "high",
        "--task", "Route work",
        "--status", "running",
        "--reason", "Dispatching agents",
    ])
    aa.main(["clear", "--agent", "orchestrator"])

    payload = _read_activity(activity_file)
    entry = next(item for item in payload["entries"] if item["agent"] == "orchestrator")

    assert entry["status"] == "idle"
    assert entry["model"] is None
    assert entry["effort"] is None
    assert entry["current_task"] is None
    assert entry["started_at"] is None
    assert entry["reason"] == ""
    assert entry["updated_at"] is not None

    mirrored_json = _read_activity_js(activity_js_file)
    prefix = "window.AGENT_ACTIVITY = "
    mirrored_json = mirrored_json[len(prefix):] if mirrored_json.startswith(prefix) else mirrored_json
    mirrored_json = mirrored_json[:-1] if mirrored_json.endswith(";") else mirrored_json
    mirrored_payload = json.loads(mirrored_json)
    mirrored_entry = next(item for item in mirrored_payload["entries"] if item["agent"] == "orchestrator")
    assert mirrored_entry == entry


def test_list_prints_json_document(tmp_path, monkeypatch, capsys):
    activity_file = tmp_path / "agent_activity.json"
    activity_js_file = tmp_path / "agent_activity.js"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", activity_js_file)

    aa.write_activity(aa.seed_activity_data(), activity_file)
    aa.main(["list"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["_meta"]["schema"] == 1
    assert len(payload["entries"]) == len(aa.SEED_AGENT_IDS)
    assert all(entry["status"] == "idle" for entry in payload["entries"])
