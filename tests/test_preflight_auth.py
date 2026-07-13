"""
tests/test_preflight_auth.py — sequential live CLI preflight probes.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import llm_provider as lp  # noqa: E402
import preflight_auth as pa  # noqa: E402


DEFAULTS_TOML = """\
[agent]
preferred_model = "claude-code"

[provider]
type = "cli"
adapter = "claude"
"""

CODEX_TOML = """\
[agent]
preferred_model = "codex"

[provider]
type          = "cli"
adapter       = "codex"
cli_exec_args = ["exec"]
"""

AGY_TOML = """\
[agent]
preferred_model = "antigravity"

[provider]
type    = "cli"
adapter = "agy"
cli_cmd = "agy"

[provider.complexity_mapping.S]
model = "gemini-3.5-flash"
"""


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "_defaults.toml").write_text(DEFAULTS_TOML, encoding="utf-8")
    (d / "codex.toml").write_text(CODEX_TOML, encoding="utf-8")
    (d / "antigravity.toml").write_text(AGY_TOML, encoding="utf-8")

    monkeypatch.setattr(lp, "CONFIG_DIR", d)
    monkeypatch.setattr(lp, "DEFAULTS", d / "_defaults.toml")
    return d


def test_probe_spawns_real_cli_not_llm_provider_dry_run(cfg_dir, monkeypatch):
    invocations = []
    def fake_dispatch(request, **kwargs):
        invocations.append((request, kwargs))
        return pa.aoa_dispatch.DispatchResult(request.engine, "success", 0, "ok", "ok", "", 0)
    monkeypatch.setattr(pa.aoa_dispatch, "dispatch_request", fake_dispatch)

    assert pa.probe("codex") is True
    assert invocations[0][0].engine == "codex"
    assert invocations[0][0].prompt == "info"
    assert invocations[0][1]["emit_telemetry"] is False


def test_probe_agy_uses_print_probe(cfg_dir, monkeypatch):
    invocations = []

    def fake_dispatch(request, **kwargs):
        invocations.append(request)
        return pa.aoa_dispatch.DispatchResult(request.engine, "success", 0, "ok", "ok", "", 0)
    monkeypatch.setattr(pa.aoa_dispatch, "dispatch_request", fake_dispatch)

    assert pa.probe("antigravity") is True
    assert invocations[0].engine == "agy"
    assert invocations[0].model == "gemini-3.5-flash"
    assert invocations[0].prompt == "health"


def test_probe_engine_uses_registered_adapter_contract(monkeypatch, tmp_path):
    executable = tmp_path / "agent-cli"
    executable.write_text("fake", encoding="utf-8")
    config = {
        "paths": {"workdir": str(tmp_path)},
        "cli": {"codex": str(executable)},
        "timeouts": {"health_probe_seconds": 5},
        "dispatch": {"adapters": {
            "codex": {"module": "adapters.codex", "cli_key": "codex", "enabled": True}
        }},
    }
    seen = []

    def fake_dispatch(request, **kwargs):
        seen.append((request, kwargs))
        return pa.aoa_dispatch.DispatchResult(
            request.engine, "success", 0, "AOA_CODEX_OK", "", "", 0
        )

    monkeypatch.setattr(pa.aoa_dispatch, "dispatch_request", fake_dispatch)
    assert pa.probe_engine("codex", config) is True
    assert seen[0][0].prompt == "Reply with exactly: AOA_CODEX_OK"
    assert seen[0][1]["preflight"] is False


def test_probe_engine_fails_when_auth_reply_is_wrong(monkeypatch, tmp_path, capsys):
    executable = tmp_path / "agent-cli"
    executable.write_text("fake", encoding="utf-8")
    config = {
        "paths": {"workdir": str(tmp_path)},
        "cli": {"codex": str(executable)},
        "timeouts": {"health_probe_seconds": 5},
        "dispatch": {"adapters": {
            "codex": {"module": "adapters.codex", "cli_key": "codex", "enabled": True}
        }},
    }
    monkeypatch.setattr(
        pa.aoa_dispatch, "dispatch_request",
        lambda request, **kwargs: pa.aoa_dispatch.DispatchResult(
            request.engine, "failure", 1, "login required", "", "authentication failed", 0
        ),
    )
    assert pa.probe_engine("codex", config) is False
    assert "authentication failed" in capsys.readouterr().err
