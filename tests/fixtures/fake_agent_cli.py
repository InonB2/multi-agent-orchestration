"""Deterministic fake agent executable used by dispatch tests."""

import os
import subprocess
import sys
import time

prompt = sys.stdin.read()
mode = os.environ.get("AOA_FAKE_MODE", "success")
if mode == "timeout":
    if os.environ.get("AOA_FAKE_CHILD_PID"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(os.environ["AOA_FAKE_CHILD_PID"], "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    time.sleep(60)
elif mode == "nonzero":
    print("fake failure", file=sys.stderr)
    raise SystemExit(17)
elif mode == "empty":
    raise SystemExit(0)
else:
    print("FAKE_OK:" + prompt)
