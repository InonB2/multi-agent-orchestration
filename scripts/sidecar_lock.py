#!/usr/bin/env python3
"""Shared sidecar-lock helpers for cross-process file coordination."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, TypeVar


DEFAULT_LOCK_TIMEOUT_SECONDS = 10
DEFAULT_LOCK_TTL_SECONDS = 300
LOCK_POLL_SECONDS = 0.05

T = TypeVar("T")


def lock_path_for(path: Path) -> Path:
    return Path(str(path) + ".lock")


def _lock_payload(now: float) -> str:
    return json.dumps(
        {
            "pid": os.getpid(),
            "mtime": now,
        },
        ensure_ascii=False,
    )


def _read_lock_meta(lock_path: Path) -> tuple[int | None, float | None]:
    pid = None
    mtime = None
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
        if text:
            payload = json.loads(text)
            raw_pid = payload.get("pid")
            raw_mtime = payload.get("mtime")
            if raw_pid is not None:
                pid = int(raw_pid)
            if raw_mtime is not None:
                mtime = float(raw_mtime)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        stat_mtime = lock_path.stat().st_mtime
    except OSError:
        stat_mtime = None
    if mtime is None:
        mtime = stat_mtime
    return pid, mtime


def _pid_is_alive_windows(pid: int) -> bool:
    """Windows-correct liveness check via GetExitCodeProcess.

    os.kill(pid, 0) is NOT reliable here: CPython's nt.kill implements sig=0
    by calling OpenProcess()+TerminateProcess(handle, 0). OpenProcess can
    still SUCCEED for a process that has already exited as long as some
    handle to it is still held anywhere on the system (e.g. the very
    subprocess.Popen object that killed it, in the same process) — Windows
    does not recycle a PID while any handle remains open. TerminateProcess on
    an already-terminated process then also "succeeds" (no-op), so os.kill
    raises nothing and a genuinely-dead holder looks alive. This is exactly
    the AGY-crash-mid-edit scenario this lock exists to reclaim from.

    The correct check is GetExitCodeProcess: a handle can stay open on a dead
    process, but its exit code will no longer be STILL_ACTIVE (259).
    """
    import ctypes

    STILL_ACTIVE = 259
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        # Access denied means the pid exists but we can't query it -- assume
        # alive (fail safe, never steal a lock we can't prove is dead).
        # Anything else (e.g. ERROR_INVALID_PARAMETER for a pid that no
        # longer exists at all) means dead.
        return err == ERROR_ACCESS_DENIED
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True  # couldn't read exit code -- be conservative, assume alive
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            return _pid_is_alive_windows(pid)
        except OSError:
            return True  # ctypes failure -- fail safe, assume alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _stale_lock(lock_path: Path, ttl: float) -> bool:
    pid, mtime = _read_lock_meta(lock_path)
    now = time.time()
    too_old = mtime is not None and (now - mtime) > ttl
    holder_dead = pid is not None and not _pid_is_alive(pid)
    holder_unknown = pid is None and too_old
    return bool(too_old or holder_dead or holder_unknown)


def acquire_lock(
    lock_path: Path,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ttl: float = DEFAULT_LOCK_TTL_SECONDS,
) -> bool:
    """Create *lock_path* exclusively, stealing stale locks when safe."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                now = time.time()
                os.write(fd, _lock_payload(now).encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            if _stale_lock(lock_path, ttl):
                try:
                    lock_path.unlink()
                    continue
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
            time.sleep(LOCK_POLL_SECONDS)
    return False


def release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def locked_update(
    path: Path,
    mutate_fn: Callable[[T], T | None],
    *,
    load_fn: Callable[[Path], T],
    save_fn: Callable[[T, Path], None],
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ttl: float = DEFAULT_LOCK_TTL_SECONDS,
) -> T | None:
    lock_path = lock_path_for(path)
    if not acquire_lock(lock_path, timeout=timeout, ttl=ttl):
        raise RuntimeError("Could not acquire lock on {}".format(path))
    try:
        payload = load_fn(path)
        updated = mutate_fn(payload)
        if updated is not None:
            save_fn(updated, path)
        return updated
    finally:
        release_lock(lock_path)


def append_jsonl_record(
    path: Path,
    record: dict,
    *,
    timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ttl: float = DEFAULT_LOCK_TTL_SECONDS,
) -> None:
    lock_path = lock_path_for(path)
    if not acquire_lock(lock_path, timeout=timeout, ttl=ttl):
        raise RuntimeError("Could not acquire lock on {}".format(path))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        release_lock(lock_path)
