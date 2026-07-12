import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agy_usage  # noqa: E402


def test_parse_usage_screen_reads_both_quota_groups():
    screen = """
Models & Quota
user@example.com

GEMINI MODELS
Gemini Flash
Gemini Pro
Weekly Limit 73.25% remaining · Refreshes in 106h 35m
Five Hour Limit 44.50% remaining

CLAUDE AND GPT MODELS
Claude Opus
Claude Sonnet
GPT-OSS
Weekly Limit 61.00% remaining · Refreshes in 12h 05m
Five Hour Limit 18.75% remaining
"""

    detail = agy_usage.parse_usage_screen(screen)

    assert detail["confidence"] == "real"
    assert detail["account"] == "user@example.com"
    assert detail["gemini"]["weekly_pct"] == 73.25
    assert detail["gemini"]["weekly_refresh"] == "106h 35m"
    assert detail["gemini"]["five_hour_pct"] == 44.5
    assert detail["claude_gpt"]["weekly_pct"] == 61.0
    assert detail["claude_gpt"]["five_hour_pct"] == 18.75


def test_parse_usage_screen_returns_honest_error_when_unparseable():
    detail = agy_usage.parse_usage_screen("no quota screen here")

    assert detail["confidence"] == "none"
    assert "error" in detail


def test_parse_usage_screen_reads_current_multiline_quota_layout():
    screen = """
Models & Quota
user@example.com

GEMINI MODELS
  Weekly Limit
    [██████████████████████████████████████████████████] 100.00%
    Quota available
  Five Hour Limit
    [██████████████████████████████████████████████████] 95.50%
    Refreshes in 4h 12m

CLAUDE AND GPT MODELS
  Weekly Limit
    [██████████████████████████████████████████████████] 87.25%
    Refreshes in 106h 35m
  Five Hour Limit
    [██████████████████████████████████████████████████] 100.00%
    Quota available
"""

    detail = agy_usage.parse_usage_screen(screen)

    assert detail["confidence"] == "real"
    assert detail["gemini"]["weekly_pct"] == 100.0
    assert detail["gemini"]["five_hour_pct"] == 95.5
    assert detail["gemini"]["weekly_refresh"] == "4h 12m"
    assert detail["claude_gpt"]["weekly_pct"] == 87.25
    assert detail["claude_gpt"]["five_hour_pct"] == 100.0
    assert detail["claude_gpt"]["weekly_refresh"] == "106h 35m"
