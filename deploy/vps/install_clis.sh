#!/usr/bin/env bash
#
# deploy/vps/install_clis.sh — install the three agent CLIs for MMOI on Ubuntu.
#
# OPTIONAL (MMOI CLI-default path). Installs, as the non-root `mmoi` user:
#   - Node.js 20 LTS  (runtime for claude + codex npm packages)
#   - Claude Code      (`claude`)  via npm   — Anthropic
#   - Codex CLI        (`codex`)   via npm   — OpenAI
#   - Antigravity      (`agy`)     via official installer — Google (free preview)
#
# It installs the BINARIES only. It does NOT log any CLI in — each CLI requires
# an interactive, per-user device-code / OAuth login that YOU must run once as
# the `mmoi` user on the box (see HOSTINGER_VPS_SETUP_GUIDE.md, "CLI logins").
#
# ToS NOTE (be honest with yourself before relying on these):
#   - Claude: `claude -p` draws from a capped monthly Agent-SDK credit from
#     2026-06-15 on subscription plans. setup-token is sanctioned; do NOT copy
#     credentials between machines (ToS violation) — generate them ON this box.
#   - Codex: OpenAI recommends API keys for programmatic use; consumer ChatGPT
#     Plus/Pro headless automation is NOT officially supported. Included per
#     owner decision; treat it as the highest-risk leg and keep an API fallback.
#   - Antigravity: free public preview, native Linux, headless device-code —
#     the cleanest server fit, but preview terms/limits can change without SLA.
#
# Run as root (sudo); it drops to `mmoi` for the per-user installs:
#   sudo bash deploy/vps/install_clis.sh

set -euo pipefail

MMOI_USER="${MMOI_USER:-mmoi}"
NODE_MAJOR="${NODE_MAJOR:-20}"

if [ "$(id -u)" -ne 0 ]; then
    echo "[clis] must run as root (use sudo)." >&2
    exit 1
fi

echo "[clis] 1/5 ensuring base packages (curl, git, libsecret for agy keyring)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl ca-certificates git gnupg libsecret-1-0

echo "[clis] 2/5 ensuring non-root '${MMOI_USER}' user exists (with a login shell)…"
# Unlike the locked-down 'orchestrator' loop account, mmoi NEEDS a real shell:
# the CLI logins are interactive and credentials are cached in this user's home.
if ! id "$MMOI_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$MMOI_USER"
fi

echo "[clis] 3/5 installing Node.js ${NODE_MAJOR} LTS (NodeSource)…"
if ! command -v node >/dev/null 2>&1 || \
   [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -lt "$NODE_MAJOR" ]; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y nodejs
fi
# Use a per-user global npm prefix so we never need sudo for npm and the CLIs
# live in the mmoi user's home (keeps installs non-root, on PATH for that user).
sudo -u "$MMOI_USER" bash -lc '
    set -e
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    grep -q ".npm-global/bin" "$HOME/.bashrc" 2>/dev/null || \
        echo "export PATH=\"\$HOME/.npm-global/bin:\$PATH\"" >> "$HOME/.bashrc"
'

echo "[clis] 4/5 installing Claude Code + Codex CLI (npm, as ${MMOI_USER})…"
sudo -u "$MMOI_USER" bash -lc '
    set -e
    export PATH="$HOME/.npm-global/bin:$PATH"
    npm install -g @anthropic-ai/claude-code
    npm install -g @openai/codex
'

echo "[clis] 5/5 installing Antigravity CLI (agy, as ${MMOI_USER})…"
# Official installer drops the `agy` binary into ~/.local/bin on Linux.
sudo -u "$MMOI_USER" bash -lc '
    set -e
    curl -fsSL https://antigravity.google/install.sh | bash
    grep -q ".local/bin" "$HOME/.bashrc" 2>/dev/null || \
        echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.bashrc"
'

echo "[clis] done. Installed binaries (versions):"
sudo -u "$MMOI_USER" bash -lc '
    export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
    echo -n "  claude: "; claude --version 2>/dev/null || echo "(installed; not on PATH yet — re-login)"
    echo -n "  codex:  "; codex --version  2>/dev/null || echo "(installed; not on PATH yet — re-login)"
    echo -n "  agy:    "; agy --version    2>/dev/null || echo "(installed; not on PATH yet — re-login)"
'
echo
echo "[clis] NEXT: log each CLI in ONCE, interactively, as the mmoi user:"
echo "  sudo -iu ${MMOI_USER}"
echo "    claude setup-token        # prints URL+code; creates ~/.claude OAuth token (Pro/Max plan)"
echo "    codex login               # device-code login; caches ~/.codex/auth.json"
echo "    agy login                 # device-code login; caches creds in libsecret keyring"
echo "  See HOSTINGER_VPS_SETUP_GUIDE.md for the full login walkthrough + ToS flags."
