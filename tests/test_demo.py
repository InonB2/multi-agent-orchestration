import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aoa_cli import cli


def test_bundled_visible_and_oracle_transition(tmp_path):
    project = tmp_path / "hello-org"
    project.mkdir()
    for name in ("hello_org.py", "visible_test.py", "oracle_test.py"):
        (project / name).write_text(
            (ROOT / "examples" / "hello-org" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (project / "hello_org.py").write_text(
        'def canonical_member_id(display_name: str) -> str:\n'
        '    return display_name.strip().lower()\n',
        encoding="utf-8",
    )
    assert cli._run_test(project / "visible_test.py").returncode == 0
    assert cli._run_test(project / "oracle_test.py").returncode != 0


def test_dashboard_catch_is_clean_and_records_different_engines(tmp_path):
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    cli._dashboard_catch(tmp_path, "worker-a", "tester-b", "in_progress", "oracle failed")
    payload = json.loads((dashboard / "live_tasks.json").read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["worker_engine"] == "worker-a"
    assert entry["tester_engine"] == "tester-b"
    assert entry["status"] == "caught"
    assert entry["lifecycle_state"] == "in_progress"
    assert "Inon" not in json.dumps(payload)


def test_shipped_dashboard_starts_empty():
    payload = json.loads((ROOT / "dashboard" / "live_tasks.json").read_text(encoding="utf-8"))
    assert payload["entries"] == []
    assert payload["_meta"]["updated_at"] is None


def test_demo_config_requires_different_engines():
    config = json.loads((ROOT / "aoa.config.json").read_text(encoding="utf-8"))
    assert config["demo"]["worker_engine"] != config["demo"]["tester_engine"]
