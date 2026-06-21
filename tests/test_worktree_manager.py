"""
tests/test_worktree_manager.py — ToT worktree lifecycle (plan task 1.2).

Acceptance: create and destroy git worktrees/branches without index corruption.
Runs against a real throwaway git repo in tmp_path.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import worktree_manager as wt  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "README.md").write_text("hello\n", encoding="utf-8")
    _git(r, "add", "README.md")
    _git(r, "commit", "-m", "init")

    monkeypatch.setattr(wt, "ROOT", r)
    # worktrees live OUTSIDE the repo so git status stays clean
    monkeypatch.setattr(wt, "WORKTREES_DIR", tmp_path / "mmoi-worktrees")
    return r


def _is_clean(repo):
    res = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                         capture_output=True, text=True)
    return res.stdout.strip() == ""


def test_create_worktree(repo):
    path = wt.create_worktree("WT-1")
    assert path.exists()
    assert (path / "README.md").exists()  # base ref content present
    # main repo index is not corrupted by the worktree add
    assert _is_clean(repo)
    # branch was created
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", "worker/WT-1"],
                              capture_output=True, text=True).stdout
    assert "worker/WT-1" in branches


def test_create_is_idempotent(repo):
    p1 = wt.create_worktree("WT-2")
    p2 = wt.create_worktree("WT-2")  # must not raise
    assert p1 == p2


def test_list_includes_new_worktree(repo):
    wt.create_worktree("WT-3")
    paths = [w.get("path", "") for w in wt.list_worktrees()]
    assert any("WT-3" in p for p in paths)


def test_destroy_worktree(repo):
    path = wt.create_worktree("WT-4")
    assert path.exists()

    assert wt.destroy_worktree("WT-4") is True
    assert not path.exists()
    # branch removed
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", "worker/WT-4"],
                              capture_output=True, text=True).stdout
    assert "worker/WT-4" not in branches
    # repo still clean
    assert _is_clean(repo)


def test_destroy_missing_is_safe(repo):
    # Destroying something never created must not raise.
    assert wt.destroy_worktree("NEVER-EXISTED") is True


def test_invalid_task_id_rejected(repo):
    with pytest.raises(ValueError):
        wt.create_worktree("../escape")
    with pytest.raises(ValueError):
        wt.worktree_path("bad name")
