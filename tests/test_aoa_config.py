import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import config_loader as cl  # noqa: E402


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
