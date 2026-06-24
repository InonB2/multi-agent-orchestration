# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added
<!-- 2026-06-24 framework upgrades (engine-scoped PTME, named specialists, closed learning loop, semantic router + rate-wall failover, usage telemetry bridge, two-tier QA, per-engine agent profiles) -->
- **Engine-Scoped PTME Selection**: Restricts LLM execution strictly to native engine model families (Anthropic models for Claude, OpenAI models for Codex, and Google models for `agy`), eliminating cross-engine model routing inefficiencies.
- **Named Specialist Agents**: Replaced monolithic agents with specialized role profiles (e.g., `Codebase Researcher`, `Database Debugger`, `Interface Architect`) equipped with tailored system instructions and tool access.
- **Subagent Delegation Hierarchy**: Enabled specialist agents to programmatically spawn subagents for modular task processing, conserving parent context window space.
- **Real Per-Task Usage Capture**: Integrated detailed metadata logging for token usage (input, output, and cache read/write tokens), timestamps (start/stop), and estimated USD costs per task.
- **Closed Learning Loop**: Operationalized an automated rule refinement cycle inspired by Reflexion and OPRO. Incorporates deterministic grading (QA, compilers, linters), failure analysis, A/B rule validation, and promotion to global or project-level instructions (`AGENTS.md`).
- **Semantic Capability Router**: Deployed an embedding-based capability matcher that matches incoming tasks to the most suitable agent profile based on semantic cosine similarity.
- **Rate-Wall Failover & Load Balancing**: Implemented dynamic lane grey-out ("walled") capabilities to auto-reroute tasks to active fallback engines during rate-limit events or API outages.
- **Unified Usage Telemetry Bridge**: Implemented a local OpenTelemetry-based collector and Python parsing reader to aggregate token consumption logs across Claude Code (OTel HTTP), Codex (Session JSONL harvesting), and Google Antigravity (Python SDK streaming client).
- **Agent Dashboard (`dashboard/`):** a single-file, zero-dependency UI for watching the multi-engine agent team — orchestrator header cards, running specialist agents grouped by engine team, an in-flight task ledger, and a per-task model/effort view. Opens directly on `file://` (no server, no build); reads live feeds as `window.*` globals injected by `<script>` tags rather than `fetch()`. Ships with the live-feed producers (`scripts/agent_activity.py`, `dispatch_worker.py`, `orchestrator_stats.py`, `codex_usage.py`, `rate_wall_watchdog.py`, `sub_orchestrator.py`, `ptme.py`, `build_analytics.py`) and seeded idle sample feeds so it renders out-of-the-box. Documents the dispatch → sub-orchestrator → PTME flow and the rate-wall watchdog in `dashboard/README.md`, with an honest **known-limitations / roadmap**: analytics aggregation, PTME model-vs-engine scoping, and the learning loop are a **v0/preview** with data-integrity hardening in progress. Sanitization: seed roster genericized to role-based ids; `build_analytics.py` now emits repo-relative source paths (no machine paths in `analytics_data.js`).
- **Deployment guide (README):** "Local / Self-Host vs. VPS / Always-On" section — CLI-subscription vs metered-API billing model, the headless auth caveat (persistent session vs API mode), the one-shot-router reality (always-on = scheduled invocation, not a daemon), and a Local-vs-VPS comparison table. (#10)

### Fixed
- **Monolithic Inefficiencies**: Fixed performance and latency overhead caused by routing simple text tasks to heavy reasoning models.
- **Prompt Cache Cost Discrepancies**: Resolved cost estimation bugs by capturing and factoring cache read/write tokens into final task expense calculations.
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

### In Progress
- **Dashboard Visualizations**: Frontend rendering of the newly exposed task usage metadata fields is currently in development.
- **Bridge Productionization**: Hardening the local OpenTelemetry exporter daemon and testing programmatic rate-limit estimation metrics for Claude and `agy` in high-throughput environments.

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
- Cyrillic character typo in `sage.toml` task type keyword

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
