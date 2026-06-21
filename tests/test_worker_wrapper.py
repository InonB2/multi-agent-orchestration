"""
tests/test_worker_wrapper.py — deterministic result writeback (ToT task 1.4).

Acceptance: workers write deliverables exclusively to owner_inbox/TASK-<ID>_result.md,
atomically, and a path-traversal task ID is refused.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import worker_wrapper as ww  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    inbox = tmp_path / "owner_inbox"
    monkeypatch.setattr(ww, "OWNER_INBOX", inbox)
    return inbox


def test_result_path_is_deterministic(sandbox):
    p = ww.result_path("TASK-101")
    assert p.name == "TASK-101_result.md"
    assert p.parent == sandbox.resolve()


def test_write_result_creates_file_atomically(sandbox):
    path = ww.write_result("CO-7", "# done\nresult body")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# done\nresult body"
    assert path.name == "TASK-CO-7_result.md"
    # No leftover temp file.
    assert list(sandbox.glob("*.tmp")) == []


def test_write_result_overwrites(sandbox):
    ww.write_result("CO-8", "first")
    path = ww.write_result("CO-8", "second")
    assert path.read_text(encoding="utf-8") == "second"


def test_path_traversal_rejected(sandbox):
    with pytest.raises(ValueError):
        ww.result_path("../escape")
    with pytest.raises(ValueError):
        ww.write_result("../../etc/passwd", "x")


def test_two_tasks_distinct_files(sandbox):
    a = ww.write_result("A1", "a")
    b = ww.write_result("B2", "b")
    assert a != b
    assert a.read_text() == "a"
    assert b.read_text() == "b"
