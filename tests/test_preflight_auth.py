"""
tests/test_preflight_auth.py — sequential live CLI preflight probes.
"""

import subprocess
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
"""

CODEX_TOML = """\
[agent]
preferred_model = "codex"

[provider]
type          = "cli"
cli_exec_args = ["exec"]
"""

AGY_TOML = """\
[agent]
preferred_model = "antigravity"

[provider]
type    = "cli"
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
    kwargs_seen = []

    def fake_run(cmd, **kwargs):
        invocations.append(cmd)
        kwargs_seen.append(kwargs)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(pa.subprocess, "run", fake_run)

    assert pa.probe("codex") is True
    assert invocations == [["codex", "exec", "info"]]
    assert kwargs_seen[0]["stdin"] == subprocess.DEVNULL


def test_probe_agy_uses_print_probe(cfg_dir, monkeypatch):
    invocations = []

    def fake_run(cmd, **kwargs):
        invocations.append(cmd)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(pa.subprocess, "run", fake_run)

    assert pa.probe("antigravity") is True
    assert invocations == [["agy", "--model", "gemini-3.5-flash", "--print", "health"]]
