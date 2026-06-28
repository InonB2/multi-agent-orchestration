"""Tests for orchestrator_stats usage_detail wiring (honest per-engine usage)."""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "usage_bridge"))

import orchestrator_stats as os_stats  # noqa: E402


def _by_id(payload, oid):
    return next(o for o in payload["orchestrators"] if o["id"] == oid)


def test_every_orchestrator_has_usage_detail():
    payload = os_stats.build_stats()
    assert len(payload["orchestrators"]) == 4
    for o in payload["orchestrators"]:
        d = o["usage_detail"]
        for key in ("tokens", "source", "confidence", "note"):
            assert key in d, f"{o['id']} usage_detail missing {key}"
        assert d["confidence"] in ("real", "estimated", "none")


def test_no_unlabeled_percent():
    """A percentage is only present when confidence is real (a known ceiling)."""
    payload = os_stats.build_stats()
    for o in payload["orchestrators"]:
        if o["usage_pct_primary"] is not None or o["usage_pct_weekly"] is not None:
            # codex is the only engine that legitimately reports a %.
            assert o["id"] == "codex"
            assert o["usage_detail"]["confidence"] == "real"


def test_usage_detail_resilient_to_reader_failure(monkeypatch):
    def _boom():
        raise RuntimeError("reader down")

    monkeypatch.setattr(os_stats.usage_bridge_reader, "read_all", _boom)
    details = os_stats._usage_details()
    assert set(details.keys()) == {"codex", "claude", "agy"}
    for d in details.values():
        assert d["confidence"] == "none"
        assert d["tokens"] is None


def test_root_detail_is_orchestrator_label():
    payload = os_stats.build_stats()
    root = _by_id(payload, "root")
    assert root["usage_detail"]["confidence"] == "none"
    assert "orchestrator" in root["usage_detail"]["note"].lower()
