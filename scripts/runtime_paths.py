#!/usr/bin/env python3
"""Resolve mutable runtime state without coupling code to a deployment layout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT_ENV = "AOA_RUNTIME_DIR"


def runtime_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the configured runtime root, or the repository root by default."""
    env = os.environ if environ is None else environ
    value = (env.get(RUNTIME_ROOT_ENV) or "").strip()
    if not value:
        return REPO_ROOT
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def runtime_path(*parts: str | Path) -> Path:
    """Resolve a relative path below the runtime root."""
    root = runtime_root().resolve()
    path = root
    for part in parts:
        candidate = Path(part)
        if candidate.is_absolute():
            raise ValueError("runtime path parts must be relative")
        path = path / candidate
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("runtime path cannot escape the runtime root") from exc
    return resolved
