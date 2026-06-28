#!/usr/bin/env bash
# run_agy_headless.sh — boot a virtual D-Bus + keyring session, then run `agy`.
#
# On a headless Linux VPS the Antigravity CLI (`agy`) can hang waiting for a GNOME
# keyring / D-Bus session that does not exist under cron/systemd. This wrapper
# starts a throwaway dbus session and unlocks a keyring before exec'ing agy, and
# exports TERM=xterm. A real PTY (via `script`) is required because --print drops
# stdout without one.
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
    printf '%s' "$KEYRING_PW" | gnome-keyring-daemon --unlock >/dev/null 2>&1 || true
  fi
  exec_agy_in_pty "$@"
}

# Run agy inside a real pseudo-terminal. agy --print drops stdout with no TTY
# (antigravity-cli #76, gemini-cli #27466); TERM alone is not enough. `script`
# is the portable POSIX PTY allocator. (Windows equivalent: ConPTY via pywinpty.)
exec_agy_in_pty() {
  if command -v script >/dev/null 2>&1; then
    local cmd="agy" a
    for a in "$@"; do cmd="$cmd $(printf '%q' "$a")"; done
    if script -qec true /dev/null >/dev/null 2>&1; then
      exec script -qec "$cmd" /dev/null      # util-linux
    else
      exec script -q /dev/null agy "$@"       # BSD/macOS
    fi
  else
    exec agy "$@"                              # no PTY available (output may drop)
  fi
}

if command -v dbus-run-session >/dev/null 2>&1; then
  exec dbus-run-session -- bash -c 'run_agy "$@"' _ "$@"
else
  run_agy "$@"
fi
