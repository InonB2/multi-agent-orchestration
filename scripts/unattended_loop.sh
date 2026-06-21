#!/usr/bin/env bash
# unattended_loop.sh — PTME integration loop (Linux / macOS).
#
# Routes the queue, then drives the per-model supervisors so each task runs under
# its PTME-resolved model + effort. Designed for cron / systemd on a headless VPS.
#
# NO SECRETS: this script reads only env-var NAMES. Provide credentials out of band
# (CLI login state or exported API-key env vars). It never echoes them.
#
# Usage:
#   scripts/unattended_loop.sh                 # one pass over all models
#   MODELS="codex" scripts/unattended_loop.sh  # restrict to a subset
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# agy hangs headless without a TTY hint — inject it for the whole pass.
export TERM="${TERM:-xterm}"

MODELS="${MODELS:-codex antigravity claude-code}"

echo "[unattended_loop] routing queue..."
python scripts/task_router.py

for model in $MODELS; do
  echo "[unattended_loop] supervising model: $model"
  python scripts/model_supervisor.py run --model "$model" || \
    echo "[unattended_loop] supervisor for $model exited non-zero (continuing)"
done

echo "[unattended_loop] pass complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
