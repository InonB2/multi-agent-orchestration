#!/usr/bin/env python3
"""Loopback-only static dashboard server with a real usage sync endpoint."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"
SYNC_SCRIPT = ROOT / "scripts" / "telemetry_refresh.py"
SYNC_JSON = DASHBOARD_DIR / "agent_activity.json"
MAX_REQUEST_BODY = 1024
SYNC_TIMEOUT_SECONDS = 45


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, dashboard_dir: Path = DASHBOARD_DIR):
        super().__init__(server_address, handler_class)
        self.sync_lock = threading.Lock()
        self.dashboard_dir = dashboard_dir


class DashboardHandler(SimpleHTTPRequestHandler):
    server: DashboardServer

    def __init__(self, *args, **kwargs):
        server = args[2]
        super().__init__(*args, directory=str(server.dashboard_dir), **kwargs)

    def _loopback_host_ok(self) -> bool:
        host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _same_origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "http"
            and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == self.server.server_port
        )

    def _json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/sync-now":
            self._json_response(405, {"error": "POST required"})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/sync-now":
            self._json_response(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_response(400, {"error": "invalid content length"})
            return
        if length < 0 or length > MAX_REQUEST_BODY:
            self._json_response(413, {"error": "request body too large"})
            return
        if length:
            self.rfile.read(length)
        if (
            not self._loopback_host_ok()
            or not self._same_origin_ok()
            or self.headers.get("X-Dashboard-Sync") != "1"
        ):
            self._json_response(403, {"error": "loopback same-origin request required"})
            return

        if not self.server.sync_lock.acquire(blocking=False):
            self._json_response(409, {"error": "sync already in progress"})
            return
        try:
            try:
                before = json.loads(SYNC_JSON.read_text(encoding="utf-8")).get("_meta", {}).get("updated_at")
            except (OSError, json.JSONDecodeError):
                before = None
            result = subprocess.run(
                [sys.executable, str(SYNC_SCRIPT)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=SYNC_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                self._json_response(500, {"error": "usage export failed"})
                return
            try:
                payload = json.loads(SYNC_JSON.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._json_response(500, {"error": "fresh usage export could not be read"})
                return
            updated_at = payload.get("_meta", {}).get("updated_at")
            if before is not None and str(updated_at or "") <= str(before):
                self._json_response(500, {"error": "usage export timestamp did not advance"})
                return
            self._json_response(200, payload)
        except subprocess.TimeoutExpired:
            self._json_response(504, {"error": "usage export timed out"})
        finally:
            self.server.sync_lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the AOA dashboard with live usage sync")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "::1"))
    parser.add_argument("--port", type=int, default=7780)
    parser.add_argument("--directory", type=Path, default=DASHBOARD_DIR,
                        help="Dashboard asset directory (default: packaged dashboard)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dashboard_dir = args.directory.expanduser().resolve()
    if not (dashboard_dir / "index.html").is_file():
        print("Dashboard directory is missing index.html: {}".format(dashboard_dir), file=sys.stderr)
        return 2
    server = DashboardServer((args.host, args.port), DashboardHandler, dashboard_dir)
    print(f"Dashboard: http://{args.host}:{server.server_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
