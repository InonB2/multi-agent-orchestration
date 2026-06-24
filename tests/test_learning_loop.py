"""tests/test_learning_loop.py — Phase 3 closed learning loop.

Covers:
  * a measurably-beneficial rule gets PROMOTED over a minimum sample
  * a non-beneficial rule is NOT promoted (stays candidate)
  * a previously-promoted rule whose benefit goes away is DEMOTED
  * summary() returns live timestamps + benefit metrics + sample sizes
  * record_outcome() computes token/duration deltas and normalizes QA verdict
  * consult() is a strict no-op when promoted_rules.json is empty/missing
"""

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import learning_loop as ll  # noqa: E402


def _outcome(**kw):
    base = {
        "task_id": "T",
        "engine": "claude",
        "role": "coder",
        "complexity": "S",
        "signals": ["wire"],
        "qa_verdict": "pass",
        "success": True,
        "planned_tokens": 8000,
        "actual_tokens": 5000,
        "token_delta": None,
        "planned_duration_ms": None,
        "actual_duration_ms": None,
        "duration_delta_ms": None,
        "ts": "2026-06-23T00:00:00Z",
    }
    base.update(kw)
    if base["token_delta"] is None and base["planned_tokens"] is not None and base["actual_tokens"] is not None:
        base["token_delta"] = base["actual_tokens"] - base["planned_tokens"]
    return base


# --------------------------------------------------------------------------- promotion
def test_beneficial_rule_is_promoted(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    # 4 under-budget S/wire tasks: actual < planned every time -> clearly beneficial.
    outcomes = [
        _outcome(task_id=f"B{i}", planned_tokens=8000, actual_tokens=5000)
        for i in range(4)
    ]
    payload = ll.validate(outcomes=outcomes, rules_path=rules_path, min_sample=3)
    promoted = [r for r in payload["rules"] if r["status"] == "promoted"]
    assert promoted, "an under-budget rule over min_sample should promote"
    rule = promoted[0]
    assert rule["benefit"] > 0
    assert rule["benefit_unit"] == "tokens_saved_per_task"
    assert rule["sample_size"] >= 3
    # round-trips through the file
    assert ll.promoted_rules(rules_path)


def test_non_beneficial_rule_not_promoted(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    # Only ONE under-budget sample -> below min_sample -> must stay candidate.
    outcomes = [_outcome(task_id="N1", planned_tokens=8000, actual_tokens=5000)]
    payload = ll.validate(outcomes=outcomes, rules_path=rules_path, min_sample=3)
    statuses = {r["key"]: r["status"] for r in payload["rules"]}
    assert all(s != "promoted" for s in statuses.values()), statuses
    assert ll.promoted_rules(rules_path) == []


def test_overrun_rule_without_struggle_not_promoted(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    # engine/role overruns BUT all QA pass and no struggle -> benefit <= 0.
    # Use over-budget tokens so the under_budget family does NOT fire, isolating
    # the effort_overrun family.
    outcomes = [
        _outcome(task_id=f"O{i}", complexity="M", signals=[], engine="codex", role="coder",
                 planned_tokens=25000, actual_tokens=40000, qa_verdict="pass")
        for i in range(4)
    ]
    payload = ll.validate(outcomes=outcomes, rules_path=rules_path, min_sample=3)
    overrun = [r for r in payload["rules"] if r.get("kind") == "effort_overrun"]
    assert overrun, "overrun candidate should be mined"
    # high pass-rate + positive overrun -> struggle_score around -0.25 -> not promoted
    assert overrun[0]["status"] != "promoted"


# --------------------------------------------------------------------------- demotion
def test_promoted_rule_demoted_when_benefit_disappears(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    good = [_outcome(task_id=f"G{i}", planned_tokens=8000, actual_tokens=5000) for i in range(4)]
    ll.validate(outcomes=good, rules_path=rules_path, min_sample=3)
    assert ll.promoted_rules(rules_path)
    # Next round: the condition no longer surfaces (no under-budget S/wire) ->
    # previously promoted rule must be demoted, not silently kept.
    none_matching = [_outcome(task_id="X", complexity="L", signals=[], planned_tokens=80000, actual_tokens=90000)]
    payload = ll.validate(outcomes=none_matching, rules_path=rules_path, min_sample=3)
    demoted = [r for r in payload["rules"] if r["status"] == "demoted"]
    assert demoted, "vanished beneficial rule must be demoted"
    assert ll.promoted_rules(rules_path) == []


# --------------------------------------------------------------------------- summary
def test_summary_returns_live_timestamps_and_metrics(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    outcomes = [_outcome(task_id=f"S{i}", planned_tokens=8000, actual_tokens=4000) for i in range(4)]
    ll.validate(outcomes=outcomes, rules_path=rules_path, min_sample=3)
    summ = ll.summary(rules_path)
    assert summ["updated_at"], "summary must carry a live updated_at timestamp"
    assert summ["promoted_count"] >= 1
    p = summ["promoted"][0]
    assert p["last_validated"]
    assert p["benefit"] is not None
    assert p["sample_size"] >= 3


# --------------------------------------------------------------------------- record_outcome
def test_record_outcome_computes_deltas_and_normalizes_verdict(tmp_path):
    path = tmp_path / "ll.jsonl"
    rec = ll.record_outcome(
        task_id="R1", engine="claude", role="coder", complexity="S",
        task_text="wire 4 images into the gallery",
        qa_verdict="GREEN", success=True,
        planned_tokens=8000, actual_tokens=6000,
        planned_duration_ms=10000, actual_duration_ms=7000, path=path,
    )
    assert rec["qa_verdict"] == "pass"      # GREEN -> pass
    assert rec["token_delta"] == -2000      # under budget
    assert rec["duration_delta_ms"] == -3000
    assert "wire" in rec["signals"]
    loaded = ll._load_outcomes(path)
    assert loaded and loaded[-1]["task_id"] == "R1"


# --------------------------------------------------------------------------- consult no-op
def test_consult_is_noop_when_rules_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert ll.consult({"complexity": "S", "signals": ["wire"]}, path=missing) == []


def test_consult_returns_matching_promoted_rule(tmp_path):
    rules_path = tmp_path / "promoted_rules.json"
    outcomes = [_outcome(task_id=f"C{i}", planned_tokens=8000, actual_tokens=5000) for i in range(4)]
    ll.validate(outcomes=outcomes, rules_path=rules_path, min_sample=3)
    hits = ll.consult({"complexity": "S", "signals": ["wire"]}, path=rules_path)
    assert hits, "a matching promoted rule should be returned"
    miss = ll.consult({"complexity": "XL", "signals": []}, path=rules_path)
    assert miss == []
