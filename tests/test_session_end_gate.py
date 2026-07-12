import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import session_end_gate as gate  # noqa: E402


def test_snapshot_task_uses_upstream_checkpoint_command(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint.py"
    checkpoint.write_text("# test\n", encoding="utf-8")
    monkeypatch.setattr(gate, "CHECKPOINT_SCRIPT", checkpoint)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    gate.snapshot_task("AOA-1")

    assert calls == [
        (
            [
                sys.executable,
                str(checkpoint),
                "save",
                "--task",
                "AOA-1",
                "--interrupted-by",
                "session-end-gate",
            ],
            {"timeout": 5, "capture_output": True},
        )
    ]
