"""Reference adapter used by the Add Your Own Agent cold-path validation."""

from __future__ import annotations

from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "example_echo"

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation:
        code = "import sys; print('EXAMPLE_ECHO:' + sys.stdin.read())"
        argv = [executable, "-c", code]
        display_argv = [executable, "-c", "<adapter-code>"]
        return Invocation(argv, request.prompt, display_argv)

    def parse(self, stdout: str, stderr: str) -> str:
        return stdout.strip()

    def probe_prompt(self) -> tuple[str, str]:
        return ("AOA_EXAMPLE_ECHO_OK", "AOA_EXAMPLE_ECHO_OK")
