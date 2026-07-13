# Add your own agent

AOA can dispatch any non-interactive agent CLI through one Python adapter file
and one `aoa.config.json` edit. The core dispatcher, telemetry, timeout handling,
process cleanup, preflight flow, parallel coordinator, and report capture do not
need engine-specific changes.

The repository includes `scripts/adapters/example_echo.py`, a deliberately small
working adapter created by following this guide from the AOA-3 contract. It uses
Python as a fake agent executable so the complete path is testable without a
vendor account.

## 1. Discover the real CLI contract

Before writing code, record the installed CLI version and its official help or
documentation. Confirm, on every target OS:

- the non-interactive command and whether it accepts the prompt on stdin;
- how to select a model and reasoning/effort level;
- whether MCP/tools can be disabled;
- authentication behavior and a cheap, non-mutating health prompt;
- stdout/stderr format, success and failure exit codes, and cancellation;
- rate limits, safe concurrency, and whether the CLI spawns child processes.

Do not infer flags from another CLI. If behavior is unknown, keep the adapter
experimental and make discovery/fixtures part of the estimate.

## 2. Create one adapter file

Create `scripts/adapters/<engine>.py`. Its module must export `Adapter` with the
four members below. This is the complete AOA-3 interface:

```python
from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "example_echo"

    def build(self, request: DispatchRequest, executable: str,
              scripts_dir: Path) -> Invocation:
        code = "import sys; print('EXAMPLE_ECHO:' + sys.stdin.read())"
        argv = [executable, "-c", code]
        display_argv = [executable, "-c", "<adapter-code>"]
        return Invocation(argv, request.prompt, display_argv)

    def parse(self, stdout: str, stderr: str) -> str:
        return stdout.strip()

    def probe_prompt(self) -> tuple[str, str]:
        return ("AOA_EXAMPLE_ECHO_OK", "AOA_EXAMPLE_ECHO_OK")
```

`DispatchRequest` supplies `engine`, `prompt`, `workdir`, `timeout`, optional
`model` and `effort`, `enable_mcp`, and an optional executable override.
`Invocation` returns the real argument vector, stdin text, a safe diagnostic
argument vector, and optionally a complete environment mapping.

Rules that preserve the common contract:

- Put the prompt in `Invocation.stdin`, never `argv`, `display_argv`, an
  environment variable, or telemetry.
- Use the supplied `executable`; AOA resolves it from config or an override.
- Let the dispatcher set `cwd=request.workdir`. Do not add a CLI cwd flag unless
  the verified CLI requires one.
- Make `display_argv` structurally useful but redact inline code, secrets, user
  content, personal paths, and credentials.
- Build a fresh environment with `os.environ.copy()` only when overrides are
  required. Never log it.
- Translate stdout into the final human-readable report in `parse`. A zero exit
  with an empty parsed report becomes AOA exit 3.
- Return a cheap probe prompt and a stable substring that proves both executable
  access and authentication. The dispatcher bounds it by
  `timeouts.health_probe_seconds`.
- Preserve vendor nonzero exit codes. AOA reserves 2 for missing/preflight
  failure, 3 for an empty report, 124 for timeout, and 130 for cancellation.

The `example_echo` adapter intentionally ignores `model`, `effort`, and MCP. A
real adapter must either map each supported field to verified CLI flags or
document it as unsupported; it must not silently invent a mapping.

## 3. Add one config entry

Register the module and executable key under `dispatch.adapters`:

```json
"example_echo": {
  "module": "adapters.example_echo",
  "cli_key": "python",
  "enabled": false
}
```

`engine` must match the config key exactly. `cli_key` selects an existing value
under `cli`; add a new `cli.<engine>` value in the same config edit for a real
binary. Set `enabled` to `true` only after live preflight and platform tests.

Direct `dispatch.py` needs only that registration. To use the engine in
`parallel_dispatch.py` and PTME routing, the same config edit must also add:

- `engine_limits.<engine>` with a conservative verified concurrency cap;
- one `models.capabilities` entry whose `family` is the engine; and
- a complete S/M/L/XL `models.ladders.<engine>` mapping.

The reference registration uses `example_echo: 2` and the synthetic
`example-echo` model. These are test fixture values, not vendor claims.

## 4. Run the cold path

From the repository root:

```bash
python scripts/dispatch.py --list-adapters
printf '%s' 'cold path prompt' | python scripts/dispatch.py \
  --engine example_echo --workdir . --task-id COLD-1 --role worker
python scripts/dispatch.py --engine example_echo \
  --prompt 'must stay private' --dry-run
python -m pytest tests/test_add_your_own_agent.py tests/test_dispatch.py -q
```

On PowerShell, replace the piped command with:

```powershell
'cold path prompt' | python scripts/dispatch.py --engine example_echo `
  --workdir . --task-id COLD-1 --role worker
```

The live command automatically runs the adapter's preflight before the task;
AOA-3's separate `preflight_auth.py` command is agent-profile based and is not
required for this direct registration path. Expected report output is
`EXAMPLE_ECHO:cold path prompt` (dashboard telemetry notices may surround it).
The dry run must show
`<redacted>` instead of the prompt. Telemetry at
`logs/dispatch_telemetry.jsonl` contains schema, timestamp, lifecycle event,
engine/adapter, task ID, role, elapsed time, and exit code; it excludes prompt,
stdout, stderr, workdir, command, environment, and credentials.

## 5. Validate a real engine

Replace the synthetic CLI with captured, sanitized fixtures from the exact
supported version. Test success, auth failure, missing executable, malformed and
empty output, vendor nonzero exit, timeout plus child-process cleanup, Ctrl+C,
Unicode, a workdir containing spaces, model/effort mappings, MCP-off behavior,
telemetry redaction, and the configured parallel cap. Run live smoke tests on
Windows, macOS, and Linux; CI fixtures alone do not establish live CLI support.

Fallback is explicit: if discovery, preflight, or parsing fails, disable the
adapter and route to another configured engine. Never report a failed or empty
run as success.

## Honest effort estimate for an additional real engine

Assumptions: one engineer familiar with Python subprocesses; vendor CLI already
exists; test credentials are supplied without auth automation; one current OS
environment of each supported family is available; review time is included;
vendor installation/account approval and core AOA redesign are excluded.

| Work item | Low | Expected | High |
|---|---:|---:|---:|
| CLI/version discovery and contract evidence | 0.5 | 1.0 | 2.0 |
| Adapter implementation and parsing | 0.5 | 1.0 | 2.0 |
| Auth/preflight and redaction review | 0.5 | 1.0 | 1.5 |
| Fixtures and failure-path tests | 1.0 | 1.5 | 2.5 |
| Windows live validation/debugging | 0.5 | 1.0 | 2.0 |
| macOS live validation/debugging | 0.5 | 1.0 | 2.0 |
| Linux live validation/debugging | 0.5 | 1.0 | 2.0 |
| CI, docs, review, and soak buffer | 1.0 | 1.5 | 3.0 |
| **Total person-days** | **5.0** | **9.0** | **17.0** |

Confidence is medium for a documented, stable CLI and low for preview CLIs,
interactive-only authentication, undocumented output, or platform-specific PTY
behavior. Supporting only one OS reduces expected effort by about 2 person-days,
but must be labeled single-platform rather than cross-platform.
