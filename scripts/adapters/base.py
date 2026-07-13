"""Stable adapter contract shared by every AOA agent CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DispatchRequest:
    engine: str
    prompt: str
    workdir: Path
    timeout: int
    model: str | None = None
    effort: str | None = None
    enable_mcp: bool = False
    executable: str | None = None


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    stdin: str
    display_argv: list[str]
    env: dict[str, str] | None = None


class AdapterProtocol(Protocol):
    engine: str

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation: ...
    def parse(self, stdout: str, stderr: str) -> str: ...
    def probe_prompt(self) -> tuple[str, str]: ...
