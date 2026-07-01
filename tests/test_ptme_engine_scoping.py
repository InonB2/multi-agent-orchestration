"""Engine-scoping + foundation-rebuild guarantees for PTME and analytics.

Covers bugs A-G from the foundation rebuild:
  A) engine-scoped model selection (no cross-engine leaks)
  B) role -> named specialist on every record
  C) complexity of a trivial task is S, not M
  D) decision records carry all required fields incl. timestamps
  E) token scale labelled (tokens_task vs tokens_session_cumulative)
  F) analytics "active" != lifetime totals
  G) orchestrator team_size reads 18 (live) from roster
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_activity as aa  # noqa: E402
import build_analytics as ba  # noqa: E402
import dispatch_worker as dw  # noqa: E402
import orchestrator_stats as os_stats  # noqa: E402
import ptme  # noqa: E402


# --------------------------------------------------------------------------- A
def test_ladders_never_leak_across_engines():
    for engine, ladder in ptme.ENGINE_LADDERS.items():
        for complexity, (model, _effort) in ladder.items():
            info = ptme.CAPABILITY_TABLE[model]
            assert info["family"] == engine, (
                f"{engine}/{complexity} leaked to {model} ({info['family']})"
            )


@pytest.mark.parametrize("complexity", ["S", "M", "L", "XL"])
def test_claude_task_never_gets_a_gpt_or_gemini_model(complexity):
    model, _ = ptme.recommend_for_complexity(complexity, family="claude")
    assert ptme.CAPABILITY_TABLE[model]["family"] == "claude"
    assert "gpt" not in model and "gemini" not in model


@pytest.mark.parametrize("complexity", ["S", "M", "L", "XL"])
def test_codex_task_never_gets_a_claude_model(complexity):
    model, _ = ptme.recommend_for_complexity(complexity, family="codex")
    assert ptme.CAPABILITY_TABLE[model]["family"] == "codex"
    assert "claude" not in model and "gemini" not in model


def test_decide_rejects_foreign_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    # A codex task is handed a claude recommendation — must be rejected.
    rec = ptme.decide(
        task_id="X1",
        task_text="Implement a contained refactor in one file.",
        engine="codex",
        recommended_model="claude-opus-4.8",
    )
    assert ptme.CAPABILITY_TABLE[rec["recommended_model"]]["family"] == "codex"
    assert "rejected foreign recommendation" in rec["reason"].lower()


def test_decide_rejects_foreign_override(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    rec = ptme.decide(
        task_id="X2",
        task_text="Build a small claude doc.",
        engine="claude",
        override_model="gpt-5.5",  # foreign override
    )
    assert ptme.CAPABILITY_TABLE[rec["decided_model"]]["family"] == "claude"
    assert "rejected foreign override" in rec["reason"].lower()


def test_decide_allows_agy_to_run_allowed_foreign_family(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    rec = ptme.decide(
        task_id="X2B",
        task_text="Synthesize a long research brief.",
        engine="agy",
        recommended_model="claude-opus-4.8",
    )
    assert rec["recommended_model"] == "claude-opus-4.8"
    assert rec["decided_model"] == "claude-opus-4.8"
    assert "rejected foreign" not in rec["reason"].lower()


def test_decide_unknown_model_fails_closed_to_engine_default(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    rec = ptme.decide(
        task_id="X2C",
        task_text="Implement a contained refactor in one file.",
        engine="codex",
        recommended_model="mystery-model-1",
    )
    assert rec["recommended_model"] == "gpt-5.3-codex"
    assert rec["decided_model"] == "gpt-5.3-codex"
    assert "unknown model" in rec["reason"].lower()


# --------------------------------------------------------------------------- B
def test_every_role_maps_to_a_named_specialist():
    for role in ("researcher", "coder", "qa", "security", "designer", "content", "data", "web"):
        name, spec = ptme.specialist_for_role(role)
        assert name, f"role {role} has no specialist name"
        assert spec
    assert ptme.specialist_for_role("coder")[0] == "Coder"
    assert ptme.specialist_for_role("qa")[0] == "QA"
    assert ptme.specialist_for_role("web")[0] == "Web"


def test_decide_record_carries_assigned_name(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    rec = ptme.decide(
        task_id="X3", task_text="Implement a feature.", engine="codex", role="coder",
        tester_role="qa",
    )
    assert rec["assigned_name"] == "Coder"
    assert rec["tester_name"] == "QA"
    assert rec["role"] == "coder"


# --------------------------------------------------------------------------- C
def test_trivial_task_is_small_not_medium():
    # "wire 4 images" used to score 0 -> M. Must now be S.
    assert ptme.classify_complexity("wire 4 images into the gallery") == "S"
    assert ptme.classify_complexity("Fix typo in README") == "S"


def test_describe_complexity_is_human_readable():
    desc = ptme.describe_complexity("Design the security architecture and migrate the auth layer")
    assert "complexity" in desc and "score" in desc
    assert "complex signals" in desc.lower()


def test_semantic_complexity_hook_returns_none():
    assert ptme.semantic_complexity("anything") is None


# --------------------------------------------------------------------------- D
REQUIRED_DECISION_FIELDS = {
    "task_id", "engine", "role", "assigned_name", "complexity", "score",
    "score_reasons", "recommended_model", "recommended_effort",
    "override_model", "override_effort", "decided_model", "decided_effort",
    "tester_role", "tester_name", "planned_tokens", "actual_tokens",
    "tokens_task", "tokens_session_cumulative", "duration_ms",
    "received_at", "finished_at", "reason", "ts",
}


def test_decision_record_contains_all_required_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ptme, "LOG_FILE", tmp_path / "d.jsonl")
    rec = ptme.decide(task_id="X4", task_text="Do a thing.", engine="agy", role="researcher")
    missing = REQUIRED_DECISION_FIELDS - set(rec)
    assert not missing, f"missing fields: {missing}"
    assert rec["received_at"] and rec["ts"]
    assert rec["finished_at"] is None  # set on complete


def test_complete_backfills_finished_at_and_actuals(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    live_tasks_file = tmp_path / "live_tasks.json"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", tmp_path / "agent_activity.js")
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", tmp_path / "live_tasks.js")
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)

    dw.start(worker_id="claude-coder", engine="claude", role="coder",
             task_id="QA-9", task_text="Implement the feature in one module.")
    dw.complete(worker_id="claude-coder", task_id="QA-9", status="done",
                usage_tokens=99000, duration_ms=5000)

    rows = [json.loads(l) for l in ptme_log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = rows[-1]
    assert rec["finished_at"] is not None
    assert rec["actual_tokens"] == 99000
    # E: claude is a per-task engine -> tokens_task set, session cumulative None.
    assert rec["tokens_task"] == 99000
    assert rec["tokens_session_cumulative"] is None
    assert rec["duration_ms"] == 5000


# --------------------------------------------------------------------------- E
def test_codex_tokens_labelled_as_session_cumulative(tmp_path, monkeypatch):
    activity_file = tmp_path / "agent_activity.json"
    live_tasks_file = tmp_path / "live_tasks.json"
    ptme_log_file = tmp_path / "ptme_decisions.jsonl"
    usage_log_file = tmp_path / "usage.jsonl"
    monkeypatch.setattr(aa, "ACTIVITY_FILE", activity_file)
    monkeypatch.setattr(aa, "ACTIVITY_JS_FILE", tmp_path / "agent_activity.js")
    monkeypatch.setattr(ptme, "LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_FILE", live_tasks_file)
    monkeypatch.setattr(dw, "LIVE_TASKS_JS_FILE", tmp_path / "live_tasks.js")
    monkeypatch.setattr(dw, "PTME_LOG_FILE", ptme_log_file)
    monkeypatch.setattr(dw, "USAGE_LOG_FILE", usage_log_file)

    dw.start(worker_id="codex-coder", engine="codex", role="coder",
             task_id="C-1", task_text="Heavy multi-file repo surgery and refactor.")
    dw.complete(worker_id="codex-coder", task_id="C-1", status="done",
                usage_tokens=1_200_000, duration_ms=9000, window_pct=42.0)

    rows = [json.loads(l) for l in ptme_log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = rows[-1]
    assert rec["tokens_session_cumulative"] == 1_200_000
    assert rec["tokens_task"] is None
    assert rec["usage_window"]["window_pct"] == 42.0


# --------------------------------------------------------------------------- F
def test_analytics_active_is_not_lifetime_total(tmp_path, monkeypatch):
    tasks_file = tmp_path / "active_tasks.json"
    output_file = tmp_path / "analytics_data.js"
    logs_dir = tmp_path / "logs"
    tasks_file.write_text(json.dumps({"tasks": [
        {"task_id": "T1", "status": "done", "assigned_to": "coder"},
        {"task_id": "T2", "status": "done", "assigned_to": "coder"},
        {"task_id": "T3", "status": "in_progress", "assigned_to": "web"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(ba, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(ba, "PTME_LOG_FILE", logs_dir / "ptme_decisions.jsonl")
    monkeypatch.setattr(ba, "ACTIVITY_FILE", tmp_path / "missing_activity.json")
    monkeypatch.setattr(ba, "LIVE_TASKS_FILE", tmp_path / "missing_live.json")
    monkeypatch.setattr(ba, "LESSONS_FILE", tmp_path / "missing.md")
    monkeypatch.setattr(ba, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(ba, "OUTPUT_FILE", output_file)

    ba.main([])
    src = output_file.read_text(encoding="utf-8").strip()
    payload = json.loads(src[len("window.MMOI_ANALYTICS = "):-1])
    sources = payload["sources"]
    assert sources["tasks_total_lifetime"] == 3
    assert sources["active"]["tasks_in_progress"] == 1
    assert sources["active"]["tasks_in_progress"] != sources["tasks_total_lifetime"]


# --------------------------------------------------------------------------- G
def test_root_team_size_reads_18_from_roster():
    assert os_stats.count_roster_agents() == 18


def test_orchestrator_stats_emits_team_labels_and_weekly_usage(monkeypatch):
    payload = os_stats.build_stats()
    by_id = {o["id"]: o for o in payload["orchestrators"]}
    assert by_id["root"]["team_size"] == 18
    assert "full roster" in by_id["root"]["team_label"]
    assert by_id["codex"]["team_size"] == 9
    assert "specialist roles" in by_id["codex"]["team_label"]
    for orch in payload["orchestrators"]:
        assert "usage_pct_primary" in orch
        assert "usage_pct_weekly" in orch
        assert "usage_source_weekly" in orch
