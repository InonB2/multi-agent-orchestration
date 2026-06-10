# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

## [0.2.0] — 2026-06-10

### Added
- `scripts/llm_provider.py` — LLM-agnostic provider abstraction supporting CLI tools and OpenAI-compatible/Anthropic API endpoints
- `config/agents/openai_agent.toml` — example config for any OpenAI-compatible endpoint
- `config/agents/anthropic_api.toml` — example config for direct Anthropic API access
- `[provider]` section in `_defaults.toml` — agents declare `type = "cli"` or `"api"`
- Auto-detection of Anthropic auth format (`x-api-key` + `anthropic-version` header)
- GitHub Actions CI workflow (Python 3.8 / 3.10 / 3.12 matrix)
- 50 pytest tests across all 5 core scripts + new provider tests
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
- `scripts/coordinator.py` — task lifecycle manager (claim/update/complete)
- `scripts/agent_config.py` — TOML-based agent config with deep-merge defaults
- 21 agent TOML configs + `_defaults.toml` base layer
- `docs/model_capability_table.md` — benchmark-backed routing rationale
- `tasks/active_tasks.example.json` — example task structure
- `CLAUDE.md.template` — template for orchestrator instructions
