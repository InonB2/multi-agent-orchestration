import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import config_loader as cl  # noqa: E402
import llm_provider as lp  # noqa: E402


def write_config(path, **updates):
    data = cl._portable_aoa_defaults()
    data.update(updates)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_environment_beats_file_and_relative_path_is_install_root_relative(tmp_path):
    cfg = write_config(tmp_path / "custom.json", paths={
        "workdir": "file-value", "agy_workers": "workspaces/agy",
        "codex_workers": "workspaces/codex", "agy_queue": "tasks/agy_queue",
        "agy_results": "tasks/agy_results",
    })
    result = cl.load_aoa_config(cfg, {"AOA_WORKDIR": "env value with spaces"})
    assert Path(result["paths"]["workdir"]) == (cl.AOA_ROOT / "env value with spaces").resolve()


def test_resolution_does_not_depend_on_current_working_directory(tmp_path, monkeypatch):
    cfg = write_config(tmp_path / "custom.json")
    monkeypatch.chdir(tmp_path)
    result = cl.load_aoa_config(cfg, {})
    assert Path(result["paths"]["workdir"]) == cl.AOA_ROOT.resolve()


def test_missing_optional_default_uses_portable_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "AOA_CONFIG_PATH", tmp_path / "absent.json")
    result = cl.load_aoa_config(environ={})
    assert Path(result["paths"]["agy_workers"]) == (cl.AOA_ROOT / "workspaces/agy").resolve()
    assert result["models"]["ladders"]["claude"]["S"][0] in result["models"]["capabilities"]


def test_ptme_imports_with_optional_config_absent(tmp_path):
    isolated = tmp_path / "portable install" / "scripts"
    isolated.mkdir(parents=True)
    for name in ("config_loader.py", "ptme.py"):
        (isolated / name).write_text((SCRIPTS / name).read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "import ptme; print(ptme.ENGINE_LADDERS['claude']['S'][0])"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(isolated)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "claude-haiku-4.5"


def test_explicit_missing_and_invalid_config_fail_clearly(tmp_path):
    with pytest.raises(cl.ConfigLoadError, match="not found"):
        cl.load_aoa_config(tmp_path / "absent.json", {})
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    with pytest.raises(cl.ConfigLoadError, match="Failed to parse"):
        cl.load_aoa_config(bad, {})


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_invalid_environment_integer_fails(value):
    with pytest.raises(cl.ConfigLoadError, match="positive integer"):
        cl.load_aoa_config(environ={"AOA_CODEX_MAX_PARALLEL": value})


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"cli": {"codex": 42}}, "cli.codex must be a non-empty command string"),
        ({"models": {"capabilities": [], "ladders": {}}}, "models.capabilities must be a JSON object"),
        ({"models": {"capabilities": {}, "ladders": "bad"}}, "models.ladders must be a JSON object"),
    ],
)
def test_invalid_structural_types_fail_clearly(tmp_path, updates, message):
    cfg = write_config(tmp_path / "invalid.json", **updates)
    with pytest.raises(cl.ConfigLoadError, match=message):
        cl.load_aoa_config(cfg, {})


def test_agent_aliases_use_engine_cli_configuration(monkeypatch):
    monkeypatch.setattr(lp, "AOA_CONFIG", {
        "cli": {"agy": "custom-agy", "claude": "custom-claude", "codex": "custom-codex"}
    })
    fallback = {"provider": {"cli_cmd": "fallback"}, "agent": {"preferred_model": "legacy"}}
    assert lp._resolve_cli_cmd(fallback, "antigravity") == "custom-agy"
    assert lp._resolve_cli_cmd(fallback, "agy") == "custom-agy"
    assert lp._resolve_cli_cmd(fallback, "claude-code") == "custom-claude"
    assert lp._resolve_cli_cmd(fallback, "claude") == "custom-claude"
    assert lp._resolve_cli_cmd(fallback, "codex") == "custom-codex"


@pytest.mark.parametrize(
    ("provider", "preferred_model", "expected"),
    [
        ({}, "claude-code", "custom-claude"),
        ({}, "codex", "custom-codex"),
        ({}, "antigravity", "custom-agy"),
        ({"cli_cmd": "agy"}, "unrelated", "custom-agy"),
        ({"cli_cmd": "codex.exe"}, "unrelated", "custom-codex"),
        ({"cli_cmd": "custom-wrapper"}, "claude-code", "custom-wrapper"),
    ],
)
def test_role_agent_resolves_engine_from_provider_or_preferred_model(
    monkeypatch, provider, preferred_model, expected
):
    monkeypatch.setattr(lp, "AOA_CONFIG", {
        "cli": {"agy": "custom-agy", "claude": "custom-claude", "codex": "custom-codex"}
    })
    config = {"provider": provider, "agent": {"preferred_model": preferred_model}}
    assert lp._resolve_cli_cmd(config, "coder") == expected


@pytest.mark.parametrize(
    ("agent", "env_name", "command"),
    [
        ("antigravity", "AOA_AGY_CMD", "agy-env-command"),
        ("claude-code", "AOA_CLAUDE_CMD", "claude-env-command"),
    ],
)
def test_agent_alias_environment_override_reaches_provider_cli(agent, env_name, command):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "llm_provider.py"), "info", "--agent", agent],
        cwd=SCRIPTS.parent,
        env={**os.environ, env_name: command},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CLI tool:      {}".format(command) in result.stdout


@pytest.mark.parametrize("agent", ["orchestrator", "coder"])
def test_shipped_role_agent_honors_claude_environment_override(agent):
    command = "claude-role-env-command"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "llm_provider.py"), "info", "--agent", agent],
        cwd=SCRIPTS.parent,
        env={**os.environ, "AOA_CLAUDE_CMD": command},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CLI tool:      {}".format(command) in result.stdout


def test_relative_cli_path_resolves_from_install_root(tmp_path):
    cfg = write_config(tmp_path / "custom.json", cli={
        "python": "python", "powershell": "powershell", "claude": "bin/claude",
        "codex": "codex", "agy": "agy",
    })
    result = cl.load_aoa_config(cfg, {})
    assert Path(result["cli"]["claude"]) == (cl.AOA_ROOT / "bin/claude").resolve()


def test_windows_absolute_path_is_not_rebased_on_windows(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows path semantics")
    cfg = write_config(tmp_path / "custom.json")
    result = cl.load_aoa_config(cfg, {"AOA_WORKDIR": r"C:\portable test\repo"})
    assert Path(result["paths"]["workdir"]) == Path(r"C:\portable test\repo")


def test_cli_from_another_cwd_honors_environment(tmp_path):
    env = os.environ.copy()
    env["AOA_CODEX_TIMEOUT_SECONDS"] = "321"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "config_loader.py"), "aoa-get", "timeouts.codex_dispatch_seconds"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "321"
