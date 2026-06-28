import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import codex_usage as cu  # noqa: E402


def test_read_latest_usage_from_session_fixture(tmp_path):
    sessions_root = tmp_path / ".codex" / "sessions" / "2026" / "06" / "23"
    sessions_root.mkdir(parents=True)
    session_file = sessions_root / "rollout-2026-06-23T00-45-11-019ef14b-694d-70c2-a825-3d0260372530.jsonl"
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
                                "primary": {"used_percent": 39.0, "window_minutes": 300},
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
                            "duration_ms": 14675,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    usage = cu.read_latest_usage(tmp_path / ".codex" / "sessions")

    assert usage["tokens"] == 17960
    assert usage["duration_ms"] == 14675
    assert usage["window_pct"] == 39.0
    assert usage["source"].endswith(session_file.name)


def test_read_latest_usage_handles_missing_sessions_root():
    usage = cu.read_latest_usage(Path("Z:/definitely/missing/.codex/sessions"))

    assert usage == {
        "tokens": None,
        "duration_ms": None,
        "window_pct": None,
        "source": None,
    }
