# AOa cold quickstart

This walkthrough starts from a new clone and runs real worker and tester agents.
Allow roughly two minutes after prerequisites are installed; vendor response time
can vary.

## 1. Prepare two authenticated lanes

Install Python 3.8+ and at least two of `claude`, `codex`, and `agy`. Authenticate
each CLI with its vendor-supported flow before installing AOa. AOa does not accept,
store, or refresh vendor credentials.

## 2. Clone and install

Windows PowerShell:

```powershell
git clone https://github.com/InonB2/multi-agent-orchestration.git
Set-Location multi-agent-orchestration
.\install.ps1
```

macOS or Linux:

```bash
git clone https://github.com/InonB2/multi-agent-orchestration.git
cd multi-agent-orchestration
./install.sh
```

Press Enter at the adapter prompt to select every discovered lane, or type at
least two comma-separated names. Installation creates gitignored local config and
runtime state, installs the editable `aoa` command, verifies selected sessions
sequentially, and starts a loopback dashboard.

If installation reports a missing adapter, install/authenticate that CLI or rerun
and select two available adapters. For automation, the platform launchers accept
non-interactive options:

```powershell
.\install.ps1 -NonInteractive -Engines claude,codex -NoOpen
```

```bash
./install.sh --non-interactive --engines claude,codex --no-open
```

## 3. Run the worker-not-tester demo

```bash
aoa demo
```

If `aoa` is not on `PATH`, use the fallback printed by the installer:

```bash
python -m aoa_cli demo
```

Success means the worker's visible test passes, AOa blocks the premature `done`,
and a different tester catches the planted Unicode defect. The command exits 0,
but the intentionally defective task remains `in_progress`; that is the correct
quality-gate result.

Open <http://127.0.0.1:7780/> to inspect the catch. Runtime files are under `.aoa/`.

## 4. Verify the result

Look for all four lines:

```text
visible worker tests: PASS
lifecycle gate: BLOCKED false done (tester sign-off missing)
independent tester: CAUGHT planted Unicode casefold defect
task state: in_progress (never advanced to tested/done)
```

If a dispatch fails, confirm both selected CLIs run from the same terminal and are
authenticated, then rerun the installer. If the dashboard was skipped, start it:

```bash
python scripts/dashboard_server.py --directory .aoa/dashboard
```

Next: read [`../OPERATORS.md`](../OPERATORS.md) for routing, manual lifecycle,
reports, and checkpoint recovery.
