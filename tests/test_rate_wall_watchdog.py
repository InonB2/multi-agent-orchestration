import argparse
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rate_wall_watchdog as rww  # noqa: E402


def test_should_dispatch_fails_open_when_telemetry_is_stale(monkeypatch, capsys):
    monkeypatch.setattr(rww, "WALL_TELEMETRY_TTL_SECONDS", 300.0)
    monkeypatch.setattr(
        rww,
        "read_codex_windows",
        lambda: {
            "found": True,
            "source": "stale.jsonl",
            "observed_at": 1_700_000_000.0,
            "observed_at_local": "2023-11-14 22:13:20 UTC",
            "telemetry_age_seconds": 9999.0,
            "windows": {
                "primary": {
                    "used_percent": 99.0,
                    "resets_at": 2_000_000_000,
                    "resets_local": "future",
                    "window_minutes": 300,
                },
                "secondary": {
                    "used_percent": 20.0,
                    "resets_at": 2_000_500_000,
                    "resets_local": "future",
                    "window_minutes": 10080,
                },
            },
        },
    )

    rc = rww.cmd_should_dispatch(argparse.Namespace(engine="codex"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "stale" in out.lower()
