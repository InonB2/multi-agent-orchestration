#!/usr/bin/env bash
#
# deploy/vps/setup.sh — one-shot provisioning for an OPTIONAL Hostinger (Ubuntu)
# VPS self-host of the multi-agent orchestration framework.
#
# OPTIONAL. Railway / managed hosting is the default (see README). Use this only
# if you specifically want a 24/7 self-hosted instance you control.
#
# What it does (idempotent — safe to re-run):
#   1. Installs Python 3.11 + git + ufw
#   2. Creates a non-root 'orchestrator' service user
#   3. Clones (or updates) the repo into /opt/orchestration
#   4. Creates a virtualenv and installs dev deps (pytest/flake8/tomli)
#   5. Installs + enables the systemd loop service (auto-restart)
#   6. Configures a basic ufw firewall (SSH only)
#
# It does NOT provision the VPS, create SSH keys, or inject any secrets — you
# bring those. See docs/self-hosting-vps.md for the full manual hardening guide.
#
# Run as root (or via sudo) on a fresh Ubuntu 22.04/24.04 VPS:
#   sudo bash deploy/vps/setup.sh

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/InonB2/multi-agent-orchestration.git}"
APP_DIR="${APP_DIR:-/opt/orchestration}"
APP_USER="${APP_USER:-orchestrator}"
SERVICE_NAME="orchestration-loop"

if [ "$(id -u)" -ne 0 ]; then
    echo "[setup] must run as root (use sudo)." >&2
    exit 1
fi

echo "[setup] 1/6 installing system packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y software-properties-common ca-certificates git ufw
# Python 3.11: present on 22.04 via deadsnakes, native on 24.04.
if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
    apt-get install -y python3.11 python3.11-venv
else
    apt-get install -y python3.11-venv || true
fi

echo "[setup] 2/6 creating non-root service user '${APP_USER}'…"
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

echo "[setup] 3/6 cloning/updating repo into ${APP_DIR}…"
if [ -d "${APP_DIR}/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "${APP_DIR}/tasks"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[setup] 4/6 creating virtualenv + installing dev deps…"
sudo -u "$APP_USER" python3.11 -m venv "${APP_DIR}/.venv"
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
# Core framework is stdlib-only; these are only for tests/lint + TOML on <3.11.
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install pytest flake8 "tomli>=1.1.0; python_version<'3.11'"

echo "[setup] 5/6 installing systemd service…"
if [ ! -f "${APP_DIR}/.env" ]; then
    cp "${APP_DIR}/deploy/vps/.env.example" "${APP_DIR}/.env"
    chown "$APP_USER:$APP_USER" "${APP_DIR}/.env"
    chmod 600 "${APP_DIR}/.env"
    echo "[setup]   created ${APP_DIR}/.env from .env.example — EDIT IT to add your API keys."
fi
install -m 644 "${APP_DIR}/deploy/vps/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[setup] 6/6 configuring ufw firewall (SSH only)…"
ufw allow OpenSSH || ufw allow 22/tcp
ufw --force enable

echo "[setup] done. Check status with:  systemctl status ${SERVICE_NAME}"
echo "[setup] tail logs with:           journalctl -u ${SERVICE_NAME} -f"
echo "[setup] REMEMBER to edit ${APP_DIR}/.env and add your API keys, then:  systemctl restart ${SERVICE_NAME}"
