from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dispatch  # noqa: E402
from adapters.base import DispatchRequest, Invocation  # noqa: E402
from adapters.agy import Adapter as AgyAdapter  # noqa: E402
from adapters.claude import Adapter as ClaudeAdapter  # noqa: E402
from adapters.codex import Adapter as CodexAdapter  # noqa: E402

FAKE = Path(__file__).parent / "fixtures" / "fake_agent_cli.py"


class FakeAdapter:
    engine = "fake"

    def build(self, request, executable, scripts_dir):
        env = os.environ.copy()
        env.update(getattr(request, "test_env", {}) or {})
        return Invocation([sys.executable, str(FAKE)], request.prompt,
                          ["<fake-cli>"], env)

    def parse(self, stdout, stderr):
        return stdout.strip()

    def probe_prompt(self):
        return ("health", "FAKE_OK:health")


def req(tmp_path, prompt="hello", timeout=3):
    return DispatchRequest("fake", prompt, tmp_path, timeout)


@pytest.fixture
def fake_core(monkeypatch):
    monkeypatch.setattr(dispatch, "_load_adapter", lambda config, engine, **kwargs: (FakeAdapter(), {"module": "fake"}))
    monkeypatch.setattr(dispatch, "_resolve_executable", lambda config, engine, entry: sys.executable)
    monkeypatch.setattr(dispatch, "_configured_executable", lambda config, engine, entry: sys.executable)


def run_fake(monkeypatch, tmp_path, mode="success", **kwargs):
    monkeypatch.setenv("AOA_FAKE_MODE", mode)
    telemetry = tmp_path / "telemetry.jsonl"
    result = dispatch.dispatch_request(req(tmp_path, **kwargs), task_id="T-1", role="qa",
                                       preflight=False, telemetry_path=telemetry, config={})
    return result, telemetry


def test_stdin_and_workdir_with_spaces(fake_core, monkeypatch, tmp_path):
    workdir = tmp_path / "directory with spaces"
    workdir.mkdir()
    result, telemetry = run_fake(monkeypatch, workdir, prompt="stdin payload")
    assert result.exit_code == 0
    assert result.message == "FAKE_OK:stdin payload"
    assert "stdin payload" not in telemetry.read_text(encoding="utf-8")


def test_nonzero_is_truthful(fake_core, monkeypatch, tmp_path):
    result, _ = run_fake(monkeypatch, tmp_path, mode="nonzero")
    assert result.exit_code == 17
    assert result.status == "failure"


def test_timeout_is_124_and_records_lifecycle(fake_core, monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("AOA_FAKE_CHILD_PID", str(pid_file))
    result, telemetry = run_fake(monkeypatch, tmp_path, mode="timeout", timeout=1)
    assert result.exit_code == 124
    assert result.status == "timeout"
    events = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["start", "timeout"]
    assert all("prompt" not in event and "stdout" not in event and "stderr" not in event
               and "workdir" not in event for event in events)
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    import time
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except (OSError, SystemError):
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"orphan child still alive: {child_pid}")


def test_success_and_failure_telemetry_and_capture(fake_core, monkeypatch, tmp_path):
    success, success_log = run_fake(monkeypatch, tmp_path, prompt="captured")
    assert success.stdout.strip() == "FAKE_OK:captured"
    assert success.stderr == ""
    assert [json.loads(line)["event"] for line in success_log.read_text().splitlines()] == ["start", "success"]
    success_log.unlink()
    failed, failure_log = run_fake(monkeypatch, tmp_path, mode="nonzero")
    assert "fake failure" in failed.stderr
    assert [json.loads(line)["event"] for line in failure_log.read_text().splitlines()] == ["start", "failure"]


def test_missing_executable_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch, "_load_adapter", lambda config, engine, **kwargs: (FakeAdapter(), {"module": "fake"}))
    monkeypatch.setattr(dispatch, "_resolve_executable", lambda config, engine, entry: "")
    telemetry = tmp_path / "missing.jsonl"
    result = dispatch.dispatch_request(req(tmp_path), preflight=False, config={},
                                       telemetry_path=telemetry)
    assert result.exit_code == 2
    assert result.status == "missing_executable"
    events = [json.loads(line)["event"] for line in telemetry.read_text().splitlines()]
    assert events == ["start", "failure"]


def test_dry_run_needs_no_installed_cli_and_redacts(fake_core, monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch, "_resolve_executable", lambda *args: pytest.fail("must not resolve"))
    result = dispatch.dispatch_request(req(tmp_path, prompt="TOP SECRET"), dry_run=True, config={})
    assert result.exit_code == 0
    assert "TOP SECRET" not in result.message
    assert "<redacted>" in result.message


def test_preflight_auth_failure(fake_core, monkeypatch, tmp_path):
    monkeypatch.setenv("AOA_FAKE_MODE", "nonzero")
    telemetry = tmp_path / "auth.jsonl"
    result = dispatch.dispatch_request(req(tmp_path), preflight=True, config={},
                                       telemetry_path=telemetry)
    assert result.exit_code == 2
    assert result.status == "preflight_failed"
    text = telemetry.read_text(encoding="utf-8")
    assert [json.loads(line)["event"] for line in text.splitlines()] == ["start", "failure"]
    assert "hello" not in text and "fake failure" not in text


@pytest.mark.parametrize("adapter", [CodexAdapter(), ClaudeAdapter(), AgyAdapter()])
def test_shipped_adapters_keep_prompt_out_of_outer_argv(adapter, tmp_path):
    request = DispatchRequest(adapter.engine, "private prompt", tmp_path, 10)
    invocation = adapter.build(request, "agent-cli", SCRIPTS)
    assert "private prompt" not in invocation.argv
    assert invocation.stdin == "private prompt"


def test_adapter_registration_is_config_driven(monkeypatch, tmp_path):
    config = {"cli": {"python": sys.executable}, "dispatch": {"adapters": {
        "stub": {"module": "adapters.stub", "cli_key": "python", "enabled": True}
    }}}
    request = DispatchRequest("stub", "hello", tmp_path, 3)
    result = dispatch.dispatch_request(request, preflight=False, config=config)
    assert result.exit_code == 0
    assert result.message == "AOA_STUB_OK"


def test_disabled_adapter_cannot_dispatch_but_can_be_inspected_by_dry_run(tmp_path):
    config = {"cli": {"python": sys.executable}, "dispatch": {"adapters": {
        "stub": {"module": "adapters.stub", "cli_key": "python", "enabled": False}
    }}}
    request = DispatchRequest("stub", "private", tmp_path, 3)

    with pytest.raises(ValueError, match="disabled"):
        dispatch.dispatch_request(request, preflight=False, config=config)

    dry = dispatch.dispatch_request(request, dry_run=True, config=config)
    assert dry.status == "dry_run"
    assert "private" not in dry.message


def test_codex_parser_last_agent_message():
    stream = '\n'.join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "one"}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "two"}}),
    ])
    assert CodexAdapter().parse(stream, "") == "two"
