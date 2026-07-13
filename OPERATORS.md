# AOa operator guide

AOa coordinates interchangeable agent CLIs; operators own task definitions,
engine selection, independent verification, and recovery decisions.

## Dispatch and routing

List registered lanes and preview an invocation without exposing its prompt:

```bash
python scripts/dispatch.py --list-adapters
python scripts/dispatch.py --engine codex --prompt "Inspect the current task" --workdir . --task-id TASK-101 --role worker --dry-run
```

Remove `--dry-run` to dispatch. AOa passes the prompt through the shipped wrapper,
sets the working directory, performs an adapter-specific health probe, enforces a
wall timeout, captures the report, and records prompt-free lifecycle telemetry.
Exit 2 indicates a missing executable or failed preflight, 3 an empty report, and
124 a timeout.

Queue routing is separate from dispatch:

```bash
python scripts/task_router.py --dry-run
python scripts/task_router.py
```

The v1 router scores task-title keywords and applies complexity rules. It writes a
`preferred_provider`; it does not learn live routing decisions from measured results.
Review the preview before applying it.

## Specify and claim work

Medium, large, and extra-large tasks need a valid spec before supervised execution:

```bash
python scripts/task_spec.py create --task TASK-101 --done "Repository inspected" --remaining "Implementation and tests" --next "Implement the smallest change" --criteria "All targeted tests pass"
python scripts/task_spec.py validate --task TASK-101
python scripts/coordinator.py claim --task TASK-101 --model codex
```

Claims use a cross-process lock and reject tasks already `in_progress`, `tested`, or
`done`. Avoid `--force` in normal operation; it bypasses lifecycle safeguards.

## Worker-not-tester gate

The required lifecycle is:

```text
pending/backlog -> in_progress -> tested -> done
```

The worker implements and reports evidence. A different tester identity reviews the
actual artifact and acceptance criteria, reruns relevant checks, and writes its own
result report. `mark-tested` records only the report path string; it does not create
the report.

```bash
python scripts/coordinator.py mark-tested --task TASK-101 --tested-by claude --result-path reports/TASK-101-qa.md
python scripts/coordinator.py mark-done --task TASK-101
python scripts/coordinator.py status --task TASK-101
```

`mark-tested` rejects a tester matching the worker/assignee. `mark-done` rejects a
task without a non-empty tester identity or without prior `tested` status. A failed
test should leave or return work to `in_progress`; do not certify a known failure.

## Reports, checkpoints, and manual resume

Agent output captured by dispatch is a report, but durable evidence belongs in a
task-specific file chosen by the operator. Record commands, exit codes, relevant
output, platform, and acceptance-criterion disposition. Do not put credentials or
raw secret-bearing prompts in reports or telemetry.

Save interrupted progress:

```bash
python scripts/checkpoint.py save --task TASK-101 --done "Parser implemented" --remaining "Edge-case tests" --next "Add Unicode fixtures" --interrupted-by rate-limit
python scripts/checkpoint.py list-resumable
python scripts/checkpoint.py read --task TASK-101
```

`model_supervisor.py` can load queued checkpoint context into the next worker prompt
and remove the queue entry. For a manual resume, read the checkpoint, dispatch the
recorded next step, then clear it only after work has actually resumed:

```bash
python scripts/checkpoint.py mark-resumed --task TASK-101
```

Checkpoint snapshots and the resume queue are local filesystem state. Back them up
if the workspace itself is ephemeral.

## Telemetry and dashboard

Dispatch lifecycle records contain timestamp, event, engine/adapter, task ID, role,
elapsed time, and exit code. They intentionally omit prompts, stdout, and stderr.
The dashboard activity bridge exposes operational metadata. Task IDs and descriptions
can still be sensitive, so use neutral identifiers and keep runtime logs private.

The installer serves the gitignored runtime dashboard on loopback. Restart it with:

```bash
python scripts/dashboard_server.py --directory .aoa/dashboard
```

## Platform and v1 limits

- Claude Code, Codex, and AGY are the core v1 lanes. Their exact command flags and
  parsing behavior live in shipped adapters; AOa follows those wrappers.
- AGY dispatch uses ConPTY on Windows and a POSIX PTY on macOS/Linux. Vendor CLI or
  terminal changes may require adapter maintenance.
- Watchdog/unattended helpers are platform-specific. Portable recovery is checkpoint
  plus manual/scheduled resume; AOa does not promise automatic restart everywhere.
- Local CLI sessions can expire. AOa detects failed probes but does not repair auth.
- The dashboard is loopback/static operational visibility, not authorization or a
  production multi-user service.

## Troubleshooting

| Symptom | Meaning and action |
|---|---|
| Installer finds fewer than two adapters | Put two supported CLIs on `PATH`, authenticate them, and rerun. |
| Preflight exits 2 | Run the selected vendor CLI directly, restore its authenticated session, then rerun installation/dispatch. |
| Dispatch exits 124 | The process tree exceeded its timeout. Inspect partial work, save a checkpoint, then retry or raise the configured timeout. |
| `mark-done` is rejected | Run independent QA, save its report, call `mark-tested` with a different tester, then retry. |
| AGY appears to hang | Confirm the shipped `scripts/adapters/agy.py` and `scripts/agy_pty.py` path is used and the CLI works interactively. |
| Dashboard is unavailable | Start `scripts/dashboard_server.py` against `.aoa/dashboard` and verify port 7780 is free. |
