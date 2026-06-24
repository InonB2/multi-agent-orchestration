"""Tests for scripts/usage_bridge/usage_bridge_reader.py — honest per-engine usage."""

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "usage_bridge"))

import usage_bridge_reader as ubr  # noqa: E402


def _write_codex_session(tmp_path: Path, primary_pct: float, weekly_pct: float) -> Path:
    root = tmp_path / ".codex" / "sessions" / "2026" / "06" / "23"
    root.mkdir(parents=True)
    f = root / "rollout-2026-06-23T00-54-32-019ef153.jsonl"
    f.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "x"}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": {"total_tokens": 123456}},
                            "rate_limits": {
                                "primary": {
                                    "used_percent": primary_pct,
                                    "resets_at": 1782180780,
                                    "window_minutes": 300,
                                },
                                "secondary": {
                                    "used_percent": weekly_pct,
                                    "resets_at": 1782404147,
                                    "window_minutes": 10080,
                                },
                            },
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path / ".codex" / "sessions"


# --------------------------------------------------------------------------- #
# CODEX: weekly (secondary) window parses + is real
# --------------------------------------------------------------------------- #
def test_codex_reads_both_windows_real(tmp_path):
    sessions_root = _write_codex_session(tmp_path, primary_pct=42.0, weekly_pct=88.0)
    detail = ubr.read_codex_usage(sessions_root)
    assert detail["confidence"] == "real"
    assert detail["window_pct_primary"] == 42.0
    assert detail["window_pct_weekly"] == 88.0  # weekly read parses
    assert detail["tokens"] == 123456
    assert detail["resets"]["weekly_resets_at"] == 1782404147
    assert detail["resets"]["primary_resets_local"] is not None


def test_codex_missing_sessions_is_honest_null(tmp_path):
    detail = ubr.read_codex_usage(tmp_path / "does-not-exist")
    assert detail["confidence"] == "none"
    assert detail["window_pct_primary"] is None
    assert detail["window_pct_weekly"] is None
    assert detail["tokens"] is None


# --------------------------------------------------------------------------- #
# CLAUDE: estimate sums logged tokens AND is clearly labeled
# --------------------------------------------------------------------------- #
def test_claude_estimate_sums_logged_tokens_and_is_labeled(tmp_path):
    log = tmp_path / "ptme_decisions.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "A", "engine": "claude", "actual_tokens": 100}),
                json.dumps({"task_id": "B", "worker_id": "claude-coder", "actual_tokens": 250}),
                json.dumps({"task_id": "C", "engine": "codex", "actual_tokens": 9999}),  # excluded
                json.dumps({"task_id": "D", "engine": "claude", "actual_tokens": None}),  # skipped
            ]
        ),
        encoding="utf-8",
    )
    # Point OTel at a nonexistent path so the logged-token fallback is exercised.
    detail = ubr.read_claude_usage(otel_output_path=tmp_path / "no_otel.json", ptme_log=log)
    assert detail["tokens"] == 350  # 100 + 250 only
    assert detail["confidence"] == "estimated"
    assert detail["window_pct_primary"] is None  # never a fabricated %
    assert detail["window_pct_weekly"] is None
    assert "estimated" in detail["note"].lower()
    assert "not a quota" in detail["note"].lower()


def test_claude_no_logs_is_honest_null(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    detail = ubr.read_claude_usage(otel_output_path=tmp_path / "no_otel.json", ptme_log=empty)
    assert detail["tokens"] is None
    assert detail["confidence"] == "none"
    assert detail["window_pct_primary"] is None


def test_claude_prefers_otel_when_present(tmp_path):
    otel = tmp_path / "otel.json"
    otel.write_text(
        json.dumps(
            {
                "attributes": [
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1000"}},
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": 500}},
                ]
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "ptme.jsonl"
    log.write_text(json.dumps({"engine": "claude", "actual_tokens": 7}), encoding="utf-8")
    detail = ubr.read_claude_usage(otel_output_path=otel, ptme_log=log)
    assert detail["tokens"] == 1500  # from OTel, not the log
    assert detail["confidence"] == "real"


# --------------------------------------------------------------------------- #
# AGY: honest null with documented howto
# --------------------------------------------------------------------------- #
def test_agy_is_honest_null_with_howto():
    detail = ubr.read_agy_usage()
    assert detail["tokens"] is None
    assert detail["window_pct_primary"] is None
    assert detail["confidence"] == "none"
    assert "/usage" in detail["note"] or "AI Studio" in detail["note"]


# --------------------------------------------------------------------------- #
# Resilience: a read failure degrades to honest null, never crashes
# --------------------------------------------------------------------------- #
def test_codex_read_failure_degrades_to_null(tmp_path, monkeypatch):
    sessions_root = _write_codex_session(tmp_path, primary_pct=10.0, weekly_pct=20.0)

    import rate_wall_watchdog

    def _boom(*_a, **_k):
        raise RuntimeError("simulated read failure")

    monkeypatch.setattr(rate_wall_watchdog, "read_codex_windows", _boom)
    detail = ubr.read_codex_usage(sessions_root)
    assert detail["confidence"] == "none"  # honest null, no crash
    assert detail["tokens"] is None
    assert "error" in detail["note"].lower()


def test_claude_read_failure_degrades_to_null(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("simulated otel failure")

    monkeypatch.setattr(ubr, "_claude_tokens_from_otel", _boom)
    detail = ubr.read_claude_usage(otel_output_path=tmp_path / "x.json", ptme_log=tmp_path / "y.jsonl")
    assert detail["confidence"] == "none"
    assert detail["tokens"] is None


def test_read_all_keys_present():
    out = ubr.read_all()
    assert set(out.keys()) == {"codex", "claude", "agy"}
    for engine, detail in out.items():
        for key in ("tokens", "window_pct_primary", "window_pct_weekly", "source", "confidence", "note"):
            assert key in detail, f"{engine} missing {key}"
