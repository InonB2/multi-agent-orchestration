import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import extract_codex_usage as ecu  # noqa: E402


def test_extract_record_from_exec_jsonl_and_session_file(tmp_path):
    exec_jsonl = tmp_path / "codex_exec.jsonl"
    session_dir = tmp_path / "sessions" / "2026" / "06" / "23"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "rollout-2026-06-23T00-45-11-019ef14b-694d-70c2-a825-3d0260372530.jsonl"

    exec_jsonl.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "019ef14b-694d-70c2-a825-3d0260372530"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "item_0", "type": "agent_message", "text": "OK"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 17827,
                            "cached_input_tokens": 2432,
                            "output_tokens": 133,
                            "reasoning_output_tokens": 126,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-22T21:45:14.159Z",
                        "type": "session_meta",
                        "payload": {"id": "019ef14b-694d-70c2-a825-3d0260372530"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-22T21:45:28.503Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 17827,
                                    "cached_input_tokens": 2432,
                                    "output_tokens": 133,
                                    "reasoning_output_tokens": 126,
                                    "total_tokens": 17960,
                                }
                            },
                            "rate_limits": {
                                "limit_id": "codex",
                                "primary": {"used_percent": 39.0, "window_minutes": 300},
                                "secondary": {"used_percent": 91.0, "window_minutes": 10080},
                                "plan_type": "plus",
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-22T21:45:28.573Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "019ef14b-7180-7223-9e23-b8846bb2de6d",
                            "duration_ms": 14675,
                            "time_to_first_token_ms": 14506,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    record = ecu.extract_record(
        exec_jsonl_path=exec_jsonl,
        sessions_root=tmp_path / "sessions",
        task_id="B5-07",
        worker="codex-2",
    )

    assert record["task_id"] == "B5-07"
    assert record["worker"] == "codex-2"
    assert record["engine"] == "codex"
    assert record["thread_id"] == "019ef14b-694d-70c2-a825-3d0260372530"
    assert record["tokens"] == 17960
    assert record["input_tokens"] == 17827
    assert record["cached_input_tokens"] == 2432
    assert record["output_tokens"] == 133
    assert record["reasoning_output_tokens"] == 126
    assert record["duration_ms"] == 14675
    assert record["ttft_ms"] == 14506
    assert record["window_pct"] == 39.0
    assert record["weekly_window_pct"] == 91.0
    assert record["cost"] is None
    assert record["plan_type"] == "plus"
    assert record["source"].endswith("codex_exec.jsonl")
