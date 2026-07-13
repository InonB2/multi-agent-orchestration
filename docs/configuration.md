# Portable configuration

AOA has one product-level configuration source: repository-root `aoa.config.json`.
Agent role files in `config/agents/*.toml` remain role/provider definitions; they
do not replace or compete with the product configuration.

## Resolution contract

Every setting resolves in this order:

1. Environment variable
2. `aoa.config.json`
3. Safe portable default

Relative paths resolve against the AOA install/repository root, never the current
working directory. `AOA_CONFIG` may point to an alternate JSON file. An explicitly
selected missing file, malformed JSON, unsupported schema, invalid timeout/limit,
or configured executable not found by a caller fails with a clear error. No secret
belongs in this file; store credentials in each CLI's auth store or secret manager.
Bare CLI command names resolve through `PATH`; CLI values containing a relative path
separator resolve against the install root, including paths containing spaces.

Run `python scripts/config_loader.py aoa-show` to inspect resolved settings, or
`python scripts/config_loader.py aoa-get paths.workdir` for one value.

## Environment variables

Paths: `AOA_WORKDIR`, `AOA_AGY_WORKERS_DIR`, `AOA_CODEX_WORKERS_DIR`,
`AOA_AGY_QUEUE_DIR`, `AOA_AGY_RESULTS_DIR`.

Commands: `AOA_PYTHON_CMD`, `AOA_POWERSHELL_CMD`, `AOA_CLAUDE_CMD`,
`AOA_CODEX_CMD`, `AOA_AGY_CMD`.

Timeouts: `AOA_CODEX_TIMEOUT_SECONDS`, `AOA_AGY_TIMEOUT_SECONDS`,
`AOA_AGY_INVOKE_TIMEOUT_SECONDS`, `AOA_AGY_USAGE_TIMEOUT_SECONDS`,
`AOA_HEALTH_PROBE_TIMEOUT_SECONDS`, `AOA_AGY_POLL_SECONDS`,
`AOA_API_TIMEOUT_SECONDS`.

Engine limits: `AOA_CLAUDE_MAX_PARALLEL`, `AOA_CODEX_MAX_PARALLEL`,
`AOA_AGY_MAX_PARALLEL`.

Model capability data and engine-scoped ladders live under `models` in the JSON,
so model churn does not require editing routing code. The shipped AGY command is a
generic PATH/config value; no personal desktop installation or workspace is assumed.
