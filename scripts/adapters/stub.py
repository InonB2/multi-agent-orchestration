"""Example fourth adapter proving registration requires no core change."""

from __future__ import annotations

import sys
from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "stub"

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation:
        code = "import sys; sys.stdin.read(); print('AOA_STUB_OK')"
        argv = [sys.executable, "-c", code]
        return Invocation(argv, request.prompt, [sys.executable, "-c", "<adapter-code>"], None)

    def parse(self, stdout: str, stderr: str) -> str:
        return stdout.strip()

    def probe_prompt(self) -> tuple[str, str]:
        return ("health", "AOA_STUB_OK")
