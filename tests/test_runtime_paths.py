from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import runtime_paths  # noqa: E402
import dispatch_worker  # noqa: E402


def test_runtime_root_defaults_to_repository_root():
    assert runtime_paths.runtime_root({}) == runtime_paths.REPO_ROOT


def test_runtime_root_accepts_deployment_override(tmp_path):
    root = runtime_paths.runtime_root({"AOA_RUNTIME_DIR": str(tmp_path)})
    assert root == tmp_path.resolve()


def test_dispatch_worker_outputs_use_runtime_path_contract():
    assert dispatch_worker.PTME_LOG_FILE == runtime_paths.runtime_path(
        "logs", "ptme_decisions.jsonl"
    )
    assert dispatch_worker.USAGE_LOG_FILE == runtime_paths.runtime_path("logs", "usage.jsonl")
    assert dispatch_worker.LIVE_TASKS_FILE == runtime_paths.runtime_path(
        "dashboard", "live_tasks.json"
    )


def test_runtime_path_uses_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AOA_RUNTIME_DIR", str(tmp_path))
    assert runtime_paths.runtime_path("logs", "events.jsonl") == (
        tmp_path / "logs" / "events.jsonl"
    ).resolve()


@pytest.mark.parametrize("part", [Path("/absolute"), Path("..") / "outside"])
def test_runtime_path_rejects_escape(monkeypatch, tmp_path, part):
    monkeypatch.setenv("AOA_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        runtime_paths.runtime_path(part)
