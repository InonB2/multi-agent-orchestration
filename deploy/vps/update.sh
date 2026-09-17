#!/usr/bin/env bash
#
# deploy/vps/update.sh — redeploy the latest code on an existing self-host.
#
# OPTIONAL. Pulls the latest main, refreshes deps, runs the test suite, and
# restarts the service only if tests pass (fail-safe: a broken pull never takes
# the running loop down).
#
#   sudo bash deploy/vps/update.sh

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/orchestration}"
APP_USER="${APP_USER:-orchestrator}"
SERVICE_NAME="orchestration-loop"

if [ "$(id -u)" -ne 0 ]; then
    echo "[update] must run as root (use sudo)." >&2
    exit 1
fi

echo "[update] pulling latest code…"
git -C "$APP_DIR" pull --ff-only
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[update] refreshing dependencies…"
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install pytest flake8 "tomli>=1.1.0; python_version<'3.11'"

echo "[update] running tests before restart…"
if sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python -m pytest tests/ -q"; then
    echo "[update] tests passed — restarting service."
    systemctl restart "$SERVICE_NAME"
    echo "[update] done. systemctl status ${SERVICE_NAME}"
else
    echo "[update] TESTS FAILED — leaving the running service untouched. Investigate before retrying." >&2
    exit 1
fi
