#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_COMMAND=${AOA_PYTHON_COMMAND:-}

if [ -z "$PYTHON_COMMAND" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_COMMAND=$candidate
            break
        fi
    done
fi

if [ -z "$PYTHON_COMMAND" ]; then
    echo "[ERROR] Python 3.8 or newer was not found on PATH. Install Python, then rerun install.sh." >&2
    exit 2
fi

if ! "$PYTHON_COMMAND" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 2)'; then
    echo "[ERROR] Python 3.8 or newer is required." >&2
    exit 2
fi

exec "$PYTHON_COMMAND" "$ROOT/scripts/install.py" --root "$ROOT" "$@"
