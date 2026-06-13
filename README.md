# AOa — agent-agnostic orchestration

> Agents are interchangeable commodities. The orchestration layer is the product.

AOa coordinates heterogeneous command-line agents through one dispatch contract,
portable configuration, shared task state, telemetry, checkpoints, and an enforced
worker-not-tester quality gate. Claude Code, Codex, and AGY are core v1 lanes and
can cooperate in one team. A new agent CLI needs one adapter module and one config
entry; the dispatcher needs no engine-specific change.

AOa is an early local-first framework, not a hosted service. It invokes the CLI
wrappers shipped in this repository and relies on their installed, authenticated
sessions. It does not manage vendor credentials.

## What ships

- Config-driven dispatch with adapters for Claude Code, Codex, and AGY.
- Static keyword/complexity routing, task claims, pre-task specs, and checkpoints.
- Lifecycle enforcement: `in_progress -> tested -> done`, with a tester identity
  different from the worker.
- Prompt-free dispatch lifecycle telemetry and a loopback dashboard.
- A live `aoa demo` in which one agent creates a planted defect and a different
  agent catches it before the task can be marked done.

## Requirements and authentication

- Git and Python 3.8 or newer.
- At least two of the supported agent CLIs on `PATH`: `claude`, `codex`, `agy`.
- Those CLIs already authenticated using their vendor-supported login flow.

The installer discovers registered adapters, asks which available engines to
enable, runs their adapter-owned health probes sequentially, and writes resolved
machine paths to gitignored `aoa.config.local.json`. Credentials remain in the
vendor CLI stores. Gemini CLI is not an AGY substitute and is not claimed as a
supported lane.

## Install and run the live demo

Windows PowerShell:

```powershell
git clone https://github.com/InonB2/multi-agent-orchestration.git
Set-Location multi-agent-orchestration
.\install.ps1
aoa demo
```

macOS or Linux:

```bash
git clone https://github.com/InonB2/multi-agent-orchestration.git
cd multi-agent-orchestration
./install.sh
aoa demo
```

At the installer prompt, press Enter to enable all discovered adapters, or enter
a comma-separated subset containing at least two engines. If the Python scripts
directory is not on `PATH`, the installer prints the exact fallback; normally:

```bash
python -m aoa_cli demo
```

The demo makes real CLI calls. It claims `HELLO-ORG-001`, dispatches the configured
worker, runs a visible test, demonstrates that an untested `done` transition is
blocked, then dispatches a different tester. The tester runs an independent Unicode
oracle and catches the planted `lower()` versus `casefold()` defect. A successful
demo exits 0 while deliberately leaving the defective task `in_progress`.

Expected key lines include:

```text
visible worker tests: PASS
lifecycle gate: BLOCKED false done (tester sign-off missing)
independent tester: CAUGHT planted Unicode casefold defect
task state: in_progress (never advanced to tested/done)
```

The installer starts the runtime dashboard at <http://127.0.0.1:7780/>, or prints
the start command when `-NoOpen`/`--no-open` is used. Demo state and telemetry are
written under gitignored `.aoa/`; tracked product files stay clean.

For a complete cold walkthrough and recovery notes, see
[`examples/quickstart.md`](examples/quickstart.md). For day-to-day commands and QA
handoffs, see [`OPERATORS.md`](OPERATORS.md). For design boundaries and rationale,
see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Direct dispatch

All lanes use the same entry point:

```bash
python scripts/dispatch.py --list-adapters
python scripts/dispatch.py --engine codex --prompt "Summarize this repository" --workdir . --task-id DOC-1 --role worker --dry-run
```

Remove `--dry-run` for a live invocation. Live dispatch first runs the adapter's
authentication/health probe, then captures the agent's final report and returns a
truthful exit code. See [`docs/dispatch.md`](docs/dispatch.md) for flags and adapter
details.

## Self-hosting (optional)

Managed hosting (e.g. Railway) is the simplest default for running always-on.
If you'd rather self-host on a VPS you control, there's an optional guide at
[docs/self-hosting-vps.md](docs/self-hosting-vps.md) with a `deploy/vps/` setup
script and systemd unit. No credentials or provisioning are included.

## Honest v1 scope

- Routing uses static title keywords and complexity tiers. Telemetry exists, but
  day-one routing is not based on measured outcomes.
- Auto-restart/watchdog helpers are platform-specific operational examples, not a
  portable always-on guarantee. Checkpoints and manual resume are the portable path.
- AOa depends on local CLI behavior and sessions; vendor CLI changes can require an
  adapter update.
- The dashboard is local operational visibility, not a multi-user control plane.
- AGY is a core supported v1 adapter. Direct Gemini CLI support is not claimed.

## Development

```bash
python -m pip install -e .
python -m pip install pytest
python -m pytest tests -q
```

Python below 3.11 also needs `tomli` for TOML role configuration. License: [MIT](LICENSE).
