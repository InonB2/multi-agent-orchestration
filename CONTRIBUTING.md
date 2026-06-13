# Contributing

Thank you for your interest in contributing to the multi-agent orchestration framework.

## Principles

- **Zero external dependencies** — all scripts must run with Python 3.8+ stdlib only. Do not add `requirements.txt` imports to the core scripts.
- **Atomic file writes** — all file writes must use the `tmp + os.replace()` pattern. See `scripts/checkpoint.py` for the reference implementation.
- **Path traversal protection** — any script that accepts a task_id or agent_name from user input must validate with `^[A-Za-z0-9_\-]+$` regex before constructing file paths.
- **Tests required** — all new scripts must include corresponding tests in `tests/`. Run `pytest tests/ -v` before submitting.

## Adding a new agent config

1. Copy `config/agents/_defaults.toml` to `config/agents/your_agent.toml`
2. Fill in `[agent]` section (name, description, model)
3. Choose `provider.type = "cli"` or `"api"` and fill in the relevant fields
4. Describe your agent's specialisation in the `[task_types]` section (`accepts` / `rejects`)

   > Note: `[task_types]` is **descriptive metadata** — the router (`scripts/task_router.py`)
   > routes by its own keyword lists and does not read these fields. Editing them documents
   > intent but does not change routing behaviour.

## Submitting changes

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Run tests: `pytest tests/ -v`
4. Run linter (same command CI runs; config lives in `.flake8`): `flake8 scripts/`
5. Submit a pull request to `main`

## Reporting issues

Open a GitHub Issue with:
- Python version
- OS
- The command you ran
- The error output
