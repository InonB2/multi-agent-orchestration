"""tests/test_learning_loop_profiles.py — close-the-loop into profiles + backfill + two-tier QA.

Covers:
  * record_lesson() appends a dated bullet to the RIGHT profile, under
    '## Lessons learned', preserving the '## Scratchpad pointer' section.
  * record_lesson() is idempotent-safe (same bullet/day is not duplicated).
  * record_lesson() returns None (no write) for an unknown engine/role.
  * a malformed/short profile is not corrupted; the Scratchpad section survives.
  * backfill() populates learning_loop.jsonl from real finished ptme decisions
    and writes the curated lessons into the matching profiles.
  * two-tier QA records internal THEN external with worker != tester at each
    tier (internal role != worker role; external engine != worker engine).
"""

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import learning_loop as ll  # noqa: E402
import sub_orchestrator as so  # noqa: E402


PROFILE_STUB = """# Coder Agent Profile - CLAUDE Engine

## Role
Coder Agent

## Specialization
Code generation.

## Lessons learned
### 2026-06-23

## Scratchpad pointer
Scratchpad directory: [scratchpad/](file:///x)
"""


def _make_teams(tmp_path, engine="claude", role="coder", content=PROFILE_STUB):
    d = tmp_path / "agents" / "teams" / engine
    d.mkdir(parents=True)
    p = d / "{}.md".format(role)
    p.write_text(content, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- record_lesson
def test_record_lesson_appends_to_right_profile(tmp_path, monkeypatch):
    p = _make_teams(tmp_path)
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")
    rec = ll.record_lesson(
        engine="claude", role="coder",
        lesson="route execution-heavy QA to Claude",
        source="GATE", severity="high", today="2026-06-23",
    )
    assert rec is not None and rec["written"] is True
    assert rec["role"] == "coder" and rec["engine"] == "claude"
    text = p.read_text(encoding="utf-8")
    assert "## Lessons learned" in text
    assert "- [HIGH] route execution-heavy QA to Claude (source: GATE)" in text
    # Scratchpad section preserved, and the bullet lands BEFORE it.
    assert "## Scratchpad pointer" in text
    assert text.index("route execution-heavy QA") < text.index("## Scratchpad pointer")


def test_record_lesson_is_idempotent(tmp_path, monkeypatch):
    p = _make_teams(tmp_path)
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")
    kw = dict(engine="claude", role="coder", lesson="same lesson", source="GATE", today="2026-06-23")
    r1 = ll.record_lesson(**kw)
    r2 = ll.record_lesson(**kw)
    assert r1["written"] is True
    assert r2["written"] is False  # second identical write is a no-op
    assert p.read_text(encoding="utf-8").count("- same lesson (source: GATE)") == 1


def test_record_lesson_unknown_engine_role_is_noop(tmp_path, monkeypatch):
    _make_teams(tmp_path)
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")
    assert ll.record_lesson(engine="nope", role="coder", lesson="x", source="s") is None
    assert ll.record_lesson(engine="claude", role="wizard", lesson="x", source="s") is None


def test_record_lesson_creates_section_when_missing(tmp_path, monkeypatch):
    short = "# Title\n\n## Role\nCoder\n\n## Scratchpad pointer\nScratchpad: [x](y)\n"
    p = _make_teams(tmp_path, content=short)
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")
    rec = ll.record_lesson(engine="claude", role="coder", lesson="new", source="S", today="2026-06-23")
    assert rec["written"] is True
    text = p.read_text(encoding="utf-8")
    assert "## Lessons learned" in text
    assert "- new (source: S)" in text
    # Lessons section inserted BEFORE the scratchpad pointer; scratchpad intact.
    assert text.index("## Lessons learned") < text.index("## Scratchpad pointer")
    assert "Scratchpad: [x](y)" in text


def test_record_lesson_does_not_corrupt_scratchpad(tmp_path, monkeypatch):
    p = _make_teams(tmp_path)
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")
    before_scratch = "## Scratchpad pointer\nScratchpad directory: [scratchpad/](file:///x)"
    ll.record_lesson(engine="claude", role="coder", lesson="a", source="S")
    ll.record_lesson(engine="claude", role="coder", lesson="b", source="S")
    text = p.read_text(encoding="utf-8")
    assert before_scratch in text  # scratchpad block byte-for-byte preserved
    assert "- a (source: S)" in text and "- b (source: S)" in text


# --------------------------------------------------------------------------- backfill
def test_backfill_populates_outcomes_and_profiles(tmp_path, monkeypatch):
    # A real-shaped finished ptme decision row.
    ptme_path = tmp_path / "ptme.jsonl"
    row = {
        "task_id": "GATE-1", "engine": "claude", "role": "coder", "complexity": "L",
        "planned_tokens": 80000, "actual_tokens": 120000, "duration_ms": 5000,
        "reason": "complex signals: architecture", "run_status": "done",
        "qa_verdict": "fail", "finished_at": "2026-06-23T10:00:00Z",
        "ts": "2026-06-23T09:00:00Z",
    }
    ptme_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    outcomes_path = tmp_path / "ll.jsonl"
    _make_teams(tmp_path, engine="claude", role="coder")
    monkeypatch.setattr(ll, "TEAMS_DIR", tmp_path / "agents" / "teams")

    sample_lessons = (
        {"engine": "claude", "role": "coder", "lesson": "role inference first-hit-wins bug",
         "source": "GATE", "severity": "med"},
    )
    result = ll.backfill(
        outcomes_path=outcomes_path, ptme_path=ptme_path,
        write_profiles=True, lessons=sample_lessons, gate_outcomes=(),
    )
    assert result["outcomes_appended"] == 1  # one finished ptme row
    loaded = ll._load_outcomes(outcomes_path)
    assert loaded[-1]["task_id"] == "GATE-1"
    assert loaded[-1]["token_delta"] == 40000
    assert loaded[-1]["qa_verdict"] == "fail"
    assert result["lessons_written"] == 1
    prof = (tmp_path / "agents" / "teams" / "claude" / "coder.md").read_text(encoding="utf-8")
    assert "role inference first-hit-wins bug" in prof

    # Idempotent: re-running appends no new outcomes.
    again = ll.backfill(outcomes_path=outcomes_path, ptme_path=ptme_path,
                        write_profiles=True, lessons=sample_lessons, gate_outcomes=())
    assert again["outcomes_appended"] == 0


def test_backfill_gate_outcomes_enable_promotion(tmp_path, monkeypatch):
    """Curated real gate FAIL verdicts are appended and let a struggle rule promote."""
    outcomes_path = tmp_path / "ll.jsonl"
    ptme_path = tmp_path / "ptme.jsonl"  # no real rows needed
    gate = (
        {"task_id": "G1", "engine": "claude", "role": "coder", "complexity": "L",
         "signals": [], "qa_verdict": "fail", "success": False,
         "planned_tokens": 25000, "actual_tokens": 120000},
        {"task_id": "G2", "engine": "claude", "role": "coder", "complexity": "L",
         "signals": [], "qa_verdict": "fail", "success": False,
         "planned_tokens": 25000, "actual_tokens": 120000},
        {"task_id": "G3", "engine": "claude", "role": "coder", "complexity": "L",
         "signals": [], "qa_verdict": "fail", "success": False,
         "planned_tokens": 25000, "actual_tokens": 120000},
    )
    res = ll.backfill(outcomes_path=outcomes_path, ptme_path=ptme_path,
                      write_profiles=False, gate_outcomes=gate)
    assert res["outcomes_appended"] == 3
    rules_path = tmp_path / "promoted_rules.json"
    payload = ll.validate(outcomes=ll._load_outcomes(outcomes_path),
                          rules_path=rules_path, min_sample=3)
    promoted = [r for r in payload["rules"] if r["status"] == "promoted"]
    # struggling (all FAIL) + overrun -> effort_overrun rule promotes.
    assert any(r["key"] == "effort_overrun|e=claude|r=coder" for r in promoted)


# --------------------------------------------------------------------------- two-tier QA
def test_two_tier_qa_internal_then_external_worker_ne_tester():
    rec = so.two_tier_qa(
        engine="claude", worker_role="coder", sub_task_id="CLAUDE-X-S1",
        internal_verdict="pass", external_verdict="pass", dry_run=True,
    )
    iq, eq = rec["internal_qa"], rec["external_qa"]
    # Tier 1 internal: same engine, different role (worker != tester).
    assert iq["engine"] == "claude"
    assert iq["tester_role"] != "coder"
    assert iq["verdict"] == "pass"
    # Tier 2 external: different ENGINE (worker != tester at engine level).
    assert eq["engine"] != "claude"
    assert eq["verdict"] == "pass"
    assert eq["security"]["tester_role"] == "security"
    assert eq["security"]["verdict"] == "pass"
    assert eq["gated_on_internal_pass"] is True


def test_two_tier_qa_external_gated_on_internal_pass():
    # Internal FAILS -> external must stay pending (None), security pending.
    rec = so.two_tier_qa(
        engine="codex", worker_role="qa", sub_task_id="CODEX-Y-S1",
        internal_verdict="fail", external_verdict="pass", dry_run=True,
    )
    # worker role 'qa' -> internal tester escalates to 'security' (not self).
    assert rec["internal_qa"]["tester_role"] != "qa"
    assert rec["internal_qa"]["verdict"] == "fail"
    assert rec["external_qa"]["verdict"] is None
    assert rec["external_qa"]["gated_on_internal_pass"] is False
    assert rec["external_qa"]["engine"] != "codex"


def test_two_tier_qa_security_role_internal_tester_differs():
    rec = so.two_tier_qa(engine="agy", worker_role="security", sub_task_id="AGY-Z-S1", dry_run=True)
    assert rec["internal_qa"]["tester_role"] != "security"
    assert rec["external_qa"]["engine"] != "agy"
