import json
import re
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
    cli._dashboard_catch(tmp_path, "worker-a", "tester-b", "in_progress", "oracle failed")
    dashboard = tmp_path / ".aoa" / "dashboard"
    payload = json.loads((dashboard / "live_tasks.json").read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["worker_engine"] == "worker-a"
    assert entry["tester_engine"] == "tester-b"
    assert entry["status"] == "caught"
    assert entry["lifecycle_state"] == "in_progress"
    assert "PRIVATE_PERSON" not in json.dumps(payload)


def test_shipped_dashboard_starts_empty():
    dashboard = ROOT / "dashboard"
    live = json.loads((dashboard / "live_tasks.json").read_text(encoding="utf-8"))
    activity = json.loads((dashboard / "agent_activity.json").read_text(encoding="utf-8"))
    stats = json.loads((dashboard / "orchestrator_stats.json").read_text(encoding="utf-8"))
    usage = json.loads((dashboard / "engine_usage.json").read_text(encoding="utf-8"))

    assert live["entries"] == [] and live["_meta"]["updated_at"] is None
    assert activity["entries"] == [] and activity["_meta"]["updated_at"] is None
    assert stats["orchestrators"] == [] and stats["_meta"]["updated_at"] is None
    assert usage["updated_at"] is None
    assert usage["codex"]["weekly_pct"] is None
    assert usage["codex"]["five_h_pct"] is None
    assert usage["agy"]["gemini"]["weekly_pct"] is None
    assert usage["claude"]["tokens_7d"] is None


def test_all_shipped_dashboard_feeds_have_no_runtime_history_or_personal_data():
    dashboard = ROOT / "dashboard"
    feeds = [
        dashboard / "agent_activity.json", dashboard / "agent_activity.js",
        dashboard / "engine_usage.json", dashboard / "engine_usage.js",
        dashboard / "live_tasks.json", dashboard / "live_tasks.js",
        dashboard / "orchestrator_stats.json", dashboard / "orchestrator_stats.js",
        dashboard / "analytics_data.js",
    ]
    forbidden = re.compile(
        r"20\d\d-\d\d-\d\d|PRIVATE_PERSON|private workspace|\\.codex\\sessions|prompt_preview",
        re.IGNORECASE,
    )
    for path in feeds:
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), "runtime/history data in {}".format(path.name)

    analytics_text = (dashboard / "analytics_data.js").read_text(encoding="utf-8")
    analytics_payload = analytics_text.split("=", 1)[1].strip()
    analytics = json.loads(analytics_payload[:-1] if analytics_payload.endswith(";") else analytics_payload)
    assert analytics["_meta"]["generated_at"] is None
    assert analytics["sources"]["activity_entries"] == 0
    assert analytics["runtime"] == {"tasks": [], "agents": []}


def test_demo_config_requires_different_engines():
    config = json.loads((ROOT / "aoa.config.json").read_text(encoding="utf-8"))
    assert config["demo"]["worker_engine"] != config["demo"]["tester_engine"]
