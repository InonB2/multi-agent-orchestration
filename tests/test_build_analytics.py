import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import build_analytics as ba  # noqa: E402


def _read_js_payload(path: Path) -> dict:
    source = path.read_text(encoding="utf-8").strip()
    prefix = "window.MMOI_ANALYTICS = "
    assert source.startswith(prefix)
    assert source.endswith(";")
    return json.loads(source[len(prefix):-1])


def test_build_analytics_handles_missing_inputs_honestly(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    output_file = tmp_path / "analytics_data.js"
    logs_dir = tmp_path / "logs"

    tasks_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "MMOI-1",
                        "title": "Seed dashboard work",
                        "status": "in_progress",
                        "assigned_to": "codex",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ba, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ba, "PTME_LOG_FILE", logs_dir / "ptme_decisions.jsonl")
    monkeypatch.setattr(ba, "ACTIVITY_FILE", tmp_path / "missing_activity.json")
    monkeypatch.setattr(ba, "LIVE_TASKS_FILE", tmp_path / "missing_live_tasks.json")
    monkeypatch.setattr(ba, "LESSONS_FILE", tmp_path / "missing_lessons.md")
    monkeypatch.setattr(ba, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(ba, "OUTPUT_FILE", output_file)

    exit_code = ba.main([])
    assert exit_code == 0

    payload = _read_js_payload(output_file)
    assert payload["_meta"]["schema"] == 2
    assert payload["sources"]["tasks_total"] == 1
    assert payload["sources"]["task_status_counts"] == {"in_progress": 1}
    assert payload["sources"]["task_complexity_counts"] == {}
    assert payload["sources"]["tasks_with_complexity"] == 0
    assert payload["sources"]["ptme_decision_count"] == 0
    assert payload["sources"]["usage_log_file_count"] == 0
    assert payload["decisions"]["rows"] == []
    assert payload["decisions"]["empty_state"] == "no PTME decisions logged yet"
    assert payload["runtime"]["tasks"] == []
    assert payload["runtime"]["agents"] == []
    assert payload["live_tasks"]["rows"] == []
    assert payload["live_tasks"]["empty_state"] == "no live tasks recorded yet"
    assert payload["per_agent_usage"]["rows"] == []
    assert payload["learning_loop"]["lessons_count"] == 0
    assert payload["learning_loop"]["last_updated"] is None
    assert payload["learning_loop"]["decision_logging_status"] == "no PTME decisions logged yet"
    assert payload["learning_loop"]["qa_rounds"] == "metric pending — needs more logged runs"
    assert payload["learning_loop"]["rework_trend"] == "metric pending — needs more logged runs"


def test_build_analytics_aggregates_real_runtime_decisions_and_usage(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    activity_file = tmp_path / "agent_activity.json"
    live_tasks_file = tmp_path / "live_tasks.json"
    lessons_file = tmp_path / "AGENT_LESSONS.md"
    output_file = tmp_path / "analytics_data.js"
    logs_dir = tmp_path / "logs"
    usage_log_file = logs_dir / "usage_codex.json"
    logs_dir.mkdir()

    tasks_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_id": "MMOI-1",
                        "title": "Implement analytics",
                        "status": "tested",
                        "complexity": "L",
                        "assigned_to": "codex",
                        "coordinator_log": [
                            "2026-06-22T10:00:00Z CLAIMED by codex",
                            "2026-06-22T10:45:00Z COMPLETE result=ok",
                        ],
                    },
                    {
                        "task_id": "MMOI-2",
                        "title": "Write content",
                        "status": "done",
                        "assigned_to": "agy",
                        "claimed_at": "2026-06-22T11:00:00Z",
                        "completed_at": "2026-06-22T11:15:00Z",
                    },
                    {
                        "task_id": "MMOI-3",
                        "title": "Pre-PTME quick task",
                        "status": "done",
                        "assigned_to": "coder",
                        "claimed_at": "2026-06-22T11:30:00Z",
                        "completed_at": "2026-06-22T11:30:20Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    ptme_log_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-22T09:59:00Z",
                        "received_at": "2026-06-22T09:59:00Z",
                        "task_id": "MMOI-1",
                        "complexity": "L",
                        "score_reasons": ["long text", "complex signals: refactor"],
                        "recommended_model": "gpt-5.3-codex",
                        "recommended_effort": "high",
                        "decided_model": "gpt-5.5",
                        "decided_effort": "high",
                        "decided_by": "codex_sub_orchestrator",
                        "judgment": "overridden",
                        "rationale": "Repo surgery and tests",
                        "reason": "Repo surgery and tests",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-06-22T10:59:00Z",
                        "received_at": "2026-06-22T10:59:00Z",
                        "task_id": "MMOI-2",
                        "complexity": "M",
                        "score_reasons": ["medium-length text"],
                        "recommended_model": "gemini-3.5-flash",
                        "recommended_effort": "medium",
                        "decided_model": "gemini-3.5-flash",
                        "decided_effort": "medium",
                        "decided_by": "agy_sub_orchestrator",
                        "judgment": "accepted",
                        "rationale": "Content and writing fit",
                        "reason": "Content and writing fit",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    activity_file.write_text(
        json.dumps(
            {
                "_meta": {"updated_at": "2026-06-22T12:00:00Z"},
                "entries": [
                    {
                        "agent": "codex",
                        "model": "gpt-5-codex",
                        "effort": "high",
                        "current_task": "Implement analytics",
                        "task_id": "MMOI-1",
                        "status": "running",
                        "started_at": "2026-06-22T10:00:00Z",
                        "updated_at": "2026-06-22T10:30:00Z",
                        "reason": "worker",
                        "usage": {
                            "window_pct": 80,
                            "tokens_used": 1200,
                            "token_budget": 2000,
                            "budget_remaining_tokens": 800,
                            "rate_limit_forecast": "codex ~80% of window",
                        },
                    },
                    {
                        "agent": "agy",
                        "model": None,
                        "effort": None,
                        "current_task": None,
                        "status": "idle",
                        "started_at": None,
                        "updated_at": "2026-06-22T11:15:00Z",
                        "reason": "",
                        "usage": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    live_tasks_file.write_text(
        json.dumps(
            {
                "_meta": {"updated_at": "2026-06-22T12:05:00Z"},
                "entries": [
                    {
                        "task_id": "MMOI-1",
                        "worker_id": "codex-2",
                        "engine": "codex",
                        "status": "done",
                        "model": "gpt-5-codex",
                        "effort": "high",
                        "started_at": "2026-06-22T10:00:00Z",
                        "completed_at": "2026-06-22T10:45:00Z",
                        "updated_at": "2026-06-22T10:45:00Z",
                        "decision_ref": "MMOI-1@2026-06-22T09:59:00Z",
                        "duration_seconds": 2700,
                        "usage": {
                            "duration_seconds": 2700,
                            "label": "45m"
                        },
                    },
                    {
                        "task_id": "MMOI-4",
                        "worker_id": "claude-w1",
                        "engine": "claude",
                        "status": "done",
                        "model": "claude-sonnet-4.6",
                        "effort": "medium",
                        "started_at": "2026-06-22T12:00:00Z",
                        "completed_at": "2026-06-22T12:10:00Z",
                        "updated_at": "2026-06-22T12:10:00Z",
                        "decision_ref": "MMOI-4@2026-06-22T11:59:00Z",
                        "duration_seconds": 600,
                        "usage": {
                            "tokens_used": 1542,
                            "label": "1,542 tokens"
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    usage_log_file.write_text(
        json.dumps(
            {
                "_meta": {"captured_at": "2026-06-22T12:01:00Z"},
                "entries": [
                    {
                        "agent": "codex",
                        "task_id": "MMOI-1",
                        "window_pct": 80,
                        "tokens_used": 1200,
                        "token_budget": 2000,
                        "budget_remaining_tokens": 800,
                        "rate_limit_forecast": "codex ~80% of window",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lessons_file.write_text(
        "\n".join(
            [
                "# Lessons",
                "## Lessons — 2026-06-22",
                "- Checkpoint before teardown.",
                "- Keep file:// data script-backed.",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(ba, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ba, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(ba, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(ba, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(ba, "LESSONS_FILE", lessons_file)
    monkeypatch.setattr(ba, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(ba, "OUTPUT_FILE", output_file)

    exit_code = ba.main([])
    assert exit_code == 0

    payload = _read_js_payload(output_file)
    assert payload["sources"]["tasks_total"] == 3
    assert payload["sources"]["task_status_counts"] == {"done": 2, "tested": 1}
    assert payload["sources"]["task_complexity_counts"] == {"L": 1}
    assert payload["sources"]["tasks_with_complexity"] == 1
    assert payload["sources"]["ptme_decision_count"] == 2
    assert payload["sources"]["running_agents"] == 1
    assert payload["sources"]["usage_log_file_count"] == 1

    assert payload["decisions"]["empty_state"] is None
    assert payload["decisions"]["summary"]["logged_count"] == 2
    assert payload["decisions"]["summary"]["accepted_count"] == 1
    assert payload["decisions"]["summary"]["overridden_count"] == 1

    decisions = {row["task_id"]: row for row in payload["decisions"]["rows"]}
    assert decisions["MMOI-1"]["complexity"] == "L"
    assert decisions["MMOI-1"]["changed"] is True
    assert decisions["MMOI-1"]["judgment"] == "overridden"
    assert decisions["MMOI-1"]["actual_usage_label"] == "45m"
    assert decisions["MMOI-2"]["complexity"] == "M"
    assert decisions["MMOI-2"]["changed"] is False
    assert decisions["MMOI-2"]["judgment"] == "accepted"

    # Complexity mix is ALWAYS ordered S, M, L, XL with all four tiers present.
    mix = payload["decisions"]["summary"]["complexity_mix"]
    assert [item["tier"] for item in mix] == ["S", "M", "L", "XL"]
    assert {item["tier"]: item["count"] for item in mix} == {"S": 0, "M": 1, "L": 1, "XL": 0}
    # decided-model chart has no 'unknown' bucket — only known-family models.
    assert set(payload["decisions"]["summary"]["by_decided_model"]) == {"gpt-5.5", "gemini-3.5-flash"}
    assert payload["decisions"]["legacy"]["count"] == 0

    runtime = {row["task_id"]: row for row in payload["runtime"]["tasks"]}
    assert runtime["MMOI-1"]["runtime_minutes"] == 45
    assert runtime["MMOI-1"]["agent"] == "codex"
    assert runtime["MMOI-2"]["runtime_minutes"] == 15
    assert runtime["MMOI-2"]["agent"] == "agy"
    assert runtime["MMOI-3"]["runtime_minutes"] == 0
    assert runtime["MMOI-3"]["runtime_seconds"] == 20
    assert runtime["MMOI-3"]["runtime_display"] == "<1m"
    assert runtime["MMOI-3"]["pre_ptme"] is True

    live_tasks = {row["task_id"]: row for row in payload["live_tasks"]["rows"]}
    assert live_tasks["MMOI-1"]["worker_id"] == "codex-2"
    assert live_tasks["MMOI-1"]["usage"]["label"] == "45m"
    assert live_tasks["MMOI-4"]["usage"]["tokens_used"] == 1542

    per_agent = {row["agent"]: row for row in payload["per_agent_usage"]["rows"]}
    assert set(per_agent) == {"agy", "codex", "coder"}
    assert per_agent["codex"]["task_count"] == 1
    assert per_agent["codex"]["total_runtime_minutes"] == 45
    assert per_agent["codex"]["model"] == "gpt-5-codex"
    assert per_agent["codex"]["effort"] == "high"
    assert per_agent["codex"]["usage"]["window_pct"] == 80
    assert per_agent["codex"]["usage"]["tokens_used"] == 1200
    assert per_agent["codex"]["usage"]["budget_remaining_tokens"] == 800
    assert per_agent["codex"]["usage"]["rate_limit_forecast"] == "codex ~80% of window"
    assert per_agent["agy"]["task_count"] == 1
    assert per_agent["agy"]["total_runtime_minutes"] == 15
    assert per_agent["agy"]["usage"]["window_pct"] is None
    assert per_agent["coder"]["task_count"] == 1
    assert per_agent["coder"]["total_runtime_minutes"] == 0
    assert per_agent["coder"]["total_runtime_seconds"] == 20
    assert per_agent["coder"]["total_runtime_display"] == "<1m"
    assert per_agent["coder"]["pre_ptme"] is True
    assert per_agent["coder"]["pre_ptme_task_ids"] == ["MMOI-3"]
    assert per_agent["coder"]["model"] is None
    assert per_agent["coder"]["effort"] is None

    assert payload["learning_loop"]["lessons_count"] == 2
    assert payload["learning_loop"]["last_updated"] == "2026-06-22"
    assert payload["learning_loop"]["decision_logging_status"] == "recording"
    assert payload["learning_loop"]["qa_rounds"] == "metric pending — needs more logged runs"
    assert payload["learning_loop"]["rework_trend"] == "metric pending — needs more logged runs"


def test_analytics_excludes_pre_ptme_and_unknown_from_ptme_charts(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    output_file = tmp_path / "analytics_data.js"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    tasks_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    ptme_log_file.write_text(
        "\n".join(
            [
                # PTME-era real decision (known model + provenance).
                json.dumps({
                    "ts": "2026-06-22T09:00:00Z", "received_at": "2026-06-22T09:00:00Z",
                    "task_id": "REAL-1", "complexity": "L",
                    "score_reasons": ["long text"],
                    "recommended_model": "claude-sonnet-4.6", "recommended_effort": "high",
                    "decided_model": "claude-sonnet-4.6", "decided_effort": "high",
                    "judgment": "accepted", "rationale": "engine-scoped",
                }),
                # PTME-era override.
                json.dumps({
                    "ts": "2026-06-22T09:30:00Z", "received_at": "2026-06-22T09:30:00Z",
                    "task_id": "REAL-2", "complexity": "S",
                    "score_reasons": ["very short text"],
                    "recommended_model": "claude-haiku-4.5", "recommended_effort": "low",
                    "decided_model": "claude-opus-4.8", "decided_effort": "high",
                    "judgment": "overridden", "rationale": "risk override",
                }),
                # Legacy: known model but NO provenance (pre-PTME).
                json.dumps({
                    "ts": "2026-06-21T08:00:00Z", "task_id": "LEGACY-1",
                    "recommended_model": "gpt-5.3-codex", "decided_model": "gpt-5.3-codex",
                }),
                # Unknown model -> the 'unknown' bucket source.
                json.dumps({
                    "ts": "2026-06-21T08:30:00Z", "received_at": "2026-06-21T08:30:00Z",
                    "task_id": "UNK-1", "complexity": "S", "score_reasons": ["x"],
                    "recommended_model": "some-unknown-model", "decided_model": "some-unknown-model",
                }),
            ]
        ) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ba, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ba, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(ba, "ACTIVITY_FILE", tmp_path / "missing_activity.json")
    monkeypatch.setattr(ba, "LIVE_TASKS_FILE", tmp_path / "missing_live_tasks.json")
    monkeypatch.setattr(ba, "LESSONS_FILE", tmp_path / "missing_lessons.md")
    monkeypatch.setattr(ba, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(ba, "OUTPUT_FILE", output_file)

    assert ba.main([]) == 0
    payload = _read_js_payload(output_file)
    summary = payload["decisions"]["summary"]

    # Only the 2 PTME-era rows count in the headline.
    assert summary["logged_count"] == 2
    assert summary["accepted_count"] == 1
    assert summary["overridden_count"] == 1
    # No 'unknown' / pre-PTME model in the decided-model chart.
    assert "some-unknown-model" not in summary["by_decided_model"]
    assert "gpt-5.3-codex" not in summary["by_decided_model"]
    assert set(summary["by_decided_model"]) == {"claude-sonnet-4.6", "claude-opus-4.8"}

    # Complexity mix ordered S, M, L, XL, all four present.
    assert [i["tier"] for i in summary["complexity_mix"]] == ["S", "M", "L", "XL"]
    assert {i["tier"]: i["count"] for i in summary["complexity_mix"]} == {"S": 1, "M": 0, "L": 1, "XL": 0}
    # Criteria/thresholds available for the explainer.
    assert summary["complexity_criteria"]["thresholds"][0]["tier"] == "S"

    # Legacy bucket is separate and labelled (2 rows: pre-PTME + unknown).
    assert payload["decisions"]["legacy"]["count"] == 2
    assert "unknown model: some-unknown-model" in payload["decisions"]["legacy"]["by_model"]


def test_analytics_override_shows_in_accepted_vs_overridden(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    output_file = tmp_path / "analytics_data.js"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    tasks_file.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    ptme_log_file.write_text(
        json.dumps({
            "ts": "2026-06-22T09:30:00Z", "received_at": "2026-06-22T09:30:00Z",
            "task_id": "OV-1", "complexity": "S", "score_reasons": ["x"],
            "recommended_model": "claude-haiku-4.5", "decided_model": "claude-opus-4.8",
            "judgment": "overridden", "rationale": "risk: production auth path",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ba, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ba, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(ba, "ACTIVITY_FILE", tmp_path / "a.json")
    monkeypatch.setattr(ba, "LIVE_TASKS_FILE", tmp_path / "l.json")
    monkeypatch.setattr(ba, "LESSONS_FILE", tmp_path / "m.md")
    monkeypatch.setattr(ba, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(ba, "OUTPUT_FILE", output_file)
    assert ba.main([]) == 0
    payload = _read_js_payload(output_file)
    assert payload["decisions"]["summary"]["overridden_count"] == 1
    assert payload["decisions"]["summary"]["accepted_count"] == 0
    row = payload["decisions"]["rows"][0]
    assert row["judgment"] == "overridden"
    assert "risk" in row["rationale"]
