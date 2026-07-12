import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_handoff as handoff  # noqa: E402


def test_generated_handoff_references_public_repo_surfaces(tmp_path, monkeypatch):
    output = tmp_path / "handoff_latest.md"
    monkeypatch.setattr(handoff, "OUT_FILE", output)
    monkeypatch.setattr(handoff, "load_tasks", lambda: ([], [], None))
    monkeypatch.setattr(handoff, "rate_limit_queue", lambda: "none")
    monkeypatch.setattr(handoff, "latest_session_log", lambda: "none")
    monkeypatch.setattr(handoff, "git_diff_stat", lambda: "none")
    monkeypatch.setattr(handoff, "extract_coding_rules", lambda: "rules")
    monkeypatch.setattr(handoff, "write_json_bundle", lambda *args: tmp_path / "task-handoff.json")

    handoff.main()

    text = output.read_text(encoding="utf-8")
    assert "README.md and CLAUDE.md.template" in text
    assert "docs/model_capability_table.md" in text
    for missing in ("PROTOCOL.md", "AGENTS.md", "GEMINI.md", "BKM/", "agents/learning_logs"):
        assert missing not in text
