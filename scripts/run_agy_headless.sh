#!/usr/bin/env bash
# run_agy_headless.sh — boot a virtual D-Bus + keyring session, then run `agy`.
#
# On a headless Linux VPS the Antigravity CLI (`agy`) can hang waiting for a GNOME
# keyring / D-Bus session that does not exist under cron/systemd. This wrapper
# starts a throwaway dbus session and unlocks a keyring before exec'ing agy, and
# exports TERM=xterm to prevent the no-TTY hang.
#
# NO SECRETS: the keyring password is read from the env var named by
# AGY_KEYRING_PASSWORD_ENV (default: AGY_KEYRING_PASSWORD). This script never
# hardcodes or prints a password. If your VPS uses transplanted CLI session state
# instead of a keyring, you may not need this wrapper at all.
#
# Usage:
#   AGY_KEYRING_PASSWORD=... scripts/run_agy_headless.sh --model gemini-3.1-pro --print "..."
set -euo pipefail

export TERM="${TERM:-xterm}"

PW_ENV="${AGY_KEYRING_PASSWORD_ENV:-AGY_KEYRING_PASSWORD}"
KEYRING_PW="${!PW_ENV:-}"

run_agy() {
  if [ -n "$KEYRING_PW" ] && command -v gnome-keyring-daemon >/dev/null 2>&1; then
    # Feed the password on stdin to unlock the login keyring, then run agy.
    printf '%s' "$KEYRING_PW" | gnome-keyring-daemon --unlock >/dev/null 2>&1 || true
  fi
  exec agy "$@"
}

if command -v dbus-run-session >/dev/null 2>&1; then
  exec dbus-run-session -- bash -c 'run_agy "$@"' _ "$@"
else
  run_agy "$@"
fi
