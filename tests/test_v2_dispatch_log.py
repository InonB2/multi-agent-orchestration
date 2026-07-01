from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import v2_dispatch_log as vdl  # noqa: E402


def _read_lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_log_command_appends_one_complete_jsonl_record(tmp_path, monkeypatch):
    log_file = tmp_path / "v2_dispatch_log.jsonl"
    monkeypatch.setattr(vdl, "LOG_FILE", log_file)

    vdl.main([
        "log",
        "--task-id", "MMOI-201",
        "--summary", "Route a dashboard bugfix",
        "--recommended-model", "claude-opus-4.8",
        "--recommended-effort", "high",
        "--recommended-by", "root",
        "--decided-model", "gpt-5",
        "--decided-effort", "medium",
        "--decided-by", "local-orchestrator",
        "--reason", "Codex is better for surgical repo edits",
    ])

    records = _read_lines(log_file)
    assert len(records) == 1
    assert records[0]["task_id"] == "MMOI-201"
    assert records[0]["summary"] == "Route a dashboard bugfix"
    assert records[0]["recommended_model"] == "claude-opus-4.8"
    assert records[0]["decided_model"] == "gpt-5"
    assert records[0]["reason"] == "Codex is better for surgical repo edits"
    assert records[0]["timestamp"]


def test_log_command_is_append_only(tmp_path, monkeypatch):
    log_file = tmp_path / "v2_dispatch_log.jsonl"
    monkeypatch.setattr(vdl, "LOG_FILE", log_file)

    vdl.main([
        "log",
        "--task-id", "MMOI-201",
        "--summary", "First decision",
        "--recommended-model", "claude-opus-4.8",
        "--recommended-effort", "high",
        "--recommended-by", "root",
        "--decided-model", "gpt-5",
        "--decided-effort", "medium",
        "--decided-by", "local-orchestrator",
        "--reason", "First pass",
    ])
    vdl.main([
        "log",
        "--task-id", "MMOI-202",
        "--summary", "Second decision",
        "--recommended-model", "claude-sonnet",
        "--recommended-effort", "low",
        "--recommended-by", "root",
        "--decided-model", "claude-sonnet",
        "--decided-effort", "low",
        "--decided-by", "local-orchestrator",
        "--reason", "Recommendation accepted",
    ])

    records = _read_lines(log_file)
    assert [record["task_id"] for record in records] == ["MMOI-201", "MMOI-202"]
