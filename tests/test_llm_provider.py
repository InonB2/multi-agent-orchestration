"""
tests/test_llm_provider.py — Tests for the LLM provider abstraction layer.

Run with:  pytest tests/test_llm_provider.py -v
"""

import argparse
import io
import json
import os
import sys
import urllib.error
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

CLI_EXEC_AGENT_TOML = """\
[agent]
name            = "testexec"
preferred_model = "codex"
max_task_size   = "M"

[provider]
type          = "cli"
cli_exec_args = ["exec"]
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
    (d / "testexec.toml").write_text(CLI_EXEC_AGENT_TOML, encoding="utf-8")
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


def test_run_cli_exec_args_inserted_before_prompt(cfg_dir, monkeypatch, capsys):
    """cli_exec_args must be inserted between the command and the prompt, so Codex
    runs as `codex exec "<prompt>"` (non-interactive) rather than the hanging TUI."""
    invocations = []
    kwargs_seen = []

    def fake_run(cmd, **kwargs):
        invocations.append(cmd)
        kwargs_seen.append(kwargs)
        return type("R", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(lp.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        args = argparse.Namespace(agent="testexec", prompt="work", dry_run=False)
        lp.cmd_run(args)

    assert exc_info.value.code == 0
    assert invocations, "subprocess.run must be called in live mode"
    # argv must be exactly [cli_cmd, *exec_args, prompt]
    assert invocations[0] == ["codex", "exec", "work"]


def test_run_cli_closes_stdin(cfg_dir, monkeypatch):
    """The CLI runner must close stdin (DEVNULL) so exec-style CLIs don't hang
    waiting on EOF when launched without a TTY."""
    kwargs_seen = []

    def fake_run(cmd, **kwargs):
        kwargs_seen.append(kwargs)
        return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(lp.subprocess, "run", fake_run)

    with pytest.raises(SystemExit):
        args = argparse.Namespace(agent="testexec", prompt="work", dry_run=False)
        lp.cmd_run(args)

    assert kwargs_seen, "subprocess.run must be called"
    assert kwargs_seen[0].get("stdin") == lp.subprocess.DEVNULL


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


# ---------------------------------------------------------------------------
# TST-7: Malformed / edge-case API responses
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Context-manager compatible fake urllib response."""
    def __init__(self, data):
        if isinstance(data, (dict, list)):
            self._bytes = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            self._bytes = data.encode("utf-8")
        else:
            self._bytes = data

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_llm_provider_anthropic_empty_content(cfg_dir, monkeypatch, capsys):
    """Anthropic response with empty content list must not crash."""
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-fake")

    def fake_urlopen(req, timeout=None):
        return _FakeResponse({"content": []})

    monkeypatch.setattr(lp.urllib.request, "urlopen", fake_urlopen)

    args = argparse.Namespace(agent="testanth", prompt="hello", dry_run=False)
    # Must not raise — empty content falls back to printing raw JSON
    lp.cmd_run(args)
    out = capsys.readouterr().out
    # Either empty or the raw JSON — both are acceptable; no crash is the key assertion
    assert out is not None


def test_llm_provider_http_429(cfg_dir, monkeypatch, capsys):
    """HTTP 429 response must cause sys.exit(1) and include '429' in stderr."""
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-fake")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b"rate limit exceeded"),
        )

    monkeypatch.setattr(lp.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        args = argparse.Namespace(agent="testapi", prompt="hello", dry_run=False)
        lp.cmd_run(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "429" in err


# ---------------------------------------------------------------------------
# TST-8: agent_config.py (via llm_provider.py cmd_list) — malformed TOML
# ---------------------------------------------------------------------------

MALFORMED_TOML = """\
[agent
name = "broken"   # missing closing bracket — invalid TOML
"""


def test_agent_config_list_with_malformed_toml(cfg_dir, capsys):
    """cmd_list must print an ERROR row for a broken TOML file and not raise."""
    # Write a syntactically invalid TOML into the temp config dir
    (cfg_dir / "badagent.toml").write_text(MALFORMED_TOML, encoding="utf-8")

    # cmd_list should complete without raising any exception
    lp.cmd_list(argparse.Namespace())

    out = capsys.readouterr().out
    # The broken agent should appear in the output with an ERROR marker
    assert "badagent" in out
    assert "ERROR" in out


# ===========================================================================
# PTME — Per-Task Model + Effort selection
# (PER_MODEL_EFFORT_PLAN §6 required test cases 1–6)
# ===========================================================================

# A codex-style agent with complexity_mapping and a default model/effort (tier 4).
CODEX_PTME_TOML = """\
[agent]
name            = "testcodex"
preferred_model = "codex"
max_task_size   = "XL"

[provider]
type          = "cli"
cli_exec_args = ["exec"]
model         = "gpt-default"
effort        = "medium"

[provider.complexity_mapping.L]
model  = "gpt-5.5"
effort = "high"
"""

# An agy-style agent whose binary (cli_cmd) differs from preferred_model.
AGY_PTME_TOML = """\
[agent]
name            = "testagy"
preferred_model = "antigravity"
max_task_size   = "L"

[provider]
type    = "cli"
cli_cmd = "agy"

[provider.complexity_mapping.L]
model  = "gemini-3.1-pro"
effort = "high"
"""


def _write_ptme_agents(cfg_dir):
    (cfg_dir / "testcodex.toml").write_text(CODEX_PTME_TOML, encoding="utf-8")
    (cfg_dir / "testagy.toml").write_text(AGY_PTME_TOML, encoding="utf-8")


def _capture_run(monkeypatch):
    """Patch subprocess.run to capture argv + kwargs; returns the capture lists."""
    invocations, kwargs_seen = [], []

    def fake_run(cmd, **kwargs):
        invocations.append(cmd)
        kwargs_seen.append(kwargs)
        return type("R", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(lp.subprocess, "run", fake_run)
    return invocations, kwargs_seen


# --- Case 6: Absent keys → legacy behavior (bare CLI binary) ----------------

def test_resolve_absent_keys_returns_none():
    """No CLI/task/complexity/default values → (None, None) (legacy argv)."""
    model, effort = lp.resolve_model_effort({})
    assert model is None and effort is None


def test_run_absent_keys_legacy_argv(cfg_dir, monkeypatch):
    """cmd_run with no PTME inputs yields the exact legacy argv (case 6)."""
    _write_ptme_agents(cfg_dir)
    invocations, _ = _capture_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        # Namespace deliberately omits task_id/model/effort/complexity (case 1
        # backward-compat: getattr must not raise AttributeError).
        lp.cmd_run(argparse.Namespace(agent="testagy", prompt="work", dry_run=False))

    assert exc.value.code == 0
    # agy with no model resolved → bare binary + prompt (legacy).
    assert invocations[0] == ["agy", "work"]


# --- Case 5: Complexity mapping overrides default ---------------------------

def test_resolve_complexity_overrides_default():
    provider = {
        "model": "gpt-default", "effort": "medium",
        "complexity_mapping": {"L": {"model": "gpt-5.5", "effort": "high"}},
    }
    model, effort = lp.resolve_model_effort(provider, complexity="L")
    assert model == "gpt-5.5"
    assert effort == "high"


def test_run_complexity_builds_codex_flags(cfg_dir, monkeypatch):
    """Codex argv includes -m and -c model_reasoning_effort from complexity map."""
    _write_ptme_agents(cfg_dir)
    invocations, _ = _capture_run(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        lp.cmd_run(argparse.Namespace(
            agent="testcodex", prompt="work", dry_run=False, complexity="L"))

    assert exc.value.code == 0
    assert invocations[0] == [
        "codex", "exec", "-m", "gpt-5.5",
        "-c", 'model_reasoning_effort="high"', "work",
    ]


# --- Case 4: Task overrides complexity --------------------------------------

def test_resolve_task_overrides_complexity():
    provider = {"complexity_mapping": {"L": {"model": "gpt-5.5", "effort": "high"}}}
    model, effort = lp.resolve_model_effort(
        provider, task_model="task-model", task_effort="low", complexity="L")
    assert model == "task-model"
    assert effort == "low"


def test_run_task_override_from_tasks_file(cfg_dir, tmp_path, monkeypatch):
    """provider_model/provider_effort in active_tasks.json beat the complexity map."""
    _write_ptme_agents(cfg_dir)
    tasks_file = tmp_path / "active_tasks.json"
    tasks_file.write_text(json.dumps({"tasks": [{
        "task_id": "T-1", "complexity": "L",
        "provider_model": "gpt-task", "provider_effort": "low",
    }]}), encoding="utf-8")
    monkeypatch.setattr(lp, "TASKS_FILE", tasks_file)

    invocations, _ = _capture_run(monkeypatch)
    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testcodex", prompt="work", dry_run=False, task_id="T-1"))

    # Task overrides win over the complexity 'L' mapping (gpt-5.5/high).
    assert invocations[0] == [
        "codex", "exec", "-m", "gpt-task",
        "-c", 'model_reasoning_effort="low"', "work",
    ]


# --- Case 3: CLI overrides task ---------------------------------------------

def test_resolve_cli_overrides_task():
    model, effort = lp.resolve_model_effort(
        {}, cli_model="cli-model", cli_effort="xhigh",
        task_model="task-model", task_effort="low")
    assert model == "cli-model"
    assert effort == "xhigh"


def test_run_cli_flags_override_tasks_file(cfg_dir, tmp_path, monkeypatch):
    """--model/--effort beat both the tasks file and the complexity map."""
    _write_ptme_agents(cfg_dir)
    tasks_file = tmp_path / "active_tasks.json"
    tasks_file.write_text(json.dumps({"tasks": [{
        "task_id": "T-1", "complexity": "L",
        "provider_model": "gpt-task", "provider_effort": "low",
    }]}), encoding="utf-8")
    monkeypatch.setattr(lp, "TASKS_FILE", tasks_file)

    invocations, _ = _capture_run(monkeypatch)
    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testcodex", prompt="work", dry_run=False,
            task_id="T-1", model="gpt-cli", effort="xhigh", complexity=None))

    assert invocations[0] == [
        "codex", "exec", "-m", "gpt-cli",
        "-c", 'model_reasoning_effort="xhigh"', "work",
    ]


# --- Case 2: cli_cmd consistency across info / list / run -------------------

def test_cli_cmd_consistent_across_commands(cfg_dir, monkeypatch, capsys):
    """cli_cmd ('agy') must show in info + list and be the binary cmd_run invokes."""
    _write_ptme_agents(cfg_dir)

    lp.cmd_info(argparse.Namespace(agent="testagy"))
    info_out = capsys.readouterr().out
    assert "agy" in info_out

    lp.cmd_list(argparse.Namespace())
    list_out = capsys.readouterr().out
    assert "agy" in list_out

    invocations, _ = _capture_run(monkeypatch)
    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testagy", prompt="work", dry_run=False, complexity="L"))
    assert invocations[0][0] == "agy"


def test_agy_run_injects_term_xterm(cfg_dir, monkeypatch):
    """agy execution must inject TERM=xterm into the subprocess environment."""
    _write_ptme_agents(cfg_dir)
    _, kwargs_seen = _capture_run(monkeypatch)

    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testagy", prompt="work", dry_run=False, complexity="L"))

    env = kwargs_seen[0].get("env")
    assert env is not None
    assert env.get("TERM") == "xterm"


def test_agy_run_no_effort_flag(cfg_dir, monkeypatch):
    """agy has no reasoning-effort flag — only --model and --print are appended."""
    _write_ptme_agents(cfg_dir)
    invocations, _ = _capture_run(monkeypatch)

    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testagy", prompt="work", dry_run=False, complexity="L"))

    assert invocations[0] == ["agy", "--model", "gemini-3.1-pro", "--print", "work"]


# --- Case 1: backward compat — codex agents get NO env override -------------

def test_codex_run_no_env_override(cfg_dir, monkeypatch):
    """Non-agy CLI agents must not receive an env kwarg (legacy behavior preserved)."""
    _write_ptme_agents(cfg_dir)
    _, kwargs_seen = _capture_run(monkeypatch)

    with pytest.raises(SystemExit):
        lp.cmd_run(argparse.Namespace(
            agent="testcodex", prompt="work", dry_run=False, complexity="L"))

    assert "env" not in kwargs_seen[0]
