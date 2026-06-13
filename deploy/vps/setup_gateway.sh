#!/usr/bin/env bash
#
# deploy/vps/setup_gateway.sh — provision the Andy API Gateway (FastAPI/uvicorn)
# behind Caddy (automatic HTTPS) on an Ubuntu VPS, to replace the Railway host.
#
# OPTIONAL (MMOI self-host path). What it does (idempotent — safe to re-run):
#   1. Installs Python 3.11 + venv tooling and Caddy (apt repo)
#   2. Ensures the non-root `mmoi` user owns the gateway
#   3. Creates a venv at /opt/andy-gateway/.venv and installs the gateway's
#      requirements.txt
#   4. Seeds /opt/andy-gateway/.env (mode 600) from the example if absent
#   5. Installs + enables the andy-gateway systemd unit (uvicorn on 127.0.0.1)
#   6. Installs the Caddyfile (reverse proxy + TLS) and reloads Caddy
#   7. Opens ufw for HTTPS (443) and HTTP (80, for ACME) — SSH stays open
#
# PRE-REQUISITE: the gateway SOURCE must already be on the box at $GATEWAY_DIR.
#   The gateway lives in its OWN repo (not this public one). Put it there first,
#   e.g.:   sudo git clone <your-andy-gateway-repo> /opt/andy-gateway
#   or scp the projects/andy-api-gateway folder up. This script never fetches it
#   (it may be private) and never writes secrets.
#
# Set your domain so Caddy can issue a real cert (else it serves self-signed):
#   sudo GATEWAY_DOMAIN=andy.example.com bash deploy/vps/setup_gateway.sh
#
# Run as root (sudo) on Ubuntu 22.04/24.04.

set -euo pipefail

MMOI_USER="${MMOI_USER:-mmoi}"
GATEWAY_DIR="${GATEWAY_DIR:-/opt/andy-gateway}"
GATEWAY_DOMAIN="${GATEWAY_DOMAIN:-}"      # e.g. andy.example.com (empty => local/self-signed)
GATEWAY_PORT="${GATEWAY_PORT:-8000}"      # uvicorn bind port (loopback only)
SERVICE_NAME="andy-gateway"
# Where this script lives, so we can find the unit + Caddyfile templates next to it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "[gw] must run as root (use sudo)." >&2
    exit 1
fi

if [ ! -f "${GATEWAY_DIR}/main.py" ]; then
    echo "[gw] ERROR: gateway source not found at ${GATEWAY_DIR}/main.py." >&2
    echo "[gw]        Put the andy-api-gateway code there first (git clone / scp), then re-run." >&2
    exit 1
fi

echo "[gw] 1/7 installing Python 3.11 + Caddy…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y software-properties-common ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https ufw
if ! command -v python3.11 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
    apt-get install -y python3.11 python3.11-venv
else
    apt-get install -y python3.11-venv || true
fi
# Caddy official apt repo (provides automatic HTTPS via Let's Encrypt).
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -y
    apt-get install -y caddy
fi

echo "[gw] 2/7 ensuring '${MMOI_USER}' owns the gateway dir…"
if ! id "$MMOI_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$MMOI_USER"
fi
chown -R "$MMOI_USER:$MMOI_USER" "$GATEWAY_DIR"

echo "[gw] 3/7 creating venv + installing gateway requirements…"
sudo -u "$MMOI_USER" python3.11 -m venv "${GATEWAY_DIR}/.venv"
sudo -u "$MMOI_USER" "${GATEWAY_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "$MMOI_USER" "${GATEWAY_DIR}/.venv/bin/pip" install -r "${GATEWAY_DIR}/requirements.txt"

echo "[gw] 4/7 seeding ${GATEWAY_DIR}/.env (mode 600) if absent…"
if [ ! -f "${GATEWAY_DIR}/.env" ]; then
    cp "${SCRIPT_DIR}/.env.example" "${GATEWAY_DIR}/.env"
    chown "$MMOI_USER:$MMOI_USER" "${GATEWAY_DIR}/.env"
    chmod 600 "${GATEWAY_DIR}/.env"
    echo "[gw]   created ${GATEWAY_DIR}/.env — EDIT IT: set ANDY_API_KEY + provider keys."
fi

echo "[gw] 5/7 installing systemd unit '${SERVICE_NAME}'…"
# Render the unit with the resolved user/dir/port so it works regardless of overrides.
sed -e "s#@MMOI_USER@#${MMOI_USER}#g" \
    -e "s#@GATEWAY_DIR@#${GATEWAY_DIR}#g" \
    -e "s#@GATEWAY_PORT@#${GATEWAY_PORT}#g" \
    "${SCRIPT_DIR}/${SERVICE_NAME}.service" > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[gw] 6/7 installing Caddyfile + reloading Caddy…"
if [ -n "$GATEWAY_DOMAIN" ]; then
    SITE="$GATEWAY_DOMAIN"
    TLS=""                       # real domain => Caddy auto-issues Let's Encrypt
else
    # No domain: serve on :443 with Caddy's internal (self-signed) CA. Good enough
    # to test end-to-end TLS; swap in a real domain before retiring Railway.
    SITE=":443"
    TLS="tls internal"
    echo "[gw]   no GATEWAY_DOMAIN set — using self-signed TLS on :443 (set a domain for a real cert)."
fi
sed -e "s#@SITE@#${SITE}#g" \
    -e "s#@TLS@#${TLS}#g" \
    -e "s#@GATEWAY_PORT@#${GATEWAY_PORT}#g" \
    "${SCRIPT_DIR}/Caddyfile.example" > /etc/caddy/Caddyfile
caddy fmt --overwrite /etc/caddy/Caddyfile || true
systemctl reload caddy || systemctl restart caddy

echo "[gw] 7/7 configuring ufw (SSH + 80 + 443)…"
ufw allow OpenSSH || ufw allow 22/tcp
ufw allow 80/tcp     # ACME HTTP-01 challenge / redirect to HTTPS
ufw allow 443/tcp    # public gateway endpoint
ufw --force enable

echo
echo "[gw] done."
echo "[gw]   service:  systemctl status ${SERVICE_NAME}"
echo "[gw]   logs:     journalctl -u ${SERVICE_NAME} -f"
echo "[gw]   caddy:    systemctl status caddy"
echo "[gw]   health:   curl -sk https://${GATEWAY_DOMAIN:-localhost}/health"
echo "[gw]   REMEMBER: edit ${GATEWAY_DIR}/.env (ANDY_API_KEY + provider keys), then:"
echo "[gw]            sudo systemctl restart ${SERVICE_NAME}"
