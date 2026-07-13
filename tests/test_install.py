import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("aoa_installer", ROOT / "scripts" / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_dry_run_discovers_registered_adapters_without_writing(monkeypatch):
    before = (ROOT / "aoa.config.json").read_text(encoding="utf-8")
    monkeypatch.setattr(installer.shutil, "which", lambda command: str(ROOT / (command + ".fake")))
    assert installer.main([
        "--root", str(ROOT), "--engines", "claude,codex",
        "--non-interactive", "--skip-auth", "--no-open", "--dry-run",
    ]) == 0
    assert (ROOT / "aoa.config.json").read_text(encoding="utf-8") == before


def test_missing_selected_adapter_fails_loud(monkeypatch, capsys):
    monkeypatch.setattr(
        installer, "load_config",
        lambda root: json.loads((ROOT / "aoa.config.json").read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(installer.shutil, "which", lambda command: None)
    assert installer.main([
        "--root", str(ROOT), "--engines", "claude,codex",
        "--non-interactive", "--no-open", "--dry-run",
    ]) == 2
    assert "selected adapters are unavailable" in capsys.readouterr().err


def test_old_python_fails_loud(monkeypatch, capsys):
    monkeypatch.setattr(installer.sys, "version_info", (3, 7, 9))
    assert installer.main(["--root", str(ROOT), "--dry-run"]) == 2
    assert "Python 3.8 or newer is required" in capsys.readouterr().err


def test_seed_is_clean_and_rerun_preserves_active_tasks(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "dashboard").mkdir()
    clean = {"last_updated": None, "tasks": []}
    (tmp_path / "tasks" / "active_tasks.example.json").write_text(
        json.dumps(clean), encoding="utf-8"
    )
    for name in installer.EMPTY_FEEDS:
        (tmp_path / "dashboard" / name).write_text("clean", encoding="utf-8")
    (tmp_path / "dashboard" / "index.html").write_text("dashboard", encoding="utf-8")

    installer.seed_state(tmp_path, False)
    active = tmp_path / "tasks" / "active_tasks.json"
    assert json.loads(active.read_text(encoding="utf-8")) == clean
    active.write_text('{"tasks":[{"task_id":"USER"}]}', encoding="utf-8")
    installer.seed_state(tmp_path, False)
    assert "USER" in active.read_text(encoding="utf-8")
