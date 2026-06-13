#!/usr/bin/env python3
"""
task_router.py — Route tasks to the best provider based on keyword matching.

Reads tasks/active_tasks.json, scores each task's title + notes against
keyword lists for each provider, and assigns preferred_provider.

Usage:
    python scripts/task_router.py                      # updates active_tasks.json
    python scripts/task_router.py --dry-run            # preview only
    python scripts/task_router.py --task-id TASK-001   # route a single task by ID
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# TOML import — stdlib (Python 3.11+) with fallback to tomli
# ---------------------------------------------------------------------------
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

ROOT       = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"
_CONFIG_DIR = ROOT / "config" / "agents"

ROUTING_RULES = {
    "codex": [
        "implement", "refactor", "pr", "pull request", "api", "endpoint",
        "migration", "schema", "fix", "bug", "patch", "unit test", "ci",
        "build", "compile", "typescript", "python script", "security audit",
        "adversarial review", "code review", "lint",
    ],
    "antigravity": [
        "research", "summarize", "analyze", "plan", "design", "ui", "ux",
        "browser", "visual", "screenshot", "e2e", "end-to-end", "artifact",
        "document", "report", "comparison", "evaluation", "benchmark",
        "scraping", "scrape", "web scraping",
    ],
    "claude-code": [
        "orchestrate", "delegate", "architect", "coordinate", "multi-file",
        "debug", "trace", "subagent", "workflow",
    ],
}

DEFAULT_PROVIDER = "claude-code"

# ---------------------------------------------------------------------------
# ORCH-19 — keyword weighting, negative keywords, confidence threshold.
#
# BACKWARD-COMPAT: with no config file present the effective behaviour is
# IDENTICAL to the original flat scorer — every keyword weighs 1, there are no
# negative keywords, and the confidence threshold is 0 (disabled). Nothing
# silently re-routes unless config/routing.toml opts in.
# ---------------------------------------------------------------------------
ROUTING_CONFIG_FILE = ROOT / "config" / "routing.toml"

DEFAULT_KEYWORD_WEIGHT       = 1
DEFAULT_CONFIDENCE_THRESHOLD = 0          # 0 = disabled (original behaviour)
# Tie-break / scan order. Original priority preserved.
PRIORITY_ORDER = ["codex", "antigravity", "claude-code"]


class RoutingConfig:
    """Resolved routing knobs: weights, negative keywords, threshold, fallback."""

    def __init__(self, weights, negatives, threshold, fallback, priority):
        self.weights   = weights       # {provider: {keyword: weight}}
        self.negatives = negatives     # {provider: {keyword: weight}}
        self.threshold = threshold     # float/int; 0 disables the gate
        self.fallback  = fallback      # provider used below threshold / on zero
        self.priority  = priority      # tie-break order (list of providers)


def _default_weights() -> dict:
    """Weight map equivalent to the original flat scorer (every keyword = 1)."""
    return {
        provider: {kw: DEFAULT_KEYWORD_WEIGHT for kw in keywords}
        for provider, keywords in ROUTING_RULES.items()
    }


def load_routing_config(path: Path = None) -> RoutingConfig:
    """Build a RoutingConfig, overlaying config/routing.toml onto the defaults.

    A missing/empty/malformed file yields the original behaviour (see
    _load_toml_safe, which never raises). Recognised TOML schema::

        [routing]
        confidence_threshold = 2          # below this -> fallback (0 = off)
        fallback_provider     = "claude-code"

        [routing.priority]
        order = ["codex", "antigravity", "claude-code"]

        [weights.codex]                   # override / add keyword weights
        "security audit" = 3

        [negative_keywords.codex]         # subtract on match
        design = 2
    """
    if path is None:
        path = ROUTING_CONFIG_FILE
    cfg = _load_toml_safe(path, "routing")

    weights = _default_weights()
    for provider, kwmap in (cfg.get("weights") or {}).items():
        bucket = weights.setdefault(provider, {})
        for kw, weight in kwmap.items():
            bucket[kw] = weight

    negatives = {}
    for provider, kwmap in (cfg.get("negative_keywords") or {}).items():
        negatives[provider] = dict(kwmap)

    routing   = cfg.get("routing") or {}
    threshold = routing.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    fallback  = routing.get("fallback_provider", DEFAULT_PROVIDER)
    priority  = (routing.get("priority") or {}).get("order") or list(PRIORITY_ORDER)

    return RoutingConfig(weights, negatives, threshold, fallback, priority)


def _keyword_matches(keyword: str, text: str) -> bool:
    """Word-boundary, case-insensitive match (EDGE-1) — prevents 'prefix'~'fix'."""
    return re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE) is not None


# ---------------------------------------------------------------------------
# File-lock helpers (cross-platform sidecar-file pattern)  [REL-2]
# ---------------------------------------------------------------------------

def _acquire_lock(lock_path: Path, timeout: int = 10) -> bool:
    """Try to create *lock_path* exclusively. Returns True on success, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.05)
    return False


def _release_lock(lock_path: Path) -> None:
    """Delete the sidecar lock file, ignoring missing-file errors."""
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Provider-type lookup (for enriched routing output)
# ---------------------------------------------------------------------------

def _load_toml_safe(path: Path, agent_name: str = "") -> dict:
    """Load a TOML file; return {} on any error (never raises).

    REL-4: warns on parse error so misconfigured agent configs are surfaced.
    """
    if tomllib is None or not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if agent_name:
            print(
                "[WARN] Could not parse TOML for agent '{}': {}".format(agent_name, exc),
                file=sys.stderr,
            )
        return {}


def _get_provider_info(agent_name: str) -> tuple:
    """Return (provider_type, model_id) for *agent_name*. Defaults to ('cli', None)."""
    try:
        defaults  = _load_toml_safe(_CONFIG_DIR / "_defaults.toml")
        agent_cfg = _load_toml_safe(
            _CONFIG_DIR / "{}.toml".format(agent_name.lower()),
            agent_name,
        )
        # Simple merge: agent overrides defaults for provider section
        merged_provider = dict(defaults.get("provider", {}))
        merged_provider.update(agent_cfg.get("provider", {}))
        ptype    = merged_provider.get("type", "cli")
        model_id = merged_provider.get("model_id")
        return ptype, model_id
    except Exception:
        return "cli", None


def score_task(task: dict, weights: dict = None, negatives: dict = None) -> dict:
    """Score a task's title + notes against each provider's weighted keywords.

    EDGE-1: word-boundary regex (not substring) prevents false positives like
    "prefix" matching "fix" or "rapid" matching "api".

    ORCH-19:
      * *weights*   — {provider: {keyword: weight}}. Defaults to the flat map
        (every keyword weighs 1), so the unweighted result is byte-identical to
        the original scorer.
      * *negatives* — {provider: {keyword: weight}}. Each matched negative
        keyword SUBTRACTS its weight from that provider's score, letting config
        steer a task away from a provider it would otherwise win.
    """
    if weights is None:
        weights = _default_weights()
    if negatives is None:
        negatives = {}

    text = " ".join([
        task.get("title", ""),
        task.get("notes", "") or "",
    ])

    providers = set(weights) | set(negatives)
    scores = {provider: 0 for provider in providers}
    for provider in providers:
        for kw, weight in weights.get(provider, {}).items():
            if _keyword_matches(kw, text):
                scores[provider] += weight
        for kw, weight in negatives.get(provider, {}).items():
            if _keyword_matches(kw, text):
                scores[provider] -= weight
    return scores


def pick_provider(scores: dict, threshold=0, fallback: str = None,
                  priority: list = None) -> str:
    """Return the best-scoring provider.

    On equal scores, *priority* order decides (default codex > antigravity >
    claude-code). Falls back to *fallback* (default claude-code) when every
    score is <= 0, or — ORCH-19 — when the best score is below *threshold*
    (a positive threshold; 0 disables the gate, preserving original behaviour).
    """
    if fallback is None:
        fallback = DEFAULT_PROVIDER
    if priority is None:
        priority = PRIORITY_ORDER
    if not scores:
        return fallback

    best_score = max(scores.values())
    if best_score <= 0:
        return fallback
    if threshold and best_score < threshold:
        return fallback

    for provider in priority:
        if scores.get(provider, 0) == best_score:
            return provider
    return fallback


def route_tasks(dry_run=False, task_id_filter=None):
    # Load tasks (no lock needed for dry-run; lock acquired before write)
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found at {}".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] Failed to parse tasks/active_tasks.json: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print(
            '[ERROR] active_tasks.json must be an object with a "tasks" array '
            '(e.g. {"tasks": [...]}), but got a top-level '
            + type(data).__name__ + '.',
            file=sys.stderr,
        )
        sys.exit(1)

    all_tasks = data.get("tasks", [])

    # If a specific task ID was requested, filter to just that task
    if task_id_filter:
        tasks = [t for t in all_tasks if t.get("task_id") == task_id_filter]
        if not tasks:
            print("[ERROR] Task '{}' not found in active_tasks.json.".format(task_id_filter),
                  file=sys.stderr)
            sys.exit(1)
    else:
        tasks = all_tasks

    # ORCH-19: load weighting/threshold config (no-op default = original flow)
    cfg = load_routing_config()

    counters = {provider: 0 for provider in ROUTING_RULES}
    routed_count = 0

    for task in tasks:
        # A null/absent preferred_provider means "unrouted" per the documented
        # task schema, so only skip tasks that already carry a real provider.
        existing_provider = task.get("preferred_provider")
        if existing_provider:
            # EDGE-2: inform the operator instead of silently skipping
            tid = task.get("task_id", "?")
            print("[INFO] Task '{}' already has preferred_provider='{}' — skipping.".format(
                tid, existing_provider
            ))
            continue

        scores = score_task(task, cfg.weights, cfg.negatives)
        provider = pick_provider(scores, cfg.threshold, cfg.fallback, cfg.priority)

        task_id = task.get("task_id", "?")
        title   = task.get("title", "(no title)")

        # ORCH-19: surface low-confidence routings (best > 0 but under threshold)
        best_score = max(scores.values()) if scores else 0
        if cfg.threshold and 0 < best_score < cfg.threshold:
            print("[INFO] Task '{}' low-confidence (best score {} < threshold {}) "
                  "-> routed to fallback '{}'.".format(
                      task_id, best_score, cfg.threshold, provider))

        score_summary = ", ".join("{}={}".format(p, s) for p, s in scores.items())

        # Look up provider type for enriched output
        ptype, model_id = _get_provider_info(provider)
        if ptype == "api" and model_id:
            provider_label = "[provider: api | model: {}]".format(model_id)
        else:
            provider_label = "[provider: {}]".format(ptype)

        # Strip non-ASCII characters to avoid cp1252 encoding errors on Windows console
        safe_title = title[:60].encode("ascii", errors="replace").decode("ascii")
        print("  {}: '{}' -> {}  {}  (scores: {})".format(
            task_id, safe_title, provider, provider_label, score_summary
        ))

        if not dry_run:
            task["preferred_provider"] = provider

        counters[provider] += 1
        routed_count += 1

    if dry_run:
        summary_parts = ["{} -> {}".format(n, p) for p, n in counters.items()]
        print("\n[DRY-RUN] Would route {} tasks: {}".format(
            routed_count, ", ".join(summary_parts)
        ))
        return

    # EDGE-2: skip the file write entirely if nothing changed
    if routed_count == 0:
        print("\nRouted 0 tasks (nothing to update).")
        return

    # REL-2: acquire lock before writing to guard against concurrent processes
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        # Atomic write — prevents file corruption on interrupted write (MINOR-2)
        tmp = TASKS_FILE.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, TASKS_FILE)
    finally:
        _release_lock(lock_path)

    summary_parts = ["{} -> {}".format(n, p) for p, n in counters.items()]
    print("\nRouted {} tasks: {}".format(routed_count, ", ".join(summary_parts)))
    print("[OK] Updated {}".format(TASKS_FILE))


def main():
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv

    # Extract --task-id value if present
    task_id_filter = None
    if "--task-id" in argv:
        idx = argv.index("--task-id")
        if idx + 1 < len(argv):
            task_id_filter = argv[idx + 1]
        else:
            print("[ERROR] --task-id requires a value, e.g. --task-id TASK-001", file=sys.stderr)
            sys.exit(1)

    if dry_run:
        print("[DRY-RUN] Routing preview (no changes written):\n")
    elif task_id_filter:
        print("Routing task '{}' (will write to active_tasks.json):\n".format(task_id_filter))
    else:
        print("Routing tasks (will write to active_tasks.json):\n")

    route_tasks(dry_run=dry_run, task_id_filter=task_id_filter)


if __name__ == "__main__":
    main()
