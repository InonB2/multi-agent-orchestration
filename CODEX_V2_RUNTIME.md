# CODEX V2 Runtime Enforcement

## Changes

1. Worker≠tester at runtime
- Runtime: `scripts/coordinator.py:428`, `scripts/coordinator.py:456`, `scripts/coordinator.py:471`
- Added `--tested-by` handling, persisted `tested_by`, and rejected `mark-tested` when the tester identity matches the task's `assigned_to` or `preferred_provider` unless `--force` is used.
- Tests: `tests/test_v2_runtime_enforcement.py:69`

2. Spec-gate in the run path
- Runtime: `scripts/task_spec.py:105`, `scripts/task_spec.py:110`, `scripts/task_spec.py:206`
- Runtime: `scripts/model_supervisor.py:94`, `scripts/model_supervisor.py:358`, `scripts/model_supervisor.py:374`
- Added pure spec-validation helpers and blocked supervisor dispatch for `M`/`L`/`XL` tasks without a valid spec. `S` remains exempt.
- Tests: `tests/test_v2_runtime_enforcement.py:110`

3. Auto-resume checkpoints
- Runtime: `scripts/checkpoint.py:144`, `scripts/checkpoint.py:155`, `scripts/checkpoint.py:163`, `scripts/checkpoint.py:177`
- Runtime: `scripts/model_supervisor.py:109`, `scripts/model_supervisor.py:134`, `scripts/model_supervisor.py:383`
- Added checkpoint/read queue helpers, reloaded queued checkpoint state into the worker prompt before rerun, and removed the resume-queue entry during that resume path.
- Tests: `tests/test_v2_runtime_enforcement.py:176`

4. Audited decision log in the runtime
- Runtime: `scripts/llm_provider.py:39`, `scripts/llm_provider.py:194`, `scripts/llm_provider.py:203`, `scripts/llm_provider.py:414`
- PTME resolution now writes one JSONL record per runtime decision to `logs/ptme_decisions.jsonl` with task ID, complexity, recommendation, final decision, decider, reason, and timestamp.
- Tests: `tests/test_v2_runtime_enforcement.py:225`

5. Team-of-teams orchestrator semantics
- Runtime: `scripts/model_supervisor.py:297`
- Added `orchestrate_worker_plan(...)` as the local-orchestrator dispatch interface: it resolves PTME model+effort per worker spec, records each decision, and dispatches worker specs in parallel through injected workers.
- The default shipped CLI supervisor path still runs one worker per claimed task; the multi-specialized path is interface-level, not a new CLI entrypoint.
- Tests: `tests/test_v2_runtime_enforcement.py:257`

6. Docs
- README updates: `README.md:77`, `README.md:96`, `README.md:105`, `README.md:193`, `README.md:199`, `README.md:327`, `README.md:359`, `README.md:459`
- ToT/PTME guide updates: `docs/tot-ptme.md:26`, `docs/tot-ptme.md:112`, `docs/tot-ptme.md:114`, `docs/tot-ptme.md:129`, `docs/tot-ptme.md:156`, `docs/tot-ptme.md:162`, `docs/tot-ptme.md:167`, `docs/tot-ptme.md:200`
- Removed overclaiming and marked the local-orchestrator multi-subagent path as interface-only where appropriate.

## Test Results

- Command: `python -m pytest -q`
- Result: `142 passed in 6.13s`

## Findings

### Infrastructure
- `pytest` was not on PATH in this workspace. Fix: used `python -m pytest -q` for verification. Prevention: keep Windows-facing verification commands interpreter-qualified.

### Design
- A real multi-specialized CLI supervisor flow would be a broader change than the current runtime seam. Fix: implemented and tested the decision+dispatch interface in `model_supervisor.orchestrate_worker_plan(...)` instead of overclaiming a new end-to-end CLI mode. Prevention: docs now explicitly call out what is runtime-real versus interface-only.

## SELF-CHECK (not a sign-off)

Verified:
- The self-test guard rejects `mark-tested` when `tested_by` matches the worker identity.
- Independent tester sign-off still marks the task tested and persists `tested_by`.
- `M` tasks without a valid spec are blocked by the supervisor before claim/dispatch.
- Valid-spec `M` tasks and spec-exempt `S` tasks still run.
- Resume queue entries are reloaded into worker context and removed during the resume path.
- PTME runtime decisions write a structured JSONL audit record.
- The local-orchestrator interface resolves PTME decisions per worker spec and dispatches parallel stub workers.
- Full suite: `python -m pytest -q` returned `142 passed in 6.13s`.

Could not verify:
- A real higher-level caller driving `model_supervisor.orchestrate_worker_plan(...)` against live CLIs and multiple live worktrees.
- Live provider-specific behavior outside tests, including real `agy` model slug correctness and live external CLI auth state.
- Independent QA review of the runtime changes; this file is not a sign-off.

## Interface-only for agy to Verify

- `model_supervisor.orchestrate_worker_plan(...)` is the shipped local-orchestrator interface for multiple specialized sub-agents, but the default CLI supervisor path still dispatches one worker per claimed task.
- End-to-end specialized sub-agent execution across multiple real worktrees depends on a higher-level caller providing an explicit worker plan to that interface.

STREAM-A DONE
