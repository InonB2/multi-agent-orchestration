"""
tests/test_docs_quickstart.py — Prevention guard for doc/code drift.

Two checks that would have caught the publish blockers (REPO-5, REPO-6):

1. Quickstart end-to-end: copy the shipped `examples/sample_active_tasks.json`
   into a tasks file (exactly what the quickstart's `cp` step does) and run the
   real router against it. A wrong-shaped sample (e.g. a bare JSON array) fails here.

2. Doc-command existence: every `python scripts/<x>.py <subcommand>` referenced in
   README.md and examples/quickstart.md must be a real subcommand of that script.
   A README that documents a non-existent command (e.g. `coordinator.py complete`)
   fails here.

Run with:  pytest tests/
"""

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Check 1 — quickstart sample file routes end-to-end (guards REPO-5)
# ---------------------------------------------------------------------------

def test_quickstart_sample_routes_end_to_end(tmp_path, monkeypatch):
    """Mirror quickstart Step 1 (cp sample) + Step 2 (run router) against the
    shipped sample file. The router must load it and assign providers without
    raising — a bare-array sample would crash with AttributeError."""
    sample = ROOT / "examples" / "sample_active_tasks.json"
    assert sample.exists(), "examples/sample_active_tasks.json is missing"

    data = json.loads(sample.read_text(encoding="utf-8"))
    assert isinstance(data, dict), (
        "sample must be an object with a 'tasks' array, not a bare list — "
        "this is the exact shape the quickstart and every script expect"
    )
    assert isinstance(data.get("tasks"), list) and data["tasks"], "sample must carry a non-empty 'tasks' array"

    # Step 1: cp examples/sample_active_tasks.json tasks/active_tasks.json
    dest = tmp_path / "active_tasks.json"
    dest.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

    import task_router
    importlib.reload(task_router)
    monkeypatch.setattr(task_router, "TASKS_FILE", dest)

    # Step 2 (preview): python scripts/task_router.py --dry-run
    task_router.route_tasks(dry_run=True)

    # Step 2 (apply): python scripts/task_router.py
    task_router.route_tasks(dry_run=False)

    written = json.loads(dest.read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    for task in written["tasks"]:
        assert task.get("preferred_provider"), "every task should be routed to a provider after a real run"


def test_router_rejects_non_dict_root(tmp_path, monkeypatch):
    """A bare-array tasks file must produce a clear error, not an AttributeError."""
    bad = tmp_path / "active_tasks.json"
    bad.write_text("[]", encoding="utf-8")

    import task_router
    importlib.reload(task_router)
    monkeypatch.setattr(task_router, "TASKS_FILE", bad)

    with pytest.raises(SystemExit) as exc:
        task_router.route_tasks(dry_run=True)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Check 2 — documented subcommands exist (guards REPO-6)
# ---------------------------------------------------------------------------

# Scripts that dispatch by positional subcommand. task_router.py is flag-driven
# (no subcommands) and is intentionally excluded from the subcommand check.
_SUBCOMMAND_SCRIPTS = {
    "coordinator.py",
    "checkpoint.py",
    "task_spec.py",
    "agent_config.py",
    "llm_provider.py",
}
_FLAG_ONLY_SCRIPTS = {"task_router.py"}

# Tokens that follow `script.py` but are NOT subcommands.
_NON_SUBCOMMAND = {"--help", "-h"}


def _valid_subcommands(script: str) -> set:
    """Discover a script's real subcommands from its source.

    Handles both dispatch styles used in this repo:
      - `COMMANDS = { "name": fn, ... }` dicts (coordinator, checkpoint)
      - argparse `sub.add_parser("name", ...)` (task_spec, agent_config, llm_provider)
    """
    src = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    cmds = set()

    # argparse subparsers
    cmds.update(re.findall(r'add_parser\(\s*["\']([\w\-]+)["\']', src))

    # COMMANDS = { "name": ... } literal
    m = re.search(r'COMMANDS\s*=\s*\{(.*?)\}', src, re.DOTALL)
    if m:
        cmds.update(re.findall(r'["\']([\w\-]+)["\']\s*:', m.group(1)))

    return cmds


def _doc_command_refs(doc: Path):
    """Yield (script, token) for every `python scripts/<x>.py <token>` inside
    fenced ```bash``` / ```sh``` / ``` code blocks of *doc*."""
    text = doc.read_text(encoding="utf-8")
    for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.DOTALL):
        # join backslash-continued lines so multi-line invocations read as one
        block = block.replace("\\\n", " ")
        for script, token in re.findall(r'scripts/([\w]+\.py)\s+([^\s\\|]+)', block):
            yield script, token


_DOCS = [ROOT / "README.md", ROOT / "examples" / "quickstart.md"]


def test_documented_subcommands_exist():
    valid_cache = {}
    failures = []
    for doc in _DOCS:
        if not doc.exists():
            continue
        for script, token in _doc_command_refs(doc):
            if script in _FLAG_ONLY_SCRIPTS:
                continue
            if token in _NON_SUBCOMMAND or token.startswith("-"):
                continue
            if script not in _SUBCOMMAND_SCRIPTS:
                continue
            valid = valid_cache.setdefault(script, _valid_subcommands(script))
            if token not in valid:
                failures.append(
                    "{}: '{} {}' — not a valid subcommand (valid: {})".format(
                        doc.name, script, token, ", ".join(sorted(valid))
                    )
                )
    assert not failures, "Documented commands that do not exist:\n  " + "\n  ".join(failures)
