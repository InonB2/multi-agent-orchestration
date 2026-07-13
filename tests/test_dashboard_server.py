import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import dashboard_server as ds  # noqa: E402


def _request(url, *, headers=None, method="POST"):
    request = Request(url, data=b"{}" if method == "POST" else None, method=method, headers=headers or {})
    with urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read())


def test_sync_now_runs_fixed_export_and_returns_new_payload(tmp_path, monkeypatch):
    usage_file = tmp_path / "agent_activity.json"
    usage_file.write_text(json.dumps({"_meta": {"updated_at": "2026-07-12T10:00:00+03:00"}, "entries": []}), encoding="utf-8")
    monkeypatch.setattr(ds, "SYNC_JSON", usage_file)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        usage_file.write_text(json.dumps({"_meta": {"updated_at": "2026-07-12T10:01:00+03:00"}, "entries": []}), encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ds.subprocess, "run", fake_run)
    server = ds.DashboardServer(("127.0.0.1", 0), ds.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        status, payload = _request(
            origin + "/sync-now",
            headers={"Origin": origin, "X-Dashboard-Sync": "1", "Content-Type": "application/json"},
        )
        assert status == 200
        assert payload["_meta"]["updated_at"] == "2026-07-12T10:01:00+03:00"
        argv, kwargs = calls[0]
        assert argv == [sys.executable, str(ds.SYNC_SCRIPT)]
        assert kwargs["cwd"] == str(ds.ROOT)
        assert kwargs["timeout"] == ds.SYNC_TIMEOUT_SECONDS
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sync_now_rejects_cross_origin_or_missing_custom_header():
    server = ds.DashboardServer(("127.0.0.1", 0), ds.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/sync-now"
        for headers in (
            {"Origin": "https://attacker.example", "X-Dashboard-Sync": "1"},
            {"Origin": f"http://127.0.0.1:{server.server_port}"},
        ):
            try:
                _request(url, headers=headers)
                raise AssertionError("request should have been rejected")
            except HTTPError as exc:
                assert exc.code == 403
        try:
            _request(url, method="GET")
            raise AssertionError("GET should have been rejected")
        except HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
