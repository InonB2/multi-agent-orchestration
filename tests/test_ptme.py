import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ptme  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_classify_complexity_marks_simple_edit_as_small():
    assert ptme.classify_complexity("Fix typo in README and rename one label") == "S"


def test_classify_complexity_marks_security_refactor_as_extra_large():
    text = (
        "Design the architecture for a security refactor across the auth layer, "
        "review risks, migrate dependencies, and coordinate the rollout plan."
    )
    assert ptme.classify_complexity(text) == "XL"


def test_decide_appends_record_and_applies_override(tmp_path, monkeypatch):
    log_file = tmp_path / "ptme_decisions.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log_file)

    record = ptme.decide(
        task_id="PTME-001",
        task_text="Refactor the authentication flow and harden the security checks.",
        override_model="gpt-5.3-codex",
        override_effort="medium",
        decided_by="root",
    )

    assert record["task_id"] == "PTME-001"
    assert record["decided_model"] == "gpt-5.3-codex"
    assert record["decided_effort"] == "medium"
    assert record["decided_by"] == "root"
    assert "override" in record["reason"].lower()
    assert "complexity" in record["reason"].lower()

    records = _read_jsonl(log_file)
    assert records == [record]


def test_decide_mentions_prior_failed_run(tmp_path, monkeypatch):
    log_file = tmp_path / "ptme_decisions.jsonl"
    log_file.write_text(
        json.dumps({"task_id": "PTME-002", "run_exit_code": 1}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ptme, "LOG_FILE", log_file)

    record = ptme.decide(
        task_id="PTME-002",
        task_text="Implement the coding change.",
    )

    assert "prior failed run" in record["reason"].lower()
    assert len(_read_jsonl(log_file)) == 2


def test_cli_decide_prints_json_and_logs_record(tmp_path, monkeypatch, capsys):
    log_file = tmp_path / "ptme_decisions.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log_file)

    rc = ptme.main([
        "decide",
        "--task-id", "PTME-003",
        "--text", "Copy this file and rename a heading",
        "--by", "cli-test",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["task_id"] == "PTME-003"
    assert payload["decided_by"] == "cli-test"
    assert len(_read_jsonl(log_file)) == 1


def test_override_records_judgment_overridden_with_rationale(tmp_path, monkeypatch):
    log = tmp_path / "ptme.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log)
    record = ptme.decide(
        task_id="OV-1",
        task_text="Fix a small label.",
        engine="claude",
        role="coder",
        override_model="claude-opus-4.8",
        override_effort="high",
        decided_by="claude_sub_orchestrator",
    )
    assert record["judgment"] == "overridden"
    assert record["decided_model"] == "claude-opus-4.8"
    assert record["rationale"]
    assert "override applied" in record["rationale"]
    assert record["received_at"]
    assert record["finished_at"] is None  # set on complete


def test_accept_records_judgment_accepted(tmp_path, monkeypatch):
    log = tmp_path / "ptme.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log)
    record = ptme.decide(
        task_id="AC-1",
        task_text="Implement a normal feature with a couple of files.",
        engine="claude",
        role="coder",
        decided_by="claude_sub_orchestrator",
    )
    assert record["judgment"] == "accepted"
    assert record["rationale"]


def test_self_recommendation_clean_rationale_no_replaced_default(tmp_path, monkeypatch):
    log = tmp_path / "ptme.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log)
    # Top orchestrator (root) recommending to itself: no caller rec, no override.
    record = ptme.decide(
        task_id="SELF-1",
        task_text="Design the architecture and security review of the pipeline.",
        engine="claude",
        role="orchestrator",
        decided_by="root",
    )
    assert record["judgment"] == "accepted"
    # No redundant "caller recommendation X replaced default Y" wording.
    assert "replaced engine default" not in record["rationale"]
    assert "caller recommendation" not in record["rationale"]
    assert record["rationale"]


def test_prior_failed_run_forces_override(tmp_path, monkeypatch):
    log = tmp_path / "ptme.jsonl"
    monkeypatch.setattr(ptme, "LOG_FILE", log)
    ptme.decide(task_id="PF-1", task_text="Implement feature.", engine="claude",
                role="coder", decided_by="claude_sub_orchestrator")
    # Mark that run failed.
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows[-1]["run_status"] = "failed"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    record = ptme.decide(task_id="PF-1", task_text="Implement feature.", engine="claude",
                         role="coder", decided_by="claude_sub_orchestrator")
    assert record["judgment"] == "overridden"
    assert "prior failed run" in record["rationale"]
