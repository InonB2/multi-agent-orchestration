#!/usr/bin/env python3
"""
agy_workspace.py — isolated Antigravity workspaces for true multi-flight dispatch.

Pattern:
    1. Call provision_workspace("worker-id") to create D:/agy-workers/worker-id/
    2. Launch scripts/invoke_agy.ps1 with -WorkspaceDir set to that path
    3. Run many agy workers in parallel because each worker now writes to its
       own workspace instead of the shared D:/Antigravity playground
"""

from __future__ import annotations

import re
from pathlib import Path

AGY_WORKERS_ROOT = Path("D:/agy-workers")
DEFAULT_ROOT = AGY_WORKERS_ROOT
WORKER_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DOT_DASH_ONLY_RE = re.compile(r"^[.\-\s]+$")


def validate_worker_id(worker_id: str) -> str:
    if (
        not worker_id
        or not WORKER_ID_RE.fullmatch(worker_id)
        or DOT_DASH_ONLY_RE.fullmatch(worker_id)
    ):
        raise ValueError(
            "Invalid worker_id '{}': use only letters, digits, dot, underscore, hyphen, and include at least one letter or digit".format(
                worker_id
            )
        )
    return worker_id


def provision_workspace(worker_id: str, root: Path = DEFAULT_ROOT) -> Path:
    safe_worker_id = validate_worker_id(worker_id)
    resolved_root = root.resolve()
    workspace = root / safe_worker_id
    resolved_workspace = workspace.resolve()
    if resolved_workspace.parent != resolved_root:
        raise ValueError(
            "Invalid worker_id '{}': resolved workspace '{}' escapes workers root '{}'".format(
                worker_id, resolved_workspace, resolved_root
            )
        )
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    return resolved_workspace
