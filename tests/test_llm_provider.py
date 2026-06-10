"""
tests/test_llm_provider.py — Tests for the LLM provider abstraction layer.

Run with:  pytest tests/test_llm_provider.py -v
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable without installing a package
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import llm_provider as lp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULTS_TOML = """\
[agent]
max_task_size   = "L"
preferred_model = "claude-code"
qa_gate         = ""
handoff_to      = ""

[task_types]
accepts = []
rejects = []

[rate_limit]
notify_at_pct = 85
resume_queue  = true

[output]
deliverable_path = "owner_inbox/"
report_path      = "agents/andy/inbox/"

[provider]
type = "cli"
"""

CLI_AGENT_TOML = """\
[agent]
name            = "testcli"
preferred_model = "codex"
max_task_size   = "M"

[provider]
type = "cli"
"""

API_AGENT_TOML = """\
[agent]
name            = "testapi"
preferred_model = "testapi"
max_task_size   = "L"

[provider]
type            = "api"
api_base_url    = "https://api.openai.com/v1"
api_key_env_var = "TEST_OPENAI_KEY"
model_id        = "gpt-4o-test"
"""

ANTHROPIC_AGENT_TOML = """\
[agent]
name            = "testanth"
preferred_model = "testanth"
max_task_size   = "L"

[provider]
type            = "api"
api_base_url    = "https://api.anthropic.com/v1"
api_key_env_var = "TEST_ANTHROPIC_KEY"
model_id        = "claude-test"
"""

NO_URL_AGENT_TOML = """\
[agent]
name            = "nourl"
preferred_model = "nourl"
max_task_size   = "M"

[provider]
type            = "api"
api_key_env_var = "NO_URL_KEY"
model_id        = "some-model"
"""


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """
    Create a temporary config directory with _defaults.toml and several test agents.
    Monkeypatch lp.CONFIG_DIR and lp.DEFAULTS so every llm_provider function uses
    the temp directory instead of the real one.
    """
    d = tmp_path / "agents"
    d.mkdir()

    (d / "_defaults.toml").write_text(DEFAULTS_TOML, encoding="utf-8")
    (d / "testcli.toml").write_text(CLI_AGENT_TOML, encoding="utf-8")
    (d / "testapi.toml").write_text(API_AGENT_TOML, encoding="utf-8")
    (d / "testanth.toml").write_text(ANTHROPIC_AGENT_TOML, encoding="utf-8")
    (d / "nourl.toml").write_text(NO_URL_AGENT_TOML, encoding="utf-8")

    monkeypatch.setattr(lp, "CONFIG_DIR", d)
    monkeypatch.setattr(lp, "DEFAULTS",   d / "_defaults.toml")
    return d


# ---------------------------------------------------------------------------
# info command — cli-type agent
# ---------------------------------------------------------------------------

def test_info_cli_agent_prints_type_and_tool(cfg_dir, capsys):
    """info for a cli-type agent must print 'cli' and the CLI tool name."""
    args = argparse.Namespace(agent="testcli")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "cli" in out
    assert "codex" in out              # preferred_model from testcli.toml


def test_info_cli_agent_no_api_fields(cfg_dir, capsys):
    """info for a cli-type agent must NOT print API base URL."""
    args = argparse.Namespace(agent="testcli")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "api.openai.com" not in out
    assert "api_base_url" not in out


# ---------------------------------------------------------------------------
# info command — api-type agent
# ---------------------------------------------------------------------------

def test_info_api_agent_prints_type_and_url(cfg_dir, monkeypatch, capsys):
    """info for an api-type agent must print 'api' and the base URL."""
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-fake")
    args = argparse.Namespace(agent="testapi")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "api" in out
    assert "api.openai.com" in out
    assert "gpt-4o-test" in out


def test_info_api_agent_shows_key_env_set(cfg_dir, monkeypatch, capsys):
    """info must show [SET] for a key env var that is set."""
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-realish")
    args = argparse.Namespace(agent="testapi")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "TEST_OPENAI_KEY" in out
    assert "SET" in out


def test_info_api_agent_shows_key_env_not_set(cfg_dir, monkeypatch, capsys):
    """info must show [NOT SET] for a key env var that is absent."""
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)
    args = argparse.Namespace(agent="testapi")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "NOT SET" in out


def test_info_anthropic_agent_shows_anthropic_auth(cfg_dir, monkeypatch, capsys):
    """info for an Anthropic agent must mention the Anthropic auth format."""
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    args = argparse.Namespace(agent="testanth")
    lp.cmd_info(args)

    out = capsys.readouterr().out
    assert "Anthropic" in out
    assert "x-api-key" in out


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------

def test_list_shows_all_agents(cfg_dir, capsys):
    """list must show all agents present in the config directory."""
    lp.cmd_list(argparse.Namespace())

    out = capsys.readouterr().out
    assert "testcli" in out
    assert "testapi" in out
    assert "testanth" in out


def test_list_shows_provider_types(cfg_dir, capsys):
    """list must show both 'cli' and 'api' in its output."""
    lp.cmd_list(argparse.Namespace())

    out = capsys.readouterr().out
    assert "cli" in out
    assert "api" in out


# ---------------------------------------------------------------------------
# run --dry-run — cli agent
# ---------------------------------------------------------------------------

def test_run_dry_run_cli_prints_command_no_exec(cfg_dir, monkeypatch, capsys):
    """run --dry-run for a cli agent prints the CLI command and does NOT call subprocess."""
    called = []

    def fake_run(cmd, **kwargs):
        called.append(cmd)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(lp.subprocess, "run", fake_run)

    args = argparse.Namespace(agent="testcli", prompt="do some work", dry_run=True)
    lp.cmd_run(args)

    out = capsys.readouterr().out
    assert "codex" in out
    assert "do some work" in out
    assert called == [], "subprocess.run must NOT be called during --dry-run"


def test_run_no_dry_run_cli_calls_subprocess(cfg_dir, monkeypatch, capsys):
    """run (live) for a cli agent must invoke subprocess.run with the CLI command."""
    invocations = []

    def fake_run(cmd, **kwargs):
        invocations.append(cmd)
        return type("R", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(lp.subprocess, "run", fake_run)

    # Wrap sys.exit so the test doesn't abort on returncode 0
    with pytest.raises(SystemExit) as exc_info:
        args = argparse.Namespace(agent="testcli", prompt="work", dry_run=False)
        lp.cmd_run(args)

    assert exc_info.value.code == 0
    assert invocations, "subprocess.run must be called in live mode"
    assert invocations[0][0] == "codex"


# ---------------------------------------------------------------------------
# run --dry-run — api agent
# ---------------------------------------------------------------------------

def test_run_dry_run_api_prints_request_no_send(cfg_dir, monkeypatch, capsys):
    """run --dry-run for an api agent prints endpoint + payload and does NOT call urlopen."""
    called = []

    def fake_urlopen(req, **kwargs):
        called.append(req)

    monkeypatch.setattr(lp.urllib.request, "urlopen", fake_urlopen)
    # API key is NOT required for dry-run
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)

    args = argparse.Namespace(agent="testapi", prompt="review the code", dry_run=True)
    lp.cmd_run(args)

    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert "api.openai.com" in out
    assert "gpt-4o-test" in out
    assert called == [], "urllib.request.urlopen must NOT be called during --dry-run"


def test_run_dry_run_anthropic_shows_messages_endpoint(cfg_dir, monkeypatch, capsys):
    """Dry-run for an Anthropic api agent must show the /messages endpoint."""
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)

    args = argparse.Namespace(agent="testanth", prompt="hello", dry_run=True)
    lp.cmd_run(args)

    out = capsys.readouterr().out
    assert "/messages" in out


# ---------------------------------------------------------------------------
# Path traversal / invalid agent name
# ---------------------------------------------------------------------------

def test_invalid_agent_name_rejected_info(cfg_dir):
    """info with an invalid agent name must exit 1."""
    args = argparse.Namespace(agent="../etc/passwd")
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_info(args)
    assert exc_info.value.code == 1


def test_invalid_agent_name_rejected_run(cfg_dir):
    """run with an invalid agent name must exit 1."""
    args = argparse.Namespace(agent="../../root", prompt="x", dry_run=True)
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_run(args)
    assert exc_info.value.code == 1


def test_invalid_agent_name_spaces_rejected(cfg_dir):
    """Agent name with spaces must be rejected."""
    args = argparse.Namespace(agent="bad name")
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_info(args)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Error: missing api_base_url
# ---------------------------------------------------------------------------

def test_missing_api_base_url_exits_1_info(cfg_dir, capsys):
    """info for an api-type agent with no api_base_url must exit 1 with a clear message."""
    args = argparse.Namespace(agent="nourl")
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_info(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "api_base_url" in err


def test_missing_api_base_url_exits_1_run(cfg_dir, capsys):
    """run for an api-type agent with no api_base_url must exit 1."""
    args = argparse.Namespace(agent="nourl", prompt="x", dry_run=True)
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_run(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "api_base_url" in err


# ---------------------------------------------------------------------------
# Error: missing API key env var (live run only)
# ---------------------------------------------------------------------------

def test_missing_api_key_env_var_exits_1_with_name(cfg_dir, monkeypatch, capsys):
    """Live run for an api-type agent when the key env var is not set must exit 1
    and include the env var name in the error message."""
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)

    args = argparse.Namespace(agent="testapi", prompt="go", dry_run=False)
    with pytest.raises(SystemExit) as exc_info:
        lp.cmd_run(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    # The env var name must appear in the error so the developer knows what to set
    assert "TEST_OPENAI_KEY" in err


def test_missing_api_key_not_required_for_dry_run(cfg_dir, monkeypatch, capsys):
    """--dry-run must not fail even when the API key env var is absent."""
    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)

    args = argparse.Namespace(agent="testapi", prompt="go", dry_run=True)
    # Should NOT raise SystemExit
    lp.cmd_run(args)

    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
