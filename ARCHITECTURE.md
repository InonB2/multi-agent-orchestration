# AOa architecture

## The orchestration layer is the product

AOa treats agents as interchangeable commodities. Claude Code, Codex, and AGY are
core v1 lanes that can work as one heterogeneous team; identity-specific invocation
details stop at adapters. The durable value is the common dispatch contract, task
state, routing policy, independent QA gate, recovery data, and operational telemetry.

```text
task/spec/config
      |
      v
router/coordinator -----> lifecycle state + checkpoints
      |
      v
common dispatcher -----> prompt-free telemetry -----> dashboard
      |
      +---- Claude adapter ----> Claude Code CLI
      +---- Codex adapter -----> Codex CLI
      +---- AGY adapter -------> PTY wrapper ----> AGY CLI
```

## Adapter and configuration boundary

`scripts/adapters/base.py` defines the shared values:

- `DispatchRequest`: engine, prompt, work directory, timeout, optional model/effort,
  MCP preference, and executable override.
- `Invocation`: argument vector, stdin, safe display vector, and optional environment.
- `AdapterProtocol`: `engine`, `build`, `parse`, and `probe_prompt`.

The adapter builds the vendor command, parses its final report, and owns a minimal
health-probe contract. `scripts/dispatch.py` owns executable resolution, stdin/workdir,
timeouts, process-tree cleanup, result status, and telemetry. It loads adapter modules
from `dispatch.adapters` in `aoa.config.json`; there is no engine switch in the core
dispatcher. Therefore another CLI requires one adapter module plus one config entry,
provided it can satisfy this contract, with no dispatcher change.

`aoa.config.json` is the portable product default. The installer writes discovered
absolute CLI paths and enabled lanes to gitignored `aoa.config.local.json`. Environment
overrides and alternate `AOA_CONFIG` files are resolved by the configuration layer.
Role TOML files describe role/provider policy; they do not replace product config.
Secrets remain outside AOa in vendor CLI auth stores or external secret managers.

## Execution and data flow

1. A task enters the filesystem queue with an ID, title, complexity, and state.
2. Static routing suggests a provider from title keywords and complexity policy.
3. M/L/XL supervised work is blocked until its pre-task spec validates.
4. The coordinator atomically claims the task and writes `in_progress`.
5. Dispatch loads the configured adapter, probes the CLI, invokes it in the selected
   work directory, captures its report, and writes lifecycle telemetry.
6. A different tester inspects the artifact and acceptance criteria, runs checks, and
   produces independent evidence.
7. The coordinator accepts `tested`, with tester identity and report path, then permits
   the final `done` transition.

Checkpoints store completed work, remaining work, exact next step, interruption reason,
and any derived acceptance context. The supervisor can append this context to a later
worker prompt. Filesystem state makes resume inspectable and provider-neutral.

## Why `in_progress -> tested -> done`

Worker output is evidence of execution, not proof of correctness. A worker can share
the same assumptions as its implementation and tests. AOa therefore makes `tested` a
real state, requires a non-empty tester identity, and rejects a tester matching the
worker/assignee. The coordinator blocks direct `in_progress -> done` transitions.
`--force` exists for recovery but deliberately bypasses these guarantees.

The demo illustrates the distinction: a worker satisfies visible ASCII tests with
`lower()`, AOa blocks premature completion, and a different tester's Unicode oracle
shows `casefold()` was required. The demo exits successfully because orchestration
caught the defect, while task state correctly remains `in_progress`.

## Telemetry hygiene

Dispatch JSONL events contain only lifecycle metadata: schema, timestamp, event,
engine/adapter, task ID, role, elapsed seconds, and exit code. Prompts and raw process
output are not written to that stream. Dry-run replaces the command executable,
prompt, and workdir with safe placeholders. Dashboard activity is best-effort and must
not change dispatch success semantics.

Metadata is not anonymous: task IDs, roles, timing, and engine choices may reveal
operational information. Operators should use non-sensitive identifiers, protect
runtime files, and avoid committing `.aoa/` or local config. The shipped dashboard
feeds are empty; the installer copies them into the gitignored runtime directory.

## Cooperation model

AOa does not hard-code Claude as planner, Codex as worker, or AGY as writer. Routing
and operator choice assign roles per task. A team may use AGY to draft, Codex to
implement, and Claude to verify—or any other cross-engine arrangement—so long as the
tester differs from the worker. Engine limits bound local concurrency; they are
capacity settings, not quality rankings.

## Explicit limitations

- V1 routing is static keyword/complexity policy, not measured-outcome optimization.
  Telemetry can support later policy work but does not make current routing adaptive.
- Claude Code, Codex, and AGY are shipped lanes. Gemini CLI is not claimed as a
  supported substitute; future adapters must be implemented and verified explicitly.
- AOa invokes local subprocess CLIs and inherits their authentication, flags, output
  formats, quotas, and failure modes. The shipped wrappers define current behavior.
- PTY handling improves AGY portability but cannot guarantee compatibility with every
  terminal or future CLI release.
- Watchdogs, unattended loops, cron/systemd, and Windows scheduling are deployment
  choices, not a portable automatic-restart guarantee. Checkpoint/manual resume is
  the cross-platform recovery mechanism.
- Local JSON files and locks are appropriate for a single workspace, not a distributed
  transactional control plane. The dashboard has no multi-user authorization layer.
