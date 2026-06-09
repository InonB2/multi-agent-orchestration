"""
tests/test_framework.py — Core correctness tests for multi-agent-orchestration scripts.

Tests import directly from scripts/ using sys.path injection.
Run with:  pytest tests/
"""

import sys
from pathlib import Path
import pytest

# Ensure scripts/ is importable without installing a package
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from checkpoint import _validate_task_id           # noqa: E402
from task_router import score_task, pick_provider  # noqa: E402
from agent_config import deep_merge, get_nested    # noqa: E402


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


def test_routing_default_no_keywords():
    """Tasks with no matching keywords fall back to the default provider (claude-code)."""
    task = {"title": "handle the thing", "notes": ""}
    assert pick_provider(score_task(task)) == "claude-code"


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
