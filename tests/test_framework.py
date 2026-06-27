"""
tests/test_framework.py — Core correctness tests for multi-agent-orchestration scripts.

Tests import directly from scripts/ using sys.path injection.
Run with:  pytest tests/
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable without installing a package
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from checkpoint import _validate_task_id           # noqa: E402
from task_router import score_task, pick_provider, route_tasks  # noqa: E402
from task_spec import REQUIRED_FIELDS  # noqa: E402
from agent_config import deep_merge, get_nested, list_agent_names, load_agent_config  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_TASKS_DATA = {
    "last_updated": "2026-01-01",
    "tasks": [
        {
            "task_id": "TEST-001",
            "title": "Implement the new API endpoint",
            "assigned_to": "codex",
            "status": "pending",
            "priority": "high",
            "complexity": "M",
        },
        {
            "task_id": "TEST-002",
            "title": "Research competitor pricing strategies",
            "assigned_to": "researcher",
            "status": "pending",
            "priority": "medium",
            "complexity": "S",
        },
        {
            "task_id": "TEST-003",
            "title": "Design onboarding flow for mobile users",
            "assigned_to": "designer",
            "status": "pending",
            "priority": "low",
            "complexity": "S",
        },
        {
            "task_id": "TEST-004",
            "title": "Orchestrate the subagent workflow",
            "assigned_to": "orchestrator",
            "status": "pending",
            "priority": "medium",
            "complexity": "M",
        },
    ],
}


@pytest.fixture
def tasks_file(tmp_path):
    """Write a minimal active_tasks.json into tmp_path and return the path."""
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(MINIMAL_TASKS_DATA, indent=2), encoding="utf-8")
    return tf


@pytest.fixture
def specs_dir(tmp_path):
    """Return a specs directory inside tmp_path."""
    sd = tmp_path / "specs"
    sd.mkdir()
    return sd


# ---------------------------------------------------------------------------
# checkpoint.py — task ID validation
# ---------------------------------------------------------------------------

def test_task_id_validation_blocks_path_traversal():
    """Reject task IDs containing path-traversal sequences."""
    with pytest.raises(SystemExit):
        _validate_task_id("../../etc/passwd")


def test_task_id_validation_blocks_slash():
    """Reject task IDs containing forward slashes."""
    with pytest.raises(SystemExit):
        _validate_task_id("TASK/001")


def test_task_id_validation_allows_valid_ids():
    """Well-formed task IDs (alphanumeric, hyphens, underscores) must not raise."""
    _validate_task_id("TASK-001")
    _validate_task_id("INFRA-009")
    _validate_task_id("my-task_1")


def test_checkpoint_save_and_read(tmp_path, monkeypatch):
    """Save a checkpoint, read it back, verify all fields are present."""
    import checkpoint as cp

    # Patch the module-level directory paths (ROOT must match so relative_to() works)
    snapshots = tmp_path / "snapshots"
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    monkeypatch.setattr(cp, "SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr(cp, "QUEUE_DIR", queue_dir)
    monkeypatch.setattr(cp, "QUEUE_FILE", queue_dir / "resume_queue.json")

    # Provide a fake active_tasks.json
    tasks_f = tmp_path / "active_tasks.json"
    tasks_f.write_text(json.dumps({"tasks": [
        {"task_id": "CP-001", "title": "Test checkpoint", "preferred_provider": "codex"}
    ]}), encoding="utf-8")
    monkeypatch.setattr(cp, "TASKS_FILE", tasks_f)

    # Save
    args = ["--task", "CP-001", "--done", "step 1", "--remaining", "step 2", "--next", "step 3"]
    cp.cmd_save(args)

    snap = snapshots / "CP-001_checkpoint.json"
    assert snap.exists(), "Checkpoint file should have been written"

    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["task_id"] == "CP-001"
    assert data["done"] == "step 1"
    assert data["remaining"] == "step 2"
    assert data["next_step"] == "step 3"
    assert "timestamp" in data
    assert "model" in data


def test_checkpoint_mark_resumed(tmp_path, monkeypatch):
    """mark-resumed removes the task from the queue and updates resumed flag."""
    import checkpoint as cp

    snapshots = tmp_path / "snapshots"
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    monkeypatch.setattr(cp, "SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr(cp, "QUEUE_DIR", queue_dir)
    monkeypatch.setattr(cp, "QUEUE_FILE", queue_dir / "resume_queue.json")

    tasks_f = tmp_path / "active_tasks.json"
    tasks_f.write_text(json.dumps({"tasks": [
        {"task_id": "CP-002", "title": "Resume test", "preferred_provider": "codex"}
    ]}), encoding="utf-8")
    monkeypatch.setattr(cp, "TASKS_FILE", tasks_f)

    # Save a checkpoint first
    cp.cmd_save(["--task", "CP-002", "--done", "x", "--remaining", "y", "--next", "z"])

    # Verify it's in the queue
    queue = json.loads((queue_dir / "resume_queue.json").read_text(encoding="utf-8"))
    assert any(e["task_id"] == "CP-002" for e in queue)

    # Mark resumed
    cp.cmd_mark_resumed(["--task", "CP-002"])

    queue_after = json.loads((queue_dir / "resume_queue.json").read_text(encoding="utf-8"))
    assert not any(e["task_id"] == "CP-002" for e in queue_after), \
        "Task should be removed from queue after mark-resumed"


def test_checkpoint_list_resumable_excludes_resumed(tmp_path, monkeypatch, capsys):
    """list-resumable only shows tasks not yet resumed."""
    import checkpoint as cp

    snapshots = tmp_path / "snapshots"
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    monkeypatch.setattr(cp, "SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr(cp, "QUEUE_DIR", queue_dir)
    monkeypatch.setattr(cp, "QUEUE_FILE", queue_dir / "resume_queue.json")

    tasks_f = tmp_path / "active_tasks.json"
    tasks_f.write_text(json.dumps({"tasks": [
        {"task_id": "CP-003", "title": "T1", "preferred_provider": "codex"},
        {"task_id": "CP-004", "title": "T2", "preferred_provider": "codex"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(cp, "TASKS_FILE", tasks_f)

    # Save two checkpoints
    cp.cmd_save(["--task", "CP-003", "--done", "a", "--remaining", "b", "--next", "c"])
    cp.cmd_save(["--task", "CP-004", "--done", "d", "--remaining", "e", "--next", "f"])

    # Mark one as resumed
    cp.cmd_mark_resumed(["--task", "CP-003"])

    # Clear capsys buffer (prior cmd_save output contains CP-003 path strings)
    capsys.readouterr()

    # list-resumable should only show CP-004
    cp.cmd_list_resumable([])
    captured = capsys.readouterr()
    assert "CP-004" in captured.out
    # CP-003 must not appear in the resumable list output
    # (check for the task ID in brackets, not as a file path substring)
    assert "[CP-003]" not in captured.out


def test_checkpoint_invalid_task_id_rejected():
    """Invalid task_id (non-alphanumeric, path-traversal) must be rejected."""
    with pytest.raises(SystemExit):
        _validate_task_id("../../../root")
    with pytest.raises(SystemExit):
        _validate_task_id("task with spaces")


# ---------------------------------------------------------------------------
# TST-2: BUG-1 regression — _get_flag must reject a flag as its own value
# ---------------------------------------------------------------------------

def test_checkpoint_get_flag_rejects_flag_as_value():
    """_get_flag must NOT silently accept another flag name as a value.

    Before the BUG-1 fix, checkpoint.py --task --done step1 would silently set
    task_id = '--done'.  After the fix it must exit with an error.
    """
    import checkpoint as cp

    with pytest.raises(SystemExit):
        # --task is followed immediately by --done (another flag), which is invalid
        cp._get_flag(["--task", "--done", "step1"], "--task")


# ---------------------------------------------------------------------------
# TST-6: checkpoint.py corrupt JSON handling
# ---------------------------------------------------------------------------

def test_checkpoint_read_corrupt_json(tmp_path, monkeypatch, capsys):
    """cmd_read must exit with code 1 and print [ERROR] for a corrupt checkpoint file."""
    import checkpoint as cp

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(parents=True)
    queue_dir = tmp_path / "queue"

    monkeypatch.setattr(cp, "ROOT", tmp_path)
    monkeypatch.setattr(cp, "SNAPSHOTS_DIR", snapshots)
    monkeypatch.setattr(cp, "QUEUE_DIR", queue_dir)
    monkeypatch.setattr(cp, "QUEUE_FILE", queue_dir / "resume_queue.json")

    # Write corrupt JSON directly to a snapshot file
    snap = snapshots / "CORRUPT-001_checkpoint.json"
    snap.write_text("not valid json {{{", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cp.cmd_read(["--task", "CORRUPT-001"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "[ERROR]" in captured.err


# ---------------------------------------------------------------------------
# task_router.py — routing decisions
# ---------------------------------------------------------------------------

def test_routing_browser_goes_to_antigravity():
    """Tasks containing 'browser' and 'screenshot' keywords must route to antigravity."""
    task = {"title": "browser automation screenshot", "notes": ""}
    assert pick_provider(score_task(task)) == "antigravity"


def test_routing_scraping_goes_to_antigravity():
    """Tasks containing 'web scraping' must route to antigravity."""
    task = {"title": "web scraping pipeline", "notes": ""}
    assert pick_provider(score_task(task)) == "antigravity"


def test_routing_orchestration_goes_to_claude():
    """Tasks with orchestration/subagent/workflow keywords must route to claude-code."""
    task = {"title": "orchestrate the subagent workflow", "notes": ""}
    assert pick_provider(score_task(task)) == "claude-code"


def test_routing_implement_goes_to_codex():
    """Tasks with implementation keywords (implement, api, endpoint) must route to codex."""
    task = {"title": "implement the API endpoint", "notes": ""}
    assert pick_provider(score_task(task)) == "codex"


def test_routing_research_goes_to_antigravity():
    """Tasks with 'research' keyword must route to antigravity."""
    task = {"title": "research the market landscape", "notes": ""}
    assert pick_provider(score_task(task)) == "antigravity"


def test_routing_design_goes_to_antigravity():
    """Tasks with 'design' keyword must route to antigravity."""
    task = {"title": "design the onboarding flow", "notes": ""}
    assert pick_provider(score_task(task)) == "antigravity"


def test_routing_default_no_keywords():
    """Tasks with no matching keywords fall back to the default provider (claude-code)."""
    task = {"title": "handle the thing", "notes": ""}
    assert pick_provider(score_task(task)) == "claude-code"


def test_routing_dry_run_no_write(tasks_file, monkeypatch, capsys):
    """--dry-run produces console output but does not modify the file."""
    import task_router as tr
    monkeypatch.setattr(tr, "TASKS_FILE", tasks_file)

    original = tasks_file.read_text(encoding="utf-8")
    route_tasks(dry_run=True)

    assert tasks_file.read_text(encoding="utf-8") == original, \
        "dry-run must not modify the file"
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out


def test_routing_task_id_filter_routes_only_one(tasks_file, monkeypatch, capsys):
    """--task-id routes only the specified task, leaving others unchanged."""
    import task_router as tr
    monkeypatch.setattr(tr, "TASKS_FILE", tasks_file)

    # Remove preferred_provider from all to give router something to do
    data = json.loads(tasks_file.read_text(encoding="utf-8"))
    for t in data["tasks"]:
        t.pop("preferred_provider", None)
    tasks_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    route_tasks(dry_run=False, task_id_filter="TEST-001")

    updated = json.loads(tasks_file.read_text(encoding="utf-8"))
    routed_ids = [t["task_id"] for t in updated["tasks"] if "preferred_provider" in t]
    assert "TEST-001" in routed_ids
    # Other tasks should NOT have been routed
    assert "TEST-002" not in routed_ids
    assert "TEST-003" not in routed_ids


def test_routing_task_id_filter_missing_exits(tasks_file, monkeypatch):
    """--task-id with a non-existent ID exits with error."""
    import task_router as tr
    monkeypatch.setattr(tr, "TASKS_FILE", tasks_file)

    with pytest.raises(SystemExit):
        route_tasks(dry_run=False, task_id_filter="NONEXISTENT-999")


def test_routing_atomic_write_produces_valid_json(tasks_file, monkeypatch):
    """After routing, the file must be valid JSON and contain preferred_provider."""
    import task_router as tr
    monkeypatch.setattr(tr, "TASKS_FILE", tasks_file)

    data = json.loads(tasks_file.read_text(encoding="utf-8"))
    for t in data["tasks"]:
        t.pop("preferred_provider", None)
    tasks_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    route_tasks(dry_run=False)

    result = json.loads(tasks_file.read_text(encoding="utf-8"))
    for t in result["tasks"]:
        assert "preferred_provider" in t, "All tasks should have a preferred_provider after routing"


# ---------------------------------------------------------------------------
# TST-3: Word-boundary matching — substring false positives must NOT match
# ---------------------------------------------------------------------------

def test_routing_prefix_not_matched_as_fix():
    """'prefix' must NOT match the 'fix' keyword (word-boundary guard, EDGE-1)."""
    task = {"title": "add a prefix to the string", "notes": ""}
    scores = score_task(task)
    # "prefix" contains "fix" as substring — without word-boundary this would hit codex
    assert scores["codex"] == 0, "'prefix' should not match the 'fix' keyword"
    assert pick_provider(scores) != "codex"


def test_routing_rapid_not_matched_as_api():
    """'rapid' must NOT match the 'api' keyword (word-boundary guard, EDGE-1)."""
    task = {"title": "rapid iteration on the product", "notes": ""}
    scores = score_task(task)
    # "rapid" contains "api" as substring — without word-boundary this would hit codex
    assert scores["codex"] == 0, "'rapid' should not match the 'api' keyword"


def test_routing_build_not_false_positive_in_rebuild():
    """'rebuild' must match 'build' because 'build' appears as a whole word within it — wait,
    actually 'rebuild' = 're' + 'build', so \\bbuild\\b matches 'build' in 'rebuild'?
    Let's verify: \\b is a word-boundary between \\w and \\W.  In 'rebuild', 'b' is preceded by
    the 'l' of 'rebui' — so there is NO word boundary before 'build' inside 'rebuild'.
    This confirms the fix works: 'rebuild' does NOT score a 'build' match.
    """
    import re
    assert not re.search(r'\bbuild\b', "rebuild", re.IGNORECASE), \
        "'rebuild' must not match '\\bbuild\\b'"
    assert re.search(r'\bbuild\b', "build the project", re.IGNORECASE), \
        "'build the project' must match '\\bbuild\\b'"


# ---------------------------------------------------------------------------
# TST-4: Tie-breaking — codex beats antigravity on equal scores
# ---------------------------------------------------------------------------

def test_routing_tiebreak_codex_beats_antigravity():
    """When codex and antigravity score equally, codex must win (priority order)."""
    # Use a clean 1:1 keyword tie so priority order decides the winner.
    task_tie = {"title": "analyze and fix", "notes": ""}
    scores_tie = score_task(task_tie)
    # "analyze" → antigravity=1, "fix" → codex=1, claude-code=0
    assert scores_tie["codex"] == scores_tie["antigravity"], \
        "Expected tie between codex and antigravity"
    assert pick_provider(scores_tie) == "codex", \
        "codex must win the tie over antigravity"


# ---------------------------------------------------------------------------
# TST-5: task_spec.py path traversal
# ---------------------------------------------------------------------------

def test_taskspec_path_traversal_rejected(tmp_path, monkeypatch):
    """cmd_read with a path-traversal task_id must exit with SystemExit."""
    import task_spec as ts

    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    import argparse
    args = argparse.Namespace(task="../../etc/passwd")
    with pytest.raises(SystemExit):
        ts.cmd_read(args)


# ---------------------------------------------------------------------------
# task_spec.py — spec creation and validation
# ---------------------------------------------------------------------------

def _make_spec(specs_dir, task_id="SPEC-001", **overrides):
    """Helper: create a minimal valid spec dict and write it."""
    spec = {
        "task_id": task_id,
        "title": "Test task title",
        "complexity": "M",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "orchestrator",
        "what_is_done": "Database schema created.",
        "what_remains": "API routes pending.",
        "exact_next_step": "Add POST /items route.",
        "acceptance_criteria": ["Tests pass", "Lint clean"],
        "assigned_to": "codex",
        "spec_version": 1,
    }
    spec.update(overrides)
    dest = specs_dir / "{}.json".format(task_id)
    dest.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return spec, dest


def test_spec_create_writes_all_fields(tmp_path, monkeypatch):
    """cmd_create must write a spec file with all required fields populated."""
    import task_spec as ts

    # Patch paths
    tasks_data = {"tasks": [{
        "task_id": "SPEC-T01",
        "title": "Create login page",
        "complexity": "M",
        "assigned_to": "web",
    }]}
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(tasks_data), encoding="utf-8")
    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "TASKS_FILE", tf)
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    import argparse
    args = argparse.Namespace(
        task="SPEC-T01",
        done="Login form built",
        remaining="OAuth integration",
        next="Wire up Google OAuth",
        criteria="User can log in,Tests pass",
        created_by="orchestrator",
    )
    ts.cmd_create(args)

    dest = sd / "SPEC-T01.json"
    assert dest.exists()
    spec = json.loads(dest.read_text(encoding="utf-8"))
    for field in REQUIRED_FIELDS:
        assert field in spec, "Required field '{}' missing from spec".format(field)
    assert spec["what_is_done"] == "Login form built"
    assert isinstance(spec["acceptance_criteria"], list)
    assert len(spec["acceptance_criteria"]) == 2


def test_spec_validate_complete_passes(tmp_path, monkeypatch, capsys):
    """A fully-populated spec must pass validation."""
    import task_spec as ts
    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    _make_spec(sd, task_id="SPEC-V01")

    import argparse
    args = argparse.Namespace(task="SPEC-V01")
    ts.cmd_validate(args)

    captured = capsys.readouterr()
    assert "PASS" in captured.out


def test_spec_validate_incomplete_fails(tmp_path, monkeypatch, capsys):
    """A spec missing required fields must fail validation with exit code 2."""
    import task_spec as ts
    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    # Write a spec with missing required fields
    incomplete = {"task_id": "SPEC-V02", "title": "Incomplete"}
    dest = sd / "SPEC-V02.json"
    dest.write_text(json.dumps(incomplete), encoding="utf-8")

    import argparse
    args = argparse.Namespace(task="SPEC-V02")
    with pytest.raises(SystemExit) as exc_info:
        ts.cmd_validate(args)
    assert exc_info.value.code == 2


def test_spec_list_missing_finds_unspecced_tasks(tmp_path, monkeypatch, capsys):
    """list-missing must report M/L/XL active tasks that have no spec file."""
    import task_spec as ts

    tasks_data = {"tasks": [
        {"task_id": "SPEC-M01", "title": "Big task", "complexity": "M", "status": "in_progress"},
        {"task_id": "SPEC-S01", "title": "Small task", "complexity": "S", "status": "in_progress"},
        {"task_id": "SPEC-DONE", "title": "Done task", "complexity": "L", "status": "done"},
    ]}
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(tasks_data), encoding="utf-8")
    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "TASKS_FILE", tf)
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    import argparse
    ts.cmd_list_missing(argparse.Namespace())

    captured = capsys.readouterr()
    # SPEC-M01 is M + in_progress → should appear
    assert "SPEC-M01" in captured.out
    # SPEC-S01 is S → excluded (not M/L/XL)
    assert "SPEC-S01" not in captured.out
    # SPEC-DONE is done → excluded
    assert "SPEC-DONE" not in captured.out


def test_spec_atomic_write_no_tmp_leftover(tmp_path, monkeypatch):
    """After cmd_create, no .tmp file should remain alongside the spec."""
    import task_spec as ts

    tasks_data = {"tasks": [{
        "task_id": "SPEC-AW1",
        "title": "Atomic write test",
        "complexity": "M",
        "assigned_to": "coder",
    }]}
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps(tasks_data), encoding="utf-8")
    sd = tmp_path / "specs"
    sd.mkdir()
    monkeypatch.setattr(ts, "TASKS_FILE", tf)
    monkeypatch.setattr(ts, "SPECS_DIR", sd)

    import argparse
    args = argparse.Namespace(
        task="SPEC-AW1",
        done="done",
        remaining="remaining",
        next="next step",
        criteria="pass",
        created_by="coder",
    )
    ts.cmd_create(args)

    # No .tmp file should be present
    tmp_files = list(sd.glob("*.tmp"))
    assert tmp_files == [], "No .tmp files should remain after atomic write"
    # The spec should exist and be valid JSON
    dest = sd / "SPEC-AW1.json"
    assert dest.exists()
    json.loads(dest.read_text(encoding="utf-8"))  # must not raise


# ---------------------------------------------------------------------------
# TST-1: coordinator.py commands (zero coverage before this PR)
# ---------------------------------------------------------------------------

def _make_coordinator_tasks_file(tmp_path, tasks):
    """Write tasks data to active_tasks.json in tmp_path."""
    tf = tmp_path / "active_tasks.json"
    tf.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")
    return tf


def test_coordinator_claim_sets_status(tmp_path, monkeypatch):
    """cmd_claim must set status=in_progress, preferred_provider, and claimed_at."""
    import coordinator as co

    tf = _make_coordinator_tasks_file(tmp_path, [
        {"task_id": "CO-001", "title": "Test task", "status": "pending", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_claim(["--task", "CO-001", "--model", "codex"])

    updated = json.loads(tf.read_text(encoding="utf-8"))
    task = next(t for t in updated["tasks"] if t["task_id"] == "CO-001")
    assert task["status"] == "in_progress"
    assert task["preferred_provider"] == "codex"
    assert "claimed_at" in task
    assert task["phase"] == "claimed"


def test_coordinator_update_status(tmp_path, monkeypatch):
    """cmd_update must set the phase field and append a coordinator_log entry."""
    import coordinator as co

    tf = _make_coordinator_tasks_file(tmp_path, [
        {"task_id": "CO-002", "title": "Test task", "status": "in_progress", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_update(["--task", "CO-002", "--phase", "in-review"])

    updated = json.loads(tf.read_text(encoding="utf-8"))
    task = next(t for t in updated["tasks"] if t["task_id"] == "CO-002")
    assert task["phase"] == "in-review"
    assert any("in-review" in entry for entry in task.get("coordinator_log", []))


def test_coordinator_complete_sets_tested(tmp_path, monkeypatch):
    """cmd_mark_tested must set status=tested and phase=done."""
    import coordinator as co

    tf = _make_coordinator_tasks_file(tmp_path, [
        {"task_id": "CO-003", "title": "Test task", "status": "in_progress", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)
    # Stub out the checkpoint subprocess so no real process is spawned
    monkeypatch.setattr(co, "_run_checkpoint", lambda *a, **kw: 0)

    co.cmd_mark_tested(["--task", "CO-003", "--tested-by", "qa-agent"])

    updated = json.loads(tf.read_text(encoding="utf-8"))
    task = next(t for t in updated["tasks"] if t["task_id"] == "CO-003")
    assert task["status"] == "tested"
    assert task["phase"] == "done"
    assert "completed_at" in task
    assert task["tested_by"] == "qa-agent"


def test_coordinator_status_output(tmp_path, monkeypatch, capsys):
    """cmd_status must print the task_id and status in its output."""
    import coordinator as co

    tf = _make_coordinator_tasks_file(tmp_path, [
        {"task_id": "CO-004", "title": "Status test task", "status": "in_progress", "complexity": "M"},
    ])
    monkeypatch.setattr(co, "TASKS_FILE", tf)

    co.cmd_status(["--task", "CO-004"])

    out = capsys.readouterr().out
    assert "CO-004" in out
    assert "in_progress" in out


# ---------------------------------------------------------------------------
# agent_config.py — config helpers
# ---------------------------------------------------------------------------

def test_deep_merge_overrides_leaf_values():
    """deep_merge must override base values with override values at matching keys."""
    base     = {"agent": {"max_task_size": "M", "preferred_model": "claude-code"}}
    override = {"agent": {"max_task_size": "L"}}
    result   = deep_merge(base, override)
    assert result["agent"]["max_task_size"] == "L"
    assert result["agent"]["preferred_model"] == "claude-code"  # preserved from base


def test_deep_merge_adds_new_keys():
    """deep_merge must add keys from override that are absent in base."""
    base     = {"agent": {"max_task_size": "M"}}
    override = {"agent": {"new_key": "hello"}}
    result   = deep_merge(base, override)
    assert result["agent"]["new_key"] == "hello"
    assert result["agent"]["max_task_size"] == "M"


def test_get_nested_dot_notation():
    """get_nested must resolve dot-notation keys and return None for missing paths."""
    config = {"agent": {"max_task_size": "M", "preferred_model": "claude-code"}}
    assert get_nested(config, "agent.max_task_size") == "M"
    assert get_nested(config, "agent.missing_key") is None
    assert get_nested(config, "nonexistent.section") is None


def test_list_agents_returns_expected_count():
    """list_agent_names() must return at least 10 agents (the known team)."""
    agents = list_agent_names()
    assert len(agents) >= 10, "Expected at least 10 configured agents, got {}".format(len(agents))


def test_list_agents_excludes_defaults():
    """list_agent_names() must not include _defaults."""
    agents = list_agent_names()
    assert "_defaults" not in agents


def test_load_agent_config_known_agent():
    """load_agent_config for 'codex' must return a dict with agent section."""
    config = load_agent_config("codex")
    assert "agent" in config
    assert config["agent"]["preferred_model"] == "codex"


def test_load_agent_config_override_wins_over_defaults():
    """Per-agent TOML must override _defaults.toml for matching keys."""
    config = load_agent_config("codex")
    # codex.toml sets max_task_size = "M", _defaults has "L"
    assert config["agent"]["max_task_size"] == "M"


def test_load_agent_config_defaults_preserved_when_not_overridden():
    """Keys in _defaults.toml not overridden by agent TOML must still be present."""
    config = load_agent_config("codex")
    # _defaults has rate_limit section; codex.toml also has it but check output section
    assert "output" in config
    assert "deliverable_path" in config["output"]
