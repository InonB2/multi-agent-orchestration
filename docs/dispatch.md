# AOA agent dispatch

AOA v1 treats agent CLIs as interchangeable workers. Claude Code, Codex, and
AGY ship as core lanes behind the same `scripts/dispatch.py` interface. The
dispatcher owns stdin delivery, work directory handling, preflight, timeout and
process-tree cleanup, captured reports, truthful exit codes, and redacted
telemetry. Engine-specific command and response details live only in
`scripts/adapters/`.

```bash
printf '%s' 'Review this repository' | python scripts/dispatch.py \
  --engine codex --workdir . --task-id REVIEW-1 --role qa
python scripts/dispatch.py --engine claude --prompt-file task.md --workdir .
python scripts/dispatch.py --engine agy --prompt 'Reply briefly' --workdir .
python scripts/dispatch.py --engine codex --prompt 'private' --dry-run
```

The dry-run plan never invokes or requires an installed model CLI and replaces
the prompt with `<redacted>`. Live runs require the configured executable and an
already authenticated vendor session; the health probe fails with exit 2 when
either prerequisite is unavailable. Exit 3 means a silent/no-report success,
and exit 124 means the entire worker process tree exceeded its wall timeout.

## Adapter contract

An adapter exports an `Adapter` class with `engine`, `build`, `parse`, and
`probe_prompt`. `build` returns an invocation whose prompt is supplied through
stdin to the dispatch subprocess. Register the module and CLI key under
`dispatch.adapters` in `aoa.config.json`; the dispatcher has no engine switch.
`adapters/stub.py` is a fourth working registration example.
See [Add your own agent](ADD_YOUR_OWN_AGENT.md) for the complete cold path,
reference adapter, validation checklist, and real-engine effort estimate.

AGY's public print-mode CLI currently accepts the final prompt only as a child
process argument. AOA still accepts it through stdin and keeps it out of dry-run
and telemetry, but local process-list visibility remains an AGY limitation.
`agy_pty.py` uses ConPTY on Windows and a standard POSIX PTY on macOS/Linux.

Telemetry writes prompt-free start/success/failure/timeout lifecycle events to
`logs/dispatch_telemetry.jsonl` and bridges live state to the existing dashboard
activity feed. Repository dashboard seeds remain generic; runtime history is not
part of a release commit.

`scripts/parallel_dispatch.py` uses this same in-process contract for every
configured adapter, including Claude and third-party adapters. Its public
diagnostic command shape is prompt-free; concurrency limits come from
`engine_limits` (or an adapter's `max_parallel`, default 1), and each engine gets
an isolated workspace under its configured/default worker root.
