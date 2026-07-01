#!/usr/bin/env python3
"""
usage_bridge_reader.py — honest per-engine USAGE telemetry reader.

Goal: give the dashboard a *truthful* per-engine usage picture. Every engine
returns a normalized usage_detail dict:

    {
        "tokens": int | None,          # token total when knowable, else None
        "window_pct_primary": float | None,   # short-window quota %  (None unless a REAL ceiling exists)
        "window_pct_weekly": float | None,     # weekly-window quota % (None unless a REAL ceiling exists)
        "source": str,                 # exactly where the number came from
        "confidence": "real" | "estimated" | "none",
        "note": str,                   # human-readable honest label
        "resets": {...}                # optional reset metadata (codex only today)
    }

Honesty rules (hard requirements):
  * NEVER emit an unlabeled percentage. A % is only emitted when a REAL quota
    ceiling is known (codex rate_limits). For claude we emit a token total with
    confidence="estimated" and percent=None ("no quota signal").
  * Any read failure degrades to an honest null result — never a crash.
  * stdlib only, no secrets, no shell.

Per engine:
  CODEX  — real. Reads the CURRENT latest ~/.codex/sessions JSONL rate_limits:
           primary (5h) used_percent + secondary (weekly) used_percent + reset
           times + session token total. confidence="real".
  CLAUDE — no trivial local per-engine quota API exists. Best-effort, in order:
           (a) if a local OTel collector export file is present, read tokens
               from it (confidence="real"); else
           (b) sum actual_tokens from this repo's logged ptme_decisions.jsonl
               records attributed to the claude engine
               (confidence="estimated", percent=None, clearly labeled
               "estimated from logged task tokens — not a quota %").
  AGY    — reads exported `/usage` groups when available; otherwise falls back
           to an honest howto. No token total is fabricated.

OTel path for fully-real claude/agy %, when you want to wire it later:
  1. Start a local OpenTelemetry collector with a `file` exporter writing to
     logs/otel_telemetry.json (OTLP-JSON lines).
  2. export CLAUDE_CODE_ENABLE_TELEMETRY=1 and
     OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
  3. Claude Code then exports spans/metrics; read_claude_usage() will pick up
     the file automatically and switch confidence to "real" (tokens from
     gen_ai.usage.* attributes). A % still requires a published quota ceiling,
     which Claude Code OTel does not currently expose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sibling scripts importable (codex_usage / rate_wall_watchdog live one dir up).
_THIS = Path(__file__).resolve()
_SCRIPTS_DIR = _THIS.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_REPO_ROOT = _SCRIPTS_DIR.parent

# Default paths
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_OTEL_OUTPUT_PATH = _REPO_ROOT / "logs" / "otel_telemetry.json"
PTME_LOG_FILE = _REPO_ROOT / "logs" / "ptme_decisions.jsonl"
DEFAULT_AGY_USAGE_PATH = _REPO_ROOT / "logs" / "agy_usage.json"

# How a user actually sees agy usage today (no non-interactive surface exists).
AGY_USAGE_HOWTO = (
    "agy exposes no local token/usage telemetry non-interactively; view usage "
    "via the interactive `/usage` command in the agy TUI or the AI Studio usage "
    "page (https://aistudio.google.com/usage)"
)


def _empty_detail(source: str, note: str, confidence: str = "none") -> Dict[str, Any]:
    return {
        "tokens": None,
        "window_pct_primary": None,
        "window_pct_weekly": None,
        "source": source,
        "confidence": confidence,
        "note": note,
    }


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file, skipping blank/garbage lines. Never raises."""
    rows: List[Dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return rows
    return rows


# --------------------------------------------------------------------------- #
# CODEX — real
# --------------------------------------------------------------------------- #
def read_codex_usage(sessions_root: Path = CODEX_SESSIONS_ROOT) -> Dict[str, Any]:
    """Read REAL codex usage from the CURRENT latest session JSONL.

    Surfaces both rate-limit windows (primary 5h + secondary weekly) with reset
    times, plus the session token total. Any failure => honest null detail.
    """
    try:
        import codex_usage
        import rate_wall_watchdog
    except Exception as exc:  # pragma: no cover - import guard
        return _empty_detail("codex (import failed)", f"codex modules unavailable: {exc}")

    try:
        if not sessions_root.exists():
            return _empty_detail(str(sessions_root), "no ~/.codex/sessions directory found")

        windows = rate_wall_watchdog.read_codex_windows(sessions_root)
        usage = codex_usage.read_latest_usage(sessions_root)
    except Exception as exc:
        return _empty_detail("codex (read failed)", f"codex usage read error: {exc}")

    if not windows.get("found"):
        return _empty_detail(
            windows.get("source") or str(sessions_root),
            "no recent codex session with rate_limits found",
        )

    primary = windows["windows"].get("primary", {})
    secondary = windows["windows"].get("secondary", {})
    detail: Dict[str, Any] = {
        "tokens": usage.get("tokens"),
        "window_pct_primary": primary.get("used_percent"),
        "window_pct_weekly": secondary.get("used_percent"),
        "source": windows.get("source"),
        "confidence": "real",
        "note": "codex rate_limits from latest ~/.codex/sessions JSONL (primary=5h, weekly=secondary)",
        "resets": {
            "primary_resets_at": primary.get("resets_at"),
            "primary_resets_local": primary.get("resets_local"),
            "weekly_resets_at": secondary.get("resets_at"),
            "weekly_resets_local": secondary.get("resets_local"),
        },
    }
    return detail


# --------------------------------------------------------------------------- #
# CLAUDE — honest best-effort (OTel real, else logged-token estimate)
# --------------------------------------------------------------------------- #
def _claude_tokens_from_otel(otel_path: Path) -> Optional[int]:
    """Best-effort: sum gen_ai/llm token attributes from an OTLP-JSON file.

    Supports both JSONL rows and a single JSON array/object. Returns the summed
    token total, or None when nothing usable is found. Never raises.
    """
    candidate_keys = (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "llm.usage.prompt_tokens",
        "llm.usage.completion_tokens",
        "llm.token.count",
    )

    def _attr_value(attr: Dict[str, Any]) -> Optional[int]:
        val = attr.get("value")
        if isinstance(val, dict):
            for k in ("intValue", "int_value", "doubleValue", "double_value"):
                if k in val:
                    try:
                        return int(float(val[k]))
                    except (TypeError, ValueError):
                        return None
        if isinstance(val, (int, float)):
            return int(val)
        return None

    def _walk_spans(obj: Any) -> int:
        total = 0
        if isinstance(obj, dict):
            attrs = obj.get("attributes")
            if isinstance(attrs, list):
                for attr in attrs:
                    if isinstance(attr, dict) and attr.get("key") in candidate_keys:
                        v = _attr_value(attr)
                        if v is not None:
                            total += v
            for value in obj.values():
                total += _walk_spans(value)
        elif isinstance(obj, list):
            for item in obj:
                total += _walk_spans(item)
        return total

    try:
        if not otel_path.exists():
            return None
        text = otel_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return None
        total = 0
        # Try whole-file JSON first, then fall back to JSONL.
        try:
            total = _walk_spans(json.loads(text))
        except json.JSONDecodeError:
            for row in read_jsonl(otel_path):
                total += _walk_spans(row)
        return total if total > 0 else None
    except OSError:
        return None


def _claude_tokens_from_logs(ptme_log: Path = PTME_LOG_FILE) -> Dict[str, Any]:
    """Sum actual_tokens from ptme_decisions records attributed to claude.

    Returns {"tokens": int|None, "records": int}. Never raises.
    """
    total = 0
    matched = 0
    for rec in read_jsonl(ptme_log):
        engine = str(rec.get("engine") or "").strip().lower()
        worker = str(rec.get("worker_id") or "").strip().lower()
        is_claude = engine == "claude" or worker.startswith("claude")
        if not is_claude:
            continue
        raw = rec.get("actual_tokens")
        if raw is None:
            continue
        try:
            total += int(raw)
            matched += 1
        except (TypeError, ValueError):
            continue
    return {"tokens": (total if matched else None), "records": matched}


def read_claude_usage(
    otel_output_path: Path = DEFAULT_OTEL_OUTPUT_PATH,
    ptme_log: Path = PTME_LOG_FILE,
) -> Dict[str, Any]:
    """Honest claude usage: OTel tokens if available, else a labeled estimate.

    A percentage is NEVER emitted (no real local quota ceiling exists for the
    Claude team here). Any failure => honest null.
    """
    try:
        otel_tokens = _claude_tokens_from_otel(otel_output_path)
        if otel_tokens is not None:
            return {
                "tokens": otel_tokens,
                "window_pct_primary": None,
                "window_pct_weekly": None,
                "source": str(otel_output_path),
                "confidence": "real",
                "note": "tokens from local OTel export; no quota signal (Claude Code OTel exposes no used_percent)",
            }

        logged = _claude_tokens_from_logs(ptme_log)
        if logged["tokens"] is not None:
            return {
                "tokens": logged["tokens"],
                "window_pct_primary": None,
                "window_pct_weekly": None,
                "source": f"{ptme_log} ({logged['records']} logged claude tasks)",
                "confidence": "estimated",
                "note": "estimated from logged task tokens - not a quota %; no quota signal",
            }

        return _empty_detail(
            str(otel_output_path),
            "no local OTel export and no logged claude task tokens; no quota signal",
        )
    except Exception as exc:  # never crash
        return _empty_detail("claude (read failed)", f"claude usage read error: {exc}")


# --------------------------------------------------------------------------- #
# AGY — real /usage groups when exported, else honest howto
# --------------------------------------------------------------------------- #
def _coerce_pct(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_agy_usage_groups(usage_file: Path) -> Dict[str, float]:
    try:
        if not usage_file.exists():
            return {}
        payload = json.loads(usage_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}

    groups = payload.get("groups")
    if not isinstance(groups, list):
        return {}

    mapped: Dict[str, float] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or group.get("group") or "").strip().lower()
        pct = _coerce_pct(group.get("used_percent"))
        if not name or pct is None:
            continue
        mapped[name] = pct
    return mapped


def read_agy_usage(usage_file: Path = DEFAULT_AGY_USAGE_PATH) -> Dict[str, Any]:
    """Read exported agy `/usage` groups when present, else return honest howto."""
    try:
        groups = _read_agy_usage_groups(usage_file)
        if groups:
            primary = groups.get("daily")
            if primary is None:
                primary = groups.get("primary")
            weekly = groups.get("weekly")
            return {
                "tokens": None,
                "window_pct_primary": primary,
                "window_pct_weekly": weekly,
                "source": str(usage_file),
                "confidence": "real",
                "note": "real quota % from exported agy /usage groups",
            }

        return _empty_detail(
            "{} (no exported usage groups)".format(usage_file),
            AGY_USAGE_HOWTO,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return _empty_detail("agy (read failed)", f"agy usage read error: {exc}")


def read_all() -> Dict[str, Dict[str, Any]]:
    return {
        "codex": read_codex_usage(),
        "claude": read_claude_usage(),
        "agy": read_agy_usage(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Honest per-engine usage-telemetry reader")
    parser.add_argument("--engine", choices=["codex", "claude", "agy", "all"], default="all")
    parser.add_argument("--codex-sessions-root", type=Path, default=CODEX_SESSIONS_ROOT)
    parser.add_argument("--otel-file", type=Path, default=DEFAULT_OTEL_OUTPUT_PATH)
    parser.add_argument("--ptme-log", type=Path, default=PTME_LOG_FILE)
    args = parser.parse_args()

    if args.engine == "codex":
        out: Any = read_codex_usage(args.codex_sessions_root)
    elif args.engine == "claude":
        out = read_claude_usage(args.otel_file, args.ptme_log)
    elif args.engine == "agy":
        out = read_agy_usage()
    else:
        out = {
            "codex": read_codex_usage(args.codex_sessions_root),
            "claude": read_claude_usage(args.otel_file, args.ptme_log),
            "agy": read_agy_usage(),
        }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
