from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "scripts" / "dispatch.py"


def run_dispatch(*args: str, stdin: str = "", env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DISPATCH), *args],
        cwd=ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=15,
        env=env,
    )


def test_guide_adapter_runs_end_to_end_with_preflight_and_redacted_telemetry(tmp_path):
    workdir = tmp_path / "workspace with spaces"
    workdir.mkdir()
    telemetry = tmp_path / "dispatch.jsonl"
    config = json.loads((ROOT / "aoa.config.json").read_text(encoding="utf-8"))
    config["dispatch"]["dashboard_telemetry"] = False
    config_path = tmp_path / "aoa.config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    env = os.environ.copy()
    env["AOA_CONFIG"] = str(config_path)
    secret_prompt = "cold path private prompt"

    result = run_dispatch(
        "--engine", "example_echo",
        "--workdir", str(workdir),
        "--task-id", "COLD-1",
        "--role", "worker",
        "--telemetry-path", str(telemetry),
        stdin=secret_prompt,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "EXAMPLE_ECHO:" + secret_prompt
    events = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["start", "success"]
    assert all(secret_prompt not in json.dumps(event) for event in events)


def test_guide_adapter_is_discoverable_and_dry_run_is_prompt_free():
    listed = run_dispatch("--list-adapters")
    assert listed.returncode == 0
    assert "example_echo\tadapters.example_echo\texample" in listed.stdout

    dry = run_dispatch(
        "--engine", "example_echo", "--prompt", "do not leak", "--dry-run"
    )
    assert dry.returncode == 0
    assert "do not leak" not in dry.stdout
    assert "<redacted>" in dry.stdout
