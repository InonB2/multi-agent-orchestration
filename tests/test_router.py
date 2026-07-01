"""tests/test_router.py — Phase 4 semantic capability router.

Covers:
  * router excludes a walled engine and reroutes by CAPABILITY (not keyword)
  * load-balancing prefers the LESS-USED engine when capability ties
  * an explicit --engine override still WINS (backward compat)
  * the router NEVER returns a foreign-family model for an engine
  * all-walled fail-open never deadlocks
  * capability_score is deterministic and embedding-ready (signature stable)
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ptme  # noqa: E402
import router  # noqa: E402


# --------------------------------------------------------------------------- helpers
def _all_available(monkeypatch, walled=()):
    """Make every engine available except those in `walled`."""
    def fake_available(engine):
        if engine in walled:
            return False, "rate-walled (test)"
        return True, None
    monkeypatch.setattr(router, "engine_available", fake_available)


def _flat_load(monkeypatch, loads=None):
    """Set deterministic per-engine load. loads: {engine: (weekly_pct, running)}."""
    loads = loads or {}
    def fake_load(engine):
        weekly, running = loads.get(engine, (None, 0))
        return {"weekly_pct": weekly, "running_now": running}
    monkeypatch.setattr(router, "engine_load", fake_load)


def _no_rules(monkeypatch):
    """Disable learning-rule consultation so routing is pure capability/load."""
    monkeypatch.setattr(router, "learning_loop", None)


# --------------------------------------------------------------------------- failover
def test_walled_engine_excluded_and_rerouted_by_capability(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch, walled=("codex",))
    _flat_load(monkeypatch)
    # A coding task best fits codex — but codex is walled. It must reroute to the
    # next most-capable engine (claude has coding-adjacent strengths) NOT just a
    # keyword default.
    result = router.route("implement and refactor a multi-file module")
    assert result["engine"] != "codex"
    assert any(e["engine"] == "codex" for e in result["excluded"])
    assert any("codex" in r for r in result["failover_reasons"])
    assert result["engine"] in ("claude", "agy")


def test_failover_picks_most_capable_available(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch, walled=("codex",))
    _flat_load(monkeypatch)
    # Research task: agy is most capable among non-codex.
    result = router.route("research and compare and evaluate the options, benchmark them")
    assert result["engine"] == "agy"


# --------------------------------------------------------------------------- load balancing
def test_load_balancing_prefers_less_used_engine(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch)
    # Neutral task -> capability roughly tied; codex is heavily loaded (busy +
    # near weekly wall) so a less-used engine must win.
    _flat_load(monkeypatch, loads={
        "codex": (95.0, 4),     # near wall + busy
        "claude": (None, 0),
        "agy": (None, 0),
    })
    result = router.route("do a thing")
    assert result["engine"] != "codex"


def test_load_balancing_breaks_capability_tie_by_running_count(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch)
    # Force a capability tie by using a task with no capability keywords, then
    # only running_now differs.
    _flat_load(monkeypatch, loads={
        "claude": (None, 3),
        "agy": (None, 0),
        "codex": (None, 1),
    })
    result = router.route("xyzzy plugh")  # no capability matches anywhere
    assert result["engine"] == "agy"  # least busy wins


# --------------------------------------------------------------------------- override wins
def test_explicit_override_wins(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch, walled=("codex",))  # codex walled...
    _flat_load(monkeypatch, loads={"codex": (99.0, 9)})  # ...and maxed out
    # ...but an explicit override forces codex anyway (backward compat).
    result = router.route("research the options", override_engine="codex")
    assert result["engine"] == "codex"
    assert result["chosen_via"] == "explicit override"
    # And the model is still codex-family (engine-scoped).
    assert ptme.CAPABILITY_TABLE[result["model"]]["family"] == "codex"


# --------------------------------------------------------------------------- engine scoping
def test_router_never_returns_foreign_family_model(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch)
    _flat_load(monkeypatch)
    for task in [
        "implement a refactor",
        "research and compare",
        "design the security architecture",
        "wire 4 images",
        "do a thing",
    ]:
        for ov in (None, "claude", "codex", "agy"):
            result = router.route(task, override_engine=ov)
            engine = result["engine"]
            model = result["model"]
            assert ptme.CAPABILITY_TABLE[model]["family"] == engine, (
                f"{task!r} ov={ov}: {model} is not {engine}-family"
            )


# --------------------------------------------------------------------------- fail-open
def test_all_walled_fails_open_without_deadlock(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch, walled=("claude", "codex", "agy"))
    _flat_load(monkeypatch)
    result = router.route("implement a refactor")
    assert result["engine"] in router.VALID_ENGINES
    assert "fail-open" in result["chosen_via"]


# --------------------------------------------------------------------------- capability scorer
def test_capability_score_is_deterministic():
    a, ma = router.capability_score("implement and refactor", "codex")
    b, mb = router.capability_score("implement and refactor", "codex")
    assert a == b and ma == mb
    assert a > 0  # coding terms match codex
    # foreign engine for the same task scores lower
    research, _ = router.capability_score("implement and refactor", "agy")
    assert a > research


def test_infer_role_routes_keywords():
    assert router.infer_role("research the market") == "researcher"
    assert router.infer_role("implement the module") == "coder"
    assert router.infer_role("security audit") == "security"


def test_role_advisory_breaks_neutral_tie(monkeypatch):
    _no_rules(monkeypatch)
    _all_available(monkeypatch)
    _flat_load(monkeypatch)
    result = router.route("xyzzy plugh", role="researcher")
    assert result["engine"] == "agy"
    assert result["advisory_engine"] == "agy"
