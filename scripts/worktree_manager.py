#!/usr/bin/env python3
"""
worktree_manager.py — Git worktree lifecycle manager for the Team-of-Teams (ToT)
worker pool.

Adapted from the Claude Playground worktree_manager.py (INFRA-008). This repo-local
version is trimmed to the ToT need: give each parallel worker an *isolated* working
directory so concurrent agents never corrupt one another's git index or source tree.

Each worker gets a dedicated branch + worktree created from a base ref (default:
current HEAD). On completion the supervisor destroys the worktree and deletes the
temporary branch, keeping the repo clean.

Design notes mirrored from the existing scripts:
  * pure-Python stdlib only (subprocess + pathlib)
  * ROOT resolved from the file location (portable across local + VPS)
  * path-traversal guard on task IDs (same allowlist as coordinator.py)
  * spaces in paths are safe because git is invoked via argv lists (never a shell
    string), so no manual quoting is required.

Commands:
    list                              — list git worktrees (porcelain parse)
    create  --task-id ID [--base-ref REF]   — create branch + worktree for a worker
    destroy --task-id ID [--keep-branch]    — remove the worktree (and its branch)

Importable API (used by model_supervisor.py):
    create_worktree(task_id, base_ref=None) -> Path
    destroy_worktree(task_id, keep_branch=False) -> bool
    worktree_path(task_id) -> Path
    branch_name(task_id) -> str
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
# Worktrees live OUTSIDE the repo working tree (a sibling dir) so creating them
# never pollutes the repo's git status / index. Mirrors the plan's separate
# worktrees root (e.g. D:\...\worktrees on Windows, /opt/orchestration/worktrees
# on the VPS). Override via the MMOI_WORKTREES_DIR env var if you prefer another
# location. Set as a module global so tests can monkeypatch it.
WORKTREES_DIR = Path(
    os.environ.get("MMOI_WORKTREES_DIR", str(ROOT.parent / "mmoi-worktrees"))
)

# Same safe allowlist coordinator.py enforces on task IDs — blocks path traversal
# and shell-meta in branch/worktree names.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_task_id(task_id: str) -> str:
    """Return *task_id* if safe, else raise ValueError (callers map to exit 1)."""
    if not task_id or not _TASK_ID_RE.match(task_id):
        raise ValueError(
            "Invalid task_id '{}': must match ^[A-Za-z0-9_\\-]+$".format(task_id)
        )
    return task_id


def branch_name(task_id: str) -> str:
    """Deterministic temporary branch name for a worker task."""
    return "worker/{}".format(_validate_task_id(task_id))


def worktree_path(task_id: str) -> Path:
    """Deterministic isolated worktree path for a worker task."""
    return WORKTREES_DIR / _validate_task_id(task_id)


def _git(args, cwd=None) -> subprocess.CompletedProcess:
    """Run a git command via argv list (space-safe, no shell)."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
    )


def _resolve_base_ref(base_ref) -> str:
    """Resolve the base ref, defaulting to current HEAD (never hardcode origin/main)."""
    if base_ref:
        return base_ref
    res = _git(["rev-parse", "HEAD"])
    if res.returncode != 0:
        raise RuntimeError("could not resolve HEAD: {}".format(res.stderr.strip()))
    return res.stdout.strip()


def create_worktree(task_id: str, base_ref=None) -> Path:
    """Create an isolated branch + worktree for *task_id*. Returns the worktree path.

    Idempotency: if the worktree path already exists it is reused (returned as-is)
    rather than failing, so a re-run of an interrupted batch does not error.
    """
    _validate_task_id(task_id)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    path = worktree_path(task_id)
    branch = branch_name(task_id)

    if path.exists():
        return path

    ref = _resolve_base_ref(base_ref)
    res = _git(["worktree", "add", "-b", branch, str(path), ref])
    if res.returncode != 0:
        raise RuntimeError(
            "git worktree add failed for '{}': {}".format(task_id, res.stderr.strip())
        )
    return path


def destroy_worktree(task_id: str, keep_branch: bool = False) -> bool:
    """Remove the worktree for *task_id* and (unless kept) delete its branch.

    Returns True if the worktree was removed (or already absent). Never raises on
    a missing worktree — destruction is best-effort cleanup.
    """
    _validate_task_id(task_id)
    path = worktree_path(task_id)
    branch = branch_name(task_id)

    removed = True
    if path.exists():
        res = _git(["worktree", "remove", "--force", str(path)])
        removed = res.returncode == 0
        if not removed:
            print(
                "[WARN] git worktree remove failed for '{}': {}".format(
                    task_id, res.stderr.strip()
                ),
                file=sys.stderr,
            )

    # Prune any dangling administrative refs left behind.
    _git(["worktree", "prune"])

    if not keep_branch:
        # -D (force) because the temp branch's commits live only in the worktree.
        _git(["branch", "-D", branch])

    return removed


def list_worktrees() -> list:
    """Parse `git worktree list --porcelain` into {path, branch, commit} dicts."""
    res = _git(["worktree", "list", "--porcelain"])
    if res.returncode != 0:
        raise RuntimeError("git worktree list failed: {}".format(res.stderr.strip()))

    worktrees = []
    current: dict = {}
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["commit"] = line[len("HEAD "):]
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line == "detached":
            current["branch"] = "(detached HEAD)"
    if current:
        worktrees.append(current)
    return worktrees


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_list(_args) -> None:
    for wt in list_worktrees():
        print("{:<60} {:<30} {}".format(
            wt.get("path", "?"),
            wt.get("branch", "(detached)"),
            wt.get("commit", "")[:12],
        ))


def cmd_create(args) -> None:
    try:
        path = create_worktree(args.task_id, base_ref=args.base_ref)
    except (ValueError, RuntimeError) as exc:
        print("[ERROR] {}".format(exc), file=sys.stderr)
        sys.exit(1)
    print("[OK] worktree ready: {} (branch {})".format(path, branch_name(args.task_id)))


def cmd_destroy(args) -> None:
    try:
        destroy_worktree(args.task_id, keep_branch=args.keep_branch)
    except ValueError as exc:
        print("[ERROR] {}".format(exc), file=sys.stderr)
        sys.exit(1)
    print("[OK] worktree destroyed for {}".format(args.task_id))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Git worktree lifecycle manager for the ToT worker pool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List current git worktrees")

    p_create = sub.add_parser("create", help="Create an isolated branch + worktree for a worker")
    p_create.add_argument("--task-id", required=True, help="Task ID (allowlist: A-Za-z0-9_-)")
    p_create.add_argument("--base-ref", help="Base ref to branch from (default: current HEAD)")

    p_destroy = sub.add_parser("destroy", help="Remove a worker's worktree and branch")
    p_destroy.add_argument("--task-id", required=True, help="Task ID")
    p_destroy.add_argument("--keep-branch", action="store_true", help="Keep the temp branch")

    args = parser.parse_args()
    dispatch = {
        "list": cmd_list,
        "create": cmd_create,
        "destroy": cmd_destroy,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
