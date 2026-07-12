import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import deploy_gate as dg  # noqa: E402


def test_load_required_env_names_supports_text_yaml_and_json(tmp_path):
    text_manifest = tmp_path / "required.txt"
    text_manifest.write_text("PATH\n# comment\nUSERPROFILE\n", encoding="utf-8")

    yaml_manifest = tmp_path / "required.yaml"
    yaml_manifest.write_text(
        "required_env:\n  - PATH\n  - USERPROFILE\n",
        encoding="utf-8",
    )

    json_manifest = tmp_path / "required.json"
    json_manifest.write_text(
        json.dumps({"required_env": ["PATH", "USERPROFILE"]}),
        encoding="utf-8",
    )

    assert dg.load_required_env_names(text_manifest) == ["PATH", "USERPROFILE"]
    assert dg.load_required_env_names(yaml_manifest) == ["PATH", "USERPROFILE"]
    assert dg.load_required_env_names(json_manifest) == ["PATH", "USERPROFILE"]


def test_collect_effective_env_uses_dotenv_values(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".env").write_text("FROM_DOTENV=present\nEMPTY=\n", encoding="utf-8")

    monkeypatch.delenv("FROM_DOTENV", raising=False)
    monkeypatch.setenv("FROM_OS", "present")

    effective_env = dg.collect_effective_env(repo_root)

    assert effective_env["FROM_OS"] == "present"
    assert effective_env["FROM_DOTENV"] == "present"
    assert effective_env["EMPTY"] == ""


def test_main_returns_exit_2_and_stops_before_tests_when_env_missing(tmp_path, monkeypatch, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    manifest = repo_root / "required.txt"
    manifest.write_text("MISSING_VAR\n", encoding="utf-8")

    seen_commands = []

    def fake_run(command, cwd, shell=False):
        seen_commands.append((command, shell))
        if shell:
            raise AssertionError("test command should not run after env gate failure")
        return dg.CommandResult(0, "scan ok", "")

    monkeypatch.setattr(dg, "run_command", fake_run)
    monkeypatch.setattr(dg, "find_gitleaks_binary", lambda _repo_root: "gitleaks")
    monkeypatch.chdir(repo_root)

    exit_code = dg.main(
        [
            "--env-manifest",
            str(manifest),
            "--test-command",
            "pytest tests/test_deploy_gate.py -x",
            "--target",
            "local",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "FAIL env validation" in output
    assert "MISSING_VAR" in output
    assert len(seen_commands) == 1
    assert seen_commands[0][1] is False


def test_main_success_appends_deployment_entry(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    dashboard_dir = repo_root / "dashboard"
    dashboard_dir.mkdir(parents=True)

    manifest = repo_root / "required.txt"
    manifest.write_text("FROM_DOTENV\n", encoding="utf-8")
    (repo_root / ".env").write_text("FROM_DOTENV=present\n", encoding="utf-8")

    git_calls = {
        ("git", "rev-parse", "HEAD"): "abc123def456",
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "main",
    }
    seen_commands = []

    def fake_run(command, cwd, shell=False):
        seen_commands.append((tuple(command) if isinstance(command, list) else command, shell))
        if shell:
            return dg.CommandResult(0, "tests ok", "")
        key = tuple(command)
        if key in git_calls:
            return dg.CommandResult(0, git_calls[key], "")
        if command[:2] == ["gitleaks", "protect"] or str(command[0]).endswith("gitleaks.exe"):
            return dg.CommandResult(0, "scan ok", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(dg, "run_command", fake_run)
    monkeypatch.setattr(dg, "find_gitleaks_binary", lambda _repo_root: "gitleaks")
    monkeypatch.chdir(repo_root)

    exit_code = dg.main(
        [
            "--env-manifest",
            str(manifest),
            "--test-command",
            "pytest tests/test_deploy_gate.py -x",
            "--target",
            "local",
        ]
    )

    assert exit_code == 0
    log_path = dashboard_dir / "deployments.jsonl"
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["project"] == "repo"
    assert entry["commit_sha"] == "abc123def456"
    assert entry["branch"] == "main"
    assert entry["target"] == "local"
    assert entry["results"]["secret_scan"] == "pass"
    assert entry["results"]["env_validation"] == "pass"
    assert entry["results"]["tests"] == "pass"
    assert any(shell for _command, shell in seen_commands)
