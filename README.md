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

## VPS Self-Hosting (optional)

For always-on unattended orchestration, you can self-host AOa on a Virtual Private Server (VPS), such as Hostinger or any Ubuntu 22.04/24.04 instance.

### Setup flow at a glance

1. **Provision server**: Set up an Ubuntu VPS, configure key-based SSH, and enable `ufw` firewall (SSH only).
2. **One-shot setup**: Clone the repository into `/opt/orchestration` and run `deploy/vps/setup.sh`. This provisions Python 3.11, creates the unprivileged `orchestrator` service user, builds a virtualenv, and configures environment defaults.
3. **Background daemon**: The script installs and starts `orchestration-loop.service` via `systemd`. The loop monitors `tasks/active_tasks.json`, runs non-blocking routing passes, and auto-restarts on failure.
4. **Optional API Gateway**: To run the Andy API Gateway behind TLS alongside the CLI adapters (`claude`, `codex`, `agy`), run `deploy/vps/setup_gateway.sh` to configure Caddy reverse-proxying with automatic HTTPS (ports 80/443).

### Honest scope & boundaries

- **Self-contained scripts, not a managed service**: Self-hosting executes local CLI scripts and task queue loops. It does not provide a multi-tenant cloud control plane.
- **Credential isolation**: Provider API keys and vendor CLI authentication remain local to the VPS (stored in mode `600` `.env` files or user keystores). No credentials or infrastructure are managed by this repo.
- **Vendor CLI compliance**: Interactive CLI sessions (`claude setup-token`, `codex login`, `agy login`) must be authenticated manually per user, adhering to vendor ToS guidelines for automated runs.

For complete step-by-step instructions, systemd unit templates, and automated update commands (`update.sh`), see [`docs/self-hosting-vps.md`](docs/self-hosting-vps.md).

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
