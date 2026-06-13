#!/usr/bin/env bash
#
# deploy/vps/run_loop.sh — continuous orchestration loop for 24/7 self-host.
#
# OPTIONAL. This is what the systemd unit (orchestration-loop.service) runs.
# It periodically routes any unrouted tasks and reports the resume queue, so a
# self-hosted instance keeps the task pipeline moving without a human at a
# terminal. It uses only the framework's existing stdlib CLI scripts.
#
# Tunables come from the .env file loaded by systemd (ROUTER_INTERVAL, APP_DIR,
# PYTHON_BIN). When run by hand, sensible defaults apply.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/orchestration}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ROUTER_INTERVAL="${ROUTER_INTERVAL:-300}"

cd "$APP_DIR"

echo "[loop] starting orchestration loop (interval=${ROUTER_INTERVAL}s, app=${APP_DIR})"

while true; do
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[loop] ${ts} routing pass"

    # Route any unrouted tasks. Tolerate a missing active_tasks.json (nothing to do yet).
    if [ -f "tasks/active_tasks.json" ]; then
        "$PYTHON_BIN" scripts/task_router.py || echo "[loop] router exited non-zero (continuing)"
        "$PYTHON_BIN" scripts/checkpoint.py list-resumable || true
    else
        echo "[loop] tasks/active_tasks.json not present yet — skipping pass"
    fi

    sleep "$ROUTER_INTERVAL"
done
