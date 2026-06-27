Applied all QA-required fixes on `feat/tot-ptme-upgrade`.

- `INFRA-1`: `scripts/preflight_auth.py` now resolves and spawns the real provider CLI probe directly, using minimal non-interactive commands instead of `llm_provider.py --dry-run`. Added `tests/test_preflight_auth.py`.
- `INFRA-2`: `scripts/model_supervisor.py` now saves a coordinator checkpoint for rate-limited tasks before worktree teardown and before the pool cooldown. Added a regression test covering checkpoint-before-destroy-before-cooldown ordering.
- `INFRA-3`: `scripts/worktree_manager.py` now reuses an existing directory only if `git worktree list --porcelain` confirms it is the registered worktree for the expected branch; otherwise it fails explicitly. Added a regression test for stale/manual directories.
- `DESIGN-1`: `scripts/llm_provider.py` now appends `--print` to `agy` PTME invocations, and the argv assertion in `tests/test_llm_provider.py` was updated.
- `DESIGN-2`: `scripts/worker_wrapper.py` now strips a leading `TASK-` from `task_id` before formatting `TASK-<id>_result.md`, and `tests/test_worker_wrapper.py` was updated.
- `Orchestrator contract`: `scripts/model_supervisor.py` now states explicitly in its module/docs that the supervisor is a pure delegating orchestrator and does not perform worker specialist work itself.

Final test result: `python -m pytest -q` -> `135 passed`
