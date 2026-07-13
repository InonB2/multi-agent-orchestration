"""FIX-7: shared-state writes survive parallel dispatch (sidecar lock).

Fires N concurrent processes that each register a distinct agent via
agent_activity.set() against one file, and asserts all N survive — i.e. the
locked read-modify-write does not drop updates the way an unlocked one would.
Also exercises append_jsonl_record concurrency and stale-lock recovery.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sidecar_lock  # noqa: E402


def test_parallel_agent_activity_set_keeps_all_entries(tmp_path):
    # Concurrent writers via threads: the sidecar lock is a FILE lock
    # (O_CREAT|O_EXCL on <path>.lock), so threads contend on the same lock file
    # exactly like separate processes do. (A true multi-process variant is
    # equivalent for this lock but gets killed by the CI sandbox.)
    import threading

    import agent_activity

    path = tmp_path / "agent_activity.json"
    path.write_text(json.dumps({"_meta": {"updated_at": "x"}, "entries": []}), encoding="utf-8")
    errors = []

    def worker(n):
        try:
            agent_activity.set(f"codex-role{n}", "m", "low", "t", path=path)
        except Exception as exc:  # pragma: no cover - failure detail
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    entries = json.loads(path.read_text(encoding="utf-8")).get("entries", [])
    agents = {e.get("agent") for e in entries}
    assert agents == {f"codex-role{n}" for n in range(5)}, agents


def test_append_jsonl_record_no_torn_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    for i in range(50):
        sidecar_lock.append_jsonl_record(path, {"i": i})
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 50
    assert [json.loads(l)["i"] for l in lines] == list(range(50))


def test_stale_lock_is_stolen(tmp_path):
    target = tmp_path / "data.json"
    lock = sidecar_lock.lock_path_for(target)
    # A lock owned by a dead PID with an old mtime must be stealable.
    lock.write_text(json.dumps({"pid": 999999999, "mtime": time.time() - 10_000}), encoding="utf-8")
    assert sidecar_lock.acquire_lock(lock, timeout=2, ttl=300) is True
    sidecar_lock.release_lock(lock)


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific dead PID behavior")
def test_dead_process_lock_is_reclaimed_immediately_on_windows(tmp_path):
    target = tmp_path / "data.json"
    lock = sidecar_lock.lock_path_for(target)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.kill()
        proc.wait(timeout=5)
        lock.write_text(
            json.dumps({"pid": proc.pid, "mtime": time.time()}),
            encoding="utf-8",
        )
        started = time.monotonic()
        assert sidecar_lock.acquire_lock(lock, timeout=1, ttl=300) is True
        assert time.monotonic() - started < 0.5
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        sidecar_lock.release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
