# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
- **v2 runtime enforcement & team-of-teams (PTME):** `model_supervisor.py run` blocks `M`/`L`/`XL` tasks without a valid spec, reloads queued checkpoint context into the next worker prompt, and writes an audited PTME decision trail to `logs/ptme_decisions.jsonl`. Adds `model_supervisor.orchestrate_worker_plan(...)` as a tested local-orchestrator interface for multi-specialized workers (interface-only today; default CLI still dispatches one worker per task). See Wiki: V2-Runtime-Enforcement.

### Security
- **worker!=tester self-approval bypass fixed (fail-closed):** `coordinator.py mark-tested` now requires `--tested-by` and rejects self-testing; `mark-done` refuses a task with no recorded tester unless `--force`. Regression tests added (omitted-tester path must fail).

### Added
- **Deployment guide (README):** "Local / Self-Host vs. VPS / Always-On" section — CLI-subscription vs metered-API billing model, the headless auth caveat (persistent session vs API mode), the one-shot-router reality (always-on = scheduled invocation, not a daemon), and a Local-vs-VPS comparison table. (#10)

### Fixed
- **Codex CLI hung in automation (non-interactive execution).** The CLI provider ran `<cli> "<prompt>"` with stdin inherited; for the OpenAI Codex CLI this launched the interactive TUI (never `codex exec`) and blocked forever waiting on stdin EOF. Added `provider.cli_exec_args` (inserted between command and prompt; `codex.toml` now sets `["exec"]`) and made the runner always pass `stdin=subprocess.DEVNULL`. Documented in `_defaults.toml`, `codex.toml`, and the README, with a Windows sandbox note. Added regression tests (argv shape + stdin closed). (#11)
- **Publish-readiness pass:** corrected docs/examples that did not match the code.
  - `examples/sample_active_tasks.json` now uses the canonical `{"tasks": [...]}` envelope (was a bare array that crashed the quickstart).
  - `task_router.py` now emits a clear error if `active_tasks.json` is not an object with a `tasks` array.
  - README Quick Start uses real commands (`coordinator.py mark-tested` / `mark-done`); removed the non-existent `complete` command.
  - Reframed routing docs (README + capability table) to describe the actual keyword-heuristic router across three providers (was "8 capability-based rules").
  - Unified agent-config schema on `[task_types]` (the two API example configs used `[task_routing]`); documented that these fields are descriptive metadata, not read by the router.
  - Reconciled Opus version label (4.8) and relabelled benchmark figures as illustrative estimates.
  - Unified task-status taxonomy in the README schema (added `backlog`, `tested`).
  - **Fixed red CI:** added a single-sourced `.flake8` config (the CI lint step was failing on main because `--ignore` overrode flake8's defaults, surfacing the codebase's intentional column-alignment style). CI and CONTRIBUTING now both run `flake8 scripts/`.
  - Added `config/agents/claude-code.toml` so the default provider's enriched label resolves.
  - Pinned GitHub Actions to commit SHAs.
  - `task_router.py` now treats a `null` `preferred_provider` as unrouted (matching the documented task schema), so the quickstart sample routes instead of being skipped.
  - Added `tasks/active_tasks.json` to `.gitignore` (runtime state, created by the quickstart).

### Added
- Doc/quickstart guard test (`tests/test_docs_quickstart.py`): runs the documented quickstart end-to-end against the shipped sample file and asserts every `scripts/*.py <subcommand>` referenced in README/quickstart exists.

## [0.2.0] — 2026-06-10

### Added
- `scripts/llm_provider.py` — LLM-agnostic provider abstraction supporting CLI tools and OpenAI-compatible/Anthropic API endpoints
- `config/agents/openai_agent.toml` — example config for any OpenAI-compatible endpoint
- `config/agents/anthropic_api.toml` — example config for direct Anthropic API access
- `[provider]` section in `_defaults.toml` — agents declare `type = "cli"` or `"api"`
- Auto-detection of Anthropic auth format (`x-api-key` + `anthropic-version` header)
- GitHub Actions CI workflow (Python 3.8 / 3.10 / 3.12 matrix)
- 64 pytest tests across all 5 core scripts + new provider tests
- `examples/` directory with `sample_active_tasks.json` and `quickstart.md`

### Fixed
- Atomic file writes in `task_router.py` and `task_spec.py` (previously used `write_text()` directly)
- Generalized internal-specific TOML references in 6 agent config files
- Context file comments in `antigravity.toml` and `codex.toml` now use generic adopter instructions
- Cyrillic character typo in `brand.toml` task type keyword

## [0.1.0] — 2026-06-09

### Added
- Initial public release
- `scripts/task_router.py` — keyword-heuristic task routing to AI agents
- `scripts/task_spec.py` — pre-task specification enforcer for M/L/XL tasks
- `scripts/checkpoint.py` — mid-task state saving with resume queue
- `scripts/coordinator.py` — task lifecycle manager (claim/update/checkpoint/mark-tested/mark-done)
- `scripts/agent_config.py` — TOML-based agent config with deep-merge defaults
- 21 agent TOML configs + `_defaults.toml` base layer
- `docs/model_capability_table.md` — benchmark-backed routing rationale
- `tasks/active_tasks.example.json` — example task structure
- `CLAUDE.md.template` — template for orchestrator instructions
