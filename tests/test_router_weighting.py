"""
tests/test_router_weighting.py — ORCH-19 task_router weighting / negatives / threshold.

Covers:
  * default (unweighted) scoring is identical to the original flat scorer
  * per-keyword weights change the winner
  * negative keywords steer a task away from a provider it would win
  * confidence threshold routes low-confidence tasks to the fallback
  * config/routing.toml is parsed and overlaid (and absent file = defaults)
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from task_router import (  # noqa: E402
    score_task,
    pick_provider,
    load_routing_config,
    _default_weights,
    ROUTING_RULES,
    DEFAULT_PROVIDER,
)


# ---------------------------------------------------------------------------
# Backward-compat: unweighted default == original flat scorer
# ---------------------------------------------------------------------------

def test_default_weights_match_flat_count():
    """With default weights every keyword scores 1 — identical to old counting."""
    task = {"title": "implement the API endpoint", "notes": ""}
    scores = score_task(task)
    # implement + api + endpoint = 3 codex hits, no others
    assert scores["codex"] == 3
    assert scores["antigravity"] == 0
    assert scores["claude-code"] == 0
    assert pick_provider(scores) == "codex"


def test_default_providers_unchanged():
    """Default score keys are exactly the original ROUTING_RULES providers."""
    scores = score_task({"title": "nothing here", "notes": ""})
    assert set(scores) == set(ROUTING_RULES)
    # no keywords -> fallback default
    assert pick_provider(scores) == DEFAULT_PROVIDER


def test_default_weights_helper_is_all_ones():
    w = _default_weights()
    assert all(v == 1 for kwmap in w.values() for v in kwmap.values())


# ---------------------------------------------------------------------------
# Per-keyword weights
# ---------------------------------------------------------------------------

def test_weight_changes_winner_on_tie():
    """A heavier antigravity keyword overturns codex's normal tie-break win."""
    task = {"title": "analyze and fix", "notes": ""}
    # Unweighted: analyze(antigravity)=1, fix(codex)=1 -> tie -> codex wins
    assert pick_provider(score_task(task)) == "codex"

    # Weight 'analyze' to 3: antigravity=3 > codex=1 -> antigravity wins
    weights = _default_weights()
    weights["antigravity"]["analyze"] = 3
    scores = score_task(task, weights=weights)
    assert scores["antigravity"] == 3
    assert scores["codex"] == 1
    assert pick_provider(scores) == "antigravity"


def test_added_keyword_via_weights():
    """Config may introduce a brand-new keyword for a provider."""
    weights = _default_weights()
    weights["claude-code"]["kubernetes"] = 5
    scores = score_task({"title": "deploy kubernetes cluster", "notes": ""}, weights=weights)
    assert scores["claude-code"] == 5
    assert pick_provider(scores) == "claude-code"


# ---------------------------------------------------------------------------
# Negative keywords
# ---------------------------------------------------------------------------

def test_negative_keyword_steers_away():
    """A negative keyword subtracts and flips the routing decision."""
    task = {"title": "implement the design system", "notes": ""}
    # Unweighted: implement -> codex=1, design -> antigravity=1 -> tie -> codex
    assert pick_provider(score_task(task)) == "codex"

    # Penalise codex when 'design' appears: codex 1 - 2 = -1; antigravity stays 1
    negatives = {"codex": {"design": 2}}
    scores = score_task(task, negatives=negatives)
    assert scores["codex"] == -1
    assert scores["antigravity"] == 1
    assert pick_provider(scores) == "antigravity"


def test_all_negative_falls_back():
    """If the only matches are negative, the winner is the fallback provider."""
    negatives = {"codex": {"implement": 5}}
    scores = score_task({"title": "implement", "notes": ""}, negatives=negatives)
    # default weight 'implement' (+1) minus negative (5) = -4 -> still <= 0
    assert scores["codex"] == -4
    assert pick_provider(scores) == DEFAULT_PROVIDER


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------

def test_threshold_routes_low_confidence_to_fallback():
    task = {"title": "fix", "notes": ""}          # codex=1 only
    scores = score_task(task)
    assert scores["codex"] == 1
    # Below threshold 2 -> fallback
    assert pick_provider(scores, threshold=2, fallback="claude-code") == "claude-code"
    # At/above threshold -> normal winner
    assert pick_provider(scores, threshold=1) == "codex"


def test_threshold_zero_is_disabled():
    """threshold=0 (default) never diverts a positive-scoring task."""
    scores = score_task({"title": "fix", "notes": ""})
    assert pick_provider(scores, threshold=0) == "codex"


def test_custom_priority_order():
    """An explicit priority list overrides the default tie-break."""
    task = {"title": "analyze and fix", "notes": ""}   # codex=1, antigravity=1
    scores = score_task(task)
    assert pick_provider(scores, priority=["antigravity", "codex", "claude-code"]) == "antigravity"


# ---------------------------------------------------------------------------
# Config file loading
# ---------------------------------------------------------------------------

def test_load_routing_config_absent_file_defaults():
    cfg = load_routing_config(Path("does-not-exist.toml"))
    assert cfg.threshold == 0
    assert cfg.fallback == DEFAULT_PROVIDER
    assert cfg.weights == _default_weights()
    assert cfg.negatives == {}


def test_load_routing_config_overlays_toml(tmp_path):
    toml = tmp_path / "routing.toml"
    toml.write_text(
        "\n".join([
            "[routing]",
            "confidence_threshold = 3",
            'fallback_provider = "antigravity"',
            "",
            "[routing.priority]",
            'order = ["claude-code", "codex", "antigravity"]',
            "",
            "[weights.codex]",
            '"security audit" = 4',
            "",
            "[negative_keywords.codex]",
            "design = 2",
        ]),
        encoding="utf-8",
    )
    cfg = load_routing_config(toml)
    assert cfg.threshold == 3
    assert cfg.fallback == "antigravity"
    assert cfg.priority == ["claude-code", "codex", "antigravity"]
    assert cfg.weights["codex"]["security audit"] == 4
    # untouched keyword keeps its default weight
    assert cfg.weights["codex"]["implement"] == 1
    assert cfg.negatives["codex"]["design"] == 2

    # And the overlaid config actually drives scoring
    scores = score_task({"title": "security audit of the repo", "notes": ""},
                        weights=cfg.weights, negatives=cfg.negatives)
    assert scores["codex"] == 4
