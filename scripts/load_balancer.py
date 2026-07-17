#!/usr/bin/env python3
"""
load_balancer.py — AOA-11 fail-closed, per-pool quota load balancer.

WHAT THIS IS
------------
The single policy path between a task planner and common dispatch
(scripts/dispatch.py -> scripts/adapters/<engine>.py). It decides WHICH
engine + exact model + effort a task should run on, given:

  * the task's classified role/complexity (ptme.classify_complexity, reused),
  * capability + rate-wall availability (router.py's existing scoring/wall
    checks, reused — this module does not re-implement capability scoring),
  * a LIVE per-pool quota snapshot (codex / claude / agy-gemini /
    agy-claude_gpt), collected from the existing honest usage readers
    (usage_bridge_reader, agy_usage) — never duplicated, never fabricated.

FAIL-CLOSED CONTRACT (the reason AOA-11 exists)
------------------------------------------------
  * Refuse dispatch when ANY relevant pool window is <10.0% remaining.
    Exactly 10.0% remaining PASSES (see meets_floor()).
  * Unknown quota (no real/estimated reading) is NEVER treated as 100% — it
    is ineligible unless load_balancer.allow_unknown_quota is set or an
    authenticated owner override with a non-empty reason is supplied.
  * QA/worker engine separation is a hard, unbypassable gate: the worker's
    engine is excluded from QA candidates before scoring, override or not.
  * If every candidate is ineligible, RETURN AND LOG a refusal. Never invent
    a route. (router.py's own "all-walled -> fail open" behavior is
    deliberately NOT reused here — see _ordered_candidates().)

POOLS
-----
  "codex"            — codex CLI, single rate-walled account.
  "claude"           — native Claude lane. No real local quota ceiling
                        exists; only a configured estimate (or none).
  "agy_gemini"       — AGY's Gemini models (Gemini 3.x Flash/Pro).
  "agy_claude_gpt"   — AGY's shared Claude + GPT-OSS pool.

Every dispatch decision (selected, refused, or owner_override_selected) is
appended atomically to logs/ptme_decisions.jsonl (schema addition, see
_log_decision) AND to logs/learning_loop.jsonl so outcome/QA feedback can be
back-annotated later exactly the way dispatch_worker.complete() already does
for ptme decisions. Never logs prompts, account/email, raw quota screens,
credentials, or secrets.

stdlib-only except for the existing project modules it composes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config_loader
import ptme
import router
import sidecar_lock
from usage_bridge import usage_bridge_reader

ROOT = Path(__file__).resolve().parent.parent
DECISIONS_LOG = ROOT / "logs" / "ptme_decisions.jsonl"
LEARNING_LOG = ROOT / "logs" / "learning_loop.jsonl"
DEFAULT_AGY_SNAPSHOT = ROOT / "logs" / "agy_usage.json"
DEFAULT_CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

DEFAULT_MIN_REMAINING_PCT = 10.0
DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 3600
DEFAULT_ALLOW_UNKNOWN_QUOTA = False
DEFAULT_HISTORY_LOOKBACK = 25

# Rough reservation (percentage points of pool headroom) held back per
# complexity tier before the safety floor is re-checked (spec rule 3: refuse
# when PROJECTED remaining would cross the floor, not just current remaining).
RESERVE_PCT_BY_COMPLEXITY = {"S": 0.5, "M": 1.0, "L": 2.5, "XL": 5.0}

POOL_CODEX = "codex"
POOL_CLAUDE = "claude"
POOL_AGY_GEMINI = "agy_gemini"
POOL_AGY_CLAUDE_GPT = "agy_claude_gpt"
ALL_POOLS = (POOL_CODEX, POOL_CLAUDE, POOL_AGY_GEMINI, POOL_AGY_CLAUDE_GPT)


class LoadBalancerRefusal(RuntimeError):
    """Raised by build_dispatch_request() when a decision has no eligible route."""

    def __init__(self, decision: "LoadBalancerDecision") -> None:
        super().__init__(decision.rationale)
        self.decision = decision


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def _lb_config(config: dict | None = None) -> dict:
    """Resolve load_balancer.* settings, defaulting anything unset.

    aoa.config.json may carry a top-level "load_balancer" object; unknown/
    missing keys fall back to the documented defaults so the balancer works
    with zero config changes.
    """
    config = config if config is not None else config_loader.load_aoa_config()
    raw = config.get("load_balancer") if isinstance(config, dict) else None
    lb = dict(raw) if isinstance(raw, dict) else {}
    lb.setdefault("min_remaining_pct", DEFAULT_MIN_REMAINING_PCT)
    lb.setdefault("max_snapshot_age_seconds", DEFAULT_MAX_SNAPSHOT_AGE_SECONDS)
    lb.setdefault("allow_unknown_quota", DEFAULT_ALLOW_UNKNOWN_QUOTA)
    lb.setdefault("claude_estimated_remaining_pct", None)
    lb.setdefault("history_lookback", DEFAULT_HISTORY_LOOKBACK)
    lb.setdefault("reserve_pct_by_complexity", dict(RESERVE_PCT_BY_COMPLEXITY))
    return lb


# --------------------------------------------------------------------------- #
# quota collection — reads EXISTING honest usage readers, never re-parses raw
# telemetry itself. Codex + Claude come from usage_bridge_reader (already
# built for exactly this). AGY comes from a cached agy_usage.py capture
# snapshot (agy_usage.py's OWN output shape: {"gemini": {...}, "claude_gpt":
# {...}}), NOT usage_bridge_reader.read_agy_usage() — that reader expects a
# different {"groups": [...]} file shape that does not match what agy_usage.py
# actually emits. See report to Andy for this judgment call.
# --------------------------------------------------------------------------- #
def _age_seconds(path: Any, now: float | None = None) -> float | None:
    if not path:
        return None
    try:
        p = Path(str(path))
        if not p.exists():
            return None
        return (now if now is not None else time.time()) - p.stat().st_mtime
    except OSError:
        return None


def _codex_snapshot(
    lb_cfg: dict, sessions_root: Path = DEFAULT_CODEX_SESSIONS_ROOT, now: float | None = None
) -> dict:
    detail = usage_bridge_reader.read_codex_usage(sessions_root)
    primary_used = detail.get("window_pct_primary")
    weekly_used = detail.get("window_pct_weekly")
    remaining_candidates = []
    if primary_used is not None:
        remaining_candidates.append(100.0 - float(primary_used))
    if weekly_used is not None:
        remaining_candidates.append(100.0 - float(weekly_used))
    remaining = min(remaining_candidates) if remaining_candidates else None
    source = detail.get("source")
    age = _age_seconds(source, now)
    stale = age is not None and age > float(lb_cfg["max_snapshot_age_seconds"])
    confidence = detail.get("confidence") if (remaining is not None and not stale) else "none"
    return {
        "pool": POOL_CODEX,
        "engine": "codex",
        "remaining_pct": remaining if confidence != "none" else None,
        "windows": {"primary_used_pct": primary_used, "weekly_used_pct": weekly_used},
        "source": source,
        "confidence": confidence,
        "age_seconds": age,
    }


def _empty_agy_pool_snapshot(pool: str, source: str, note: str, age: float | None) -> dict:
    return {
        "pool": pool,
        "engine": "agy",
        "remaining_pct": None,
        "windows": {},
        "source": source,
        "confidence": "none",
        "age_seconds": age,
        "note": note,
    }


def _agy_group_remaining(group: dict) -> float | None:
    values = [v for v in (group.get("weekly_pct"), group.get("five_hour_pct")) if v is not None]
    return min(values) if values else None


def _agy_snapshots(
    lb_cfg: dict, snapshot_path: Path = DEFAULT_AGY_SNAPSHOT, now: float | None = None
) -> dict:
    age = _age_seconds(snapshot_path, now)
    max_age = float(lb_cfg["max_snapshot_age_seconds"])
    source = str(snapshot_path)
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else None
    except (OSError, json.JSONDecodeError):
        payload = None

    if not isinstance(payload, dict):
        note = "no exported agy usage snapshot at {}".format(source)
        return {
            POOL_AGY_GEMINI: _empty_agy_pool_snapshot(POOL_AGY_GEMINI, source, note, age),
            POOL_AGY_CLAUDE_GPT: _empty_agy_pool_snapshot(POOL_AGY_CLAUDE_GPT, source, note, age),
        }
    if payload.get("confidence") != "real":
        note = "agy usage snapshot confidence={!r} (not real)".format(payload.get("confidence"))
        return {
            POOL_AGY_GEMINI: _empty_agy_pool_snapshot(POOL_AGY_GEMINI, source, note, age),
            POOL_AGY_CLAUDE_GPT: _empty_agy_pool_snapshot(POOL_AGY_CLAUDE_GPT, source, note, age),
        }
    if age is not None and age > max_age:
        note = "agy usage snapshot stale ({:.0f}s > {:.0f}s max)".format(age, max_age)
        return {
            POOL_AGY_GEMINI: _empty_agy_pool_snapshot(POOL_AGY_GEMINI, source, note, age),
            POOL_AGY_CLAUDE_GPT: _empty_agy_pool_snapshot(POOL_AGY_CLAUDE_GPT, source, note, age),
        }

    gemini = payload.get("gemini") or {}
    claude_gpt = payload.get("claude_gpt") or {}
    gemini_remaining = _agy_group_remaining(gemini)
    claude_gpt_remaining = _agy_group_remaining(claude_gpt)
    return {
        POOL_AGY_GEMINI: {
            "pool": POOL_AGY_GEMINI,
            "engine": "agy",
            "remaining_pct": gemini_remaining,
            "windows": {"weekly_pct": gemini.get("weekly_pct"), "five_hour_pct": gemini.get("five_hour_pct")},
            "source": source,
            "confidence": "real" if gemini_remaining is not None else "none",
            "age_seconds": age,
        },
        POOL_AGY_CLAUDE_GPT: {
            "pool": POOL_AGY_CLAUDE_GPT,
            "engine": "agy",
            "remaining_pct": claude_gpt_remaining,
            "windows": {
                "weekly_pct": claude_gpt.get("weekly_pct"),
                "five_hour_pct": claude_gpt.get("five_hour_pct"),
            },
            "source": source,
            "confidence": "real" if claude_gpt_remaining is not None else "none",
            "age_seconds": age,
        },
    }


def _claude_snapshot(lb_cfg: dict) -> dict:
    ceiling = lb_cfg.get("claude_estimated_remaining_pct")
    if ceiling is None:
        return {
            "pool": POOL_CLAUDE,
            "engine": "claude",
            "remaining_pct": None,
            "windows": {},
            "source": "no configured ceiling — Claude Code exposes no real quota %",
            "confidence": "none",
            "age_seconds": None,
        }
    return {
        "pool": POOL_CLAUDE,
        "engine": "claude",
        "remaining_pct": float(ceiling),
        "windows": {},
        "source": "aoa.config.json load_balancer.claude_estimated_remaining_pct",
        "confidence": "estimated",
        "age_seconds": None,
    }


def collect_quota(
    lb_cfg: dict | None = None,
    *,
    sessions_root: Path = DEFAULT_CODEX_SESSIONS_ROOT,
    agy_snapshot_path: Path = DEFAULT_AGY_SNAPSHOT,
    now: float | None = None,
) -> dict[str, dict]:
    """Return {pool_name: snapshot} for every pool. Never raises."""
    lb_cfg = lb_cfg if lb_cfg is not None else _lb_config()
    snapshot: dict[str, dict] = {}
    try:
        snapshot[POOL_CODEX] = _codex_snapshot(lb_cfg, sessions_root, now)
    except Exception as exc:  # pragma: no cover - defensive
        snapshot[POOL_CODEX] = {
            "pool": POOL_CODEX, "engine": "codex", "remaining_pct": None, "windows": {},
            "source": "codex (read failed)", "confidence": "none", "age_seconds": None,
            "note": str(exc),
        }
    try:
        snapshot.update(_agy_snapshots(lb_cfg, agy_snapshot_path, now))
    except Exception as exc:  # pragma: no cover - defensive
        note = "agy usage read error: {}".format(exc)
        snapshot[POOL_AGY_GEMINI] = _empty_agy_pool_snapshot(POOL_AGY_GEMINI, str(agy_snapshot_path), note, None)
        snapshot[POOL_AGY_CLAUDE_GPT] = _empty_agy_pool_snapshot(
            POOL_AGY_CLAUDE_GPT, str(agy_snapshot_path), note, None
        )
    try:
        snapshot[POOL_CLAUDE] = _claude_snapshot(lb_cfg)
    except Exception as exc:  # pragma: no cover - defensive
        snapshot[POOL_CLAUDE] = {
            "pool": POOL_CLAUDE, "engine": "claude", "remaining_pct": None, "windows": {},
            "source": "claude (read failed)", "confidence": "none", "age_seconds": None,
            "note": str(exc),
        }
    return snapshot


def _redact_quota(quota: dict[str, dict]) -> dict[str, dict]:
    """Keep only numeric/label fields; never propagate raw screens/accounts."""
    redacted = {}
    for pool, snap in quota.items():
        redacted[pool] = {
            "pool": snap.get("pool", pool),
            "engine": snap.get("engine"),
            "remaining_pct": snap.get("remaining_pct"),
            "windows": snap.get("windows", {}),
            "source": snap.get("source"),
            "confidence": snap.get("confidence"),
            "age_seconds": (round(snap["age_seconds"], 1) if isinstance(snap.get("age_seconds"), (int, float)) else None),
        }
    return redacted


# --------------------------------------------------------------------------- #
# eligibility
# --------------------------------------------------------------------------- #
def meets_floor(remaining_pct: float | None, threshold: float) -> bool:
    """The literal AOA-11 boundary rule.

    Refuse when remaining is <threshold. remaining == threshold PASSES.
    9.99 -> False, 10.0 -> True, 10.01 -> True (threshold=10.0).
    """
    if remaining_pct is None:
        return False
    return float(remaining_pct) >= float(threshold)


def projected_remaining(remaining_pct: float | None, complexity: str, lb_cfg: dict | None = None) -> float | None:
    if remaining_pct is None:
        return None
    reserves = (lb_cfg or {}).get("reserve_pct_by_complexity") or RESERVE_PCT_BY_COMPLEXITY
    reserve = reserves.get(complexity, 1.0)
    return float(remaining_pct) - float(reserve)


def pool_for(engine: str, model: str | None) -> str | None:
    """Map an (engine, exact model) pick to the quota pool it debits."""
    if engine == "codex":
        return POOL_CODEX
    if engine == "claude":
        return POOL_CLAUDE
    if engine == "agy":
        family = ptme.model_family(model)
        if family == "gemini":
            return POOL_AGY_GEMINI
        if family in ("claude", "gpt-oss"):
            return POOL_AGY_CLAUDE_GPT
        return None
    return None


# --------------------------------------------------------------------------- #
# candidate ordering — reuses router.py's capability+load scoring and rate-
# wall check. Deliberately does NOT call router.route() end-to-end: router's
# "all candidates walled -> fail open to the most-capable one anyway" branch
# is the opposite of AOA-11's fail-closed contract, so this module reproduces
# only the ranking of AVAILABLE engines and treats "none available" as an
# immediate refusal signal instead.
# --------------------------------------------------------------------------- #
def _ordered_candidates(
    task_text: str, role: str | None, exclude_engines: set[str]
) -> tuple[list[str], list[dict]]:
    candidates = [e for e in router.VALID_ENGINES if e not in exclude_engines]
    excluded: list[dict] = []
    available: list[str] = []
    for engine in candidates:
        ok, reason = router.engine_available(engine)
        if ok:
            available.append(engine)
        else:
            excluded.append({"engine": engine, "reason": reason, "reason_code": "rate_walled"})
    if not available:
        return [], excluded
    scored = sorted(
        (router._score_engine(task_text, e, router.RouterConfig(), role=role) for e in available),
        key=lambda s: (s["final_score"], s["capability_score"]),
        reverse=True,
    )
    return [s["engine"] for s in scored], excluded


def _recent_pair_counts(worker_engine: str | None, lookback: int, path: Path) -> dict[str, int]:
    """Least-used worker->QA engine pair, from this module's own log lines."""
    counts: dict[str, int] = {}
    if not worker_engine or not path.exists():
        return counts
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(lookback, 0):]
    except OSError:
        return counts
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("decision_kind") != "load_balancer" or rec.get("status") == "refused":
            continue
        if rec.get("worker_engine_ref") != worker_engine:
            continue
        engine = rec.get("engine")
        if engine:
            counts[engine] = counts.get(engine, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# decision
# --------------------------------------------------------------------------- #
@dataclass
class LoadBalancerDecision:
    schema_version: int
    decision_kind: str
    status: str  # "selected" | "refused" | "owner_override_selected"
    task_id: str
    task_type: str
    role: str | None
    complexity: str
    engine: str | None
    model: str | None
    effort: str | None
    debit_pool: str | None
    candidate_order: list[str]
    excluded: list[dict]
    quota_snapshot: dict
    rationale: str
    override: dict | None
    bypassed_gates: list[str]
    worker_engine_ref: str | None
    decision_ts: str

    def to_log_record(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "decision_kind": self.decision_kind,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "role": self.role,
            "complexity": self.complexity,
            "status": self.status,
            "engine": self.engine,
            "model": self.model,
            "effort": self.effort,
            "debit_pool": self.debit_pool,
            "candidate_order": self.candidate_order,
            "excluded": self.excluded,
            "quota_snapshot": self.quota_snapshot,
            "override": self.override,
            "bypassed_gates": self.bypassed_gates,
            "worker_engine_ref": self.worker_engine_ref,
            "rationale": self.rationale,
            "ts": self.decision_ts,
        }


def _acquire_lock_safe(lock_path: Path, timeout: float = sidecar_lock.DEFAULT_LOCK_TIMEOUT_SECONDS) -> bool:
    """sidecar_lock.acquire_lock(), retrying past a benign Windows race.

    os.open(O_CREAT|O_EXCL) can raise PermissionError instead of
    FileExistsError when another thread/process deletes the lock file at the
    exact moment this one tries to create it. That's not a real failure to
    acquire — it's a create/delete race — so retry within the same deadline
    instead of letting the low-level OSError escape the routing decision.
    """
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return False
        try:
            return sidecar_lock.acquire_lock(lock_path, timeout=remaining)
        except PermissionError:
            time.sleep(sidecar_lock.LOCK_POLL_SECONDS)


def _append_locked(path: Path, record: dict) -> None:
    """Append one JSON line. Caller must already hold path's sidecar lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_record_safe(path: Path, record: dict, attempts: int = 20) -> None:
    """sidecar_lock.append_jsonl_record(), retrying past the same benign
    Windows create/delete race handled in _acquire_lock_safe()."""
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            sidecar_lock.append_jsonl_record(path, record)
            return
        except (PermissionError, RuntimeError) as exc:
            last_exc = exc
            time.sleep(sidecar_lock.LOCK_POLL_SECONDS)
    if last_exc:
        raise last_exc


def select_route(
    task_id: str,
    task_text: str,
    role: str | None = None,
    worker_engine: str | None = None,
    override: dict | None = None,
    config: dict | None = None,
    *,
    sessions_root: Path = DEFAULT_CODEX_SESSIONS_ROOT,
    agy_snapshot_path: Path = DEFAULT_AGY_SNAPSHOT,
    decisions_log: Path | None = None,
    learning_log: Path | None = None,
    now: float | None = None,
    log: bool = True,
) -> LoadBalancerDecision:
    """Compute (and, by default, log) one fail-closed AOA-11 routing decision.

    worker_engine: when set, that engine is excluded from candidates before
    any scoring happens (the unbypassable worker != QA gate). Callers doing a
    QA/review dispatch always pass the prior worker's engine here.

    override: optional {"actor": str, "reason": str, "engine": str|None}.
      * engine alone (no reason) only NARROWS candidates to that engine —
        it still has to pass every quota gate normally.
      * a non-empty reason additionally lets the balancer bypass a
        below-floor/stale/unknown quota gate for the (narrowed or top-
        ranked) candidate. It can NEVER bypass rate-wall exclusion or the
        worker != QA hard gate.
    """
    aoa_config = config if config is not None else config_loader.load_aoa_config()
    lb_cfg = _lb_config(aoa_config)
    threshold = float(lb_cfg["min_remaining_pct"])
    allow_unknown = bool(lb_cfg["allow_unknown_quota"])
    decisions_log = decisions_log or DECISIONS_LOG
    learning_log = learning_log or LEARNING_LOG

    complexity = ptme.classify_complexity(task_text)
    resolved_role = role or router.infer_role(task_text)

    exclude_engines: set[str] = set()
    hard_gate_note = None
    if worker_engine:
        exclude_engines.add(worker_engine)
        hard_gate_note = "worker!=QA gate: excluded worker engine {} (unbypassable)".format(worker_engine)

    override_reason = (override or {}).get("reason")
    override_actor = (override or {}).get("actor")
    override_engine = (override or {}).get("engine")
    has_valid_override = bool(override_reason and str(override_reason).strip())

    lock_path = sidecar_lock.lock_path_for(decisions_log)
    acquired = _acquire_lock_safe(lock_path, timeout=30.0)
    if not acquired:
        raise RuntimeError("load_balancer: could not acquire decision log lock on {}".format(decisions_log))
    try:
        quota = collect_quota(lb_cfg, sessions_root=sessions_root, agy_snapshot_path=agy_snapshot_path, now=now)
        order, excluded = _ordered_candidates(task_text, resolved_role, exclude_engines)

        if resolved_role == "qa" and worker_engine and order:
            counts = _recent_pair_counts(worker_engine, int(lb_cfg["history_lookback"]), decisions_log)
            order = sorted(order, key=lambda e: counts.get(e, 0))

        selected: dict | None = None
        checks: list[dict] = []
        for engine in order:
            model, effort = ptme.recommend_for_complexity(complexity, family=engine)
            pool = pool_for(engine, model)
            snap = quota.get(pool) if pool else None
            remaining = snap.get("remaining_pct") if snap else None
            confidence = snap.get("confidence") if snap else "none"
            known = confidence in ("real", "estimated") and remaining is not None
            floor_ok = known and meets_floor(remaining, threshold)
            proj = projected_remaining(remaining, complexity, lb_cfg) if known else None
            proj_ok = known and proj is not None and meets_floor(proj, threshold)
            eligible = known and floor_ok and proj_ok

            if not known:
                reason = "unknown_quota" if not allow_unknown else "unknown_quota_allowed_by_config"
                if allow_unknown:
                    eligible = True
            elif not floor_ok:
                reason = "below_floor({:.2f}%<{:.2f}%)".format(remaining, threshold)
            elif not proj_ok:
                reason = "projected_below_floor({:.2f}%<{:.2f}%)".format(proj, threshold)
            else:
                reason = "eligible"

            checks.append({
                "engine": engine, "model": model, "effort": effort, "pool": pool,
                "remaining_pct": remaining, "confidence": confidence, "eligible": eligible, "reason": reason,
            })
            if eligible and selected is None and (not override_engine or override_engine == engine):
                selected = {"engine": engine, "model": model, "effort": effort, "pool": pool}
                break

        status = "selected"
        bypassed_gates: list[str] = []
        if selected is None and has_valid_override:
            target_order = [override_engine] if override_engine else order
            for engine in target_order:
                if engine not in order:
                    continue  # never bypasses rate-wall exclusion or the worker!=QA gate
                model, effort = ptme.recommend_for_complexity(complexity, family=engine)
                pool = pool_for(engine, model)
                selected = {"engine": engine, "model": model, "effort": effort, "pool": pool}
                bypassed_gates.append(
                    "quota_floor_or_unknown (actor={}, reason={!r})".format(override_actor, override_reason)
                )
                status = "owner_override_selected"
                break

        if selected is None:
            status = "refused"

        for item in checks:
            if selected and item["engine"] == selected["engine"] and status != "refused":
                continue
            excluded.append({
                "engine": item["engine"], "reason": item["reason"], "reason_code": "quota",
                "pool": item["pool"], "remaining_pct": item["remaining_pct"],
            })

        rationale_bits = ["role={} complexity={}".format(resolved_role, complexity)]
        if hard_gate_note:
            rationale_bits.append(hard_gate_note)
        if not order:
            rationale_bits.append("all candidate engines rate-walled or hard-gated")
        if status == "refused":
            rationale_bits.append(
                "no candidate engine has an eligible pool at/above {:.1f}% remaining — refusing (fail-closed)".format(
                    threshold
                )
            )
        elif status == "owner_override_selected":
            rationale_bits.append("owner override bypassed: {}".format("; ".join(bypassed_gates)))
        else:
            pct = quota.get(selected["pool"], {}).get("remaining_pct")
            rationale_bits.append(
                "selected {} (pool {}, {} remaining)".format(
                    selected["engine"], selected["pool"], "{:.2f}%".format(pct) if pct is not None else "unknown"
                )
            )

        decision = LoadBalancerDecision(
            schema_version=1,
            decision_kind="load_balancer",
            status=status,
            task_id=task_id,
            task_type=resolved_role,
            role=resolved_role,
            complexity=complexity,
            engine=(selected or {}).get("engine"),
            model=(selected or {}).get("model"),
            effort=(selected or {}).get("effort"),
            debit_pool=(selected or {}).get("pool"),
            candidate_order=order,
            excluded=excluded,
            quota_snapshot=_redact_quota(quota),
            rationale=". ".join(rationale_bits),
            override=(
                {"actor": override_actor, "reason": override_reason, "engine": override_engine} if override else None
            ),
            bypassed_gates=bypassed_gates,
            worker_engine_ref=worker_engine,
            decision_ts=ptme.now_iso(),
        )
        if log:
            _append_locked(decisions_log, decision.to_log_record())
    finally:
        sidecar_lock.release_lock(lock_path)

    if log:
        # Different file/lock — safe to append after releasing decisions_log's lock.
        learning_record = {
            "task_id": decision.task_id,
            "engine": decision.engine,
            "role": decision.role,
            "complexity": decision.complexity,
            "signals": [],
            "qa_verdict": None,
            "success": (False if decision.status == "refused" else None),
            "planned_tokens": (
                ptme.estimate_planned_tokens(decision.complexity, decision.engine) if decision.engine else None
            ),
            "actual_tokens": None,
            "token_delta": None,
            "planned_duration_ms": None,
            "actual_duration_ms": None,
            "duration_delta_ms": None,
            "load_balancer_status": decision.status,
            "load_balancer_debit_pool": decision.debit_pool,
            "ts": decision.decision_ts,
        }
        _append_jsonl_record_safe(learning_log, learning_record)
    return decision


def build_dispatch_request(
    decision: LoadBalancerDecision,
    prompt: str,
    workdir: Path,
    timeout: int,
    *,
    enable_mcp: bool = False,
    executable: str | None = None,
):
    """Turn a "selected"/"owner_override_selected" decision into a DispatchRequest.

    Raises LoadBalancerRefusal if the decision has no route — callers must
    never fall back to a manual engine choice on refusal.
    """
    from adapters.base import DispatchRequest  # local import: avoids a hard dep for pure routing callers

    if decision.status == "refused" or not decision.engine:
        raise LoadBalancerRefusal(decision)
    return DispatchRequest(
        engine=decision.engine,
        prompt=prompt,
        workdir=Path(workdir),
        timeout=timeout,
        model=decision.model,
        effort=decision.effort,
        enable_mcp=enable_mcp,
        executable=executable,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_route(args: argparse.Namespace) -> int:
    override = None
    if args.override_reason:
        override = {"actor": args.override_actor, "reason": args.override_reason, "engine": args.override_engine}
    decision = select_route(
        task_id=args.task_id,
        task_text=args.text,
        role=args.role,
        worker_engine=args.worker_engine,
        override=override,
        log=not args.dry_run,
    )
    print(json.dumps(decision.to_log_record(), indent=2, ensure_ascii=False))
    return 0 if decision.status != "refused" else 1


def cmd_quota(_: argparse.Namespace) -> int:
    print(json.dumps(_redact_quota(collect_quota()), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOA-11 fail-closed per-pool quota load balancer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_route = sub.add_parser("route", help="Compute (and log) one routing decision")
    p_route.add_argument("--task-id", required=True)
    p_route.add_argument("--text", required=True)
    p_route.add_argument("--role")
    p_route.add_argument("--worker-engine", help="Exclude this engine (QA gate)")
    p_route.add_argument("--override-actor")
    p_route.add_argument("--override-reason")
    p_route.add_argument("--override-engine")
    p_route.add_argument("--dry-run", action="store_true", help="Do not append to the decision/learning logs")
    p_route.set_defaults(func=cmd_route)

    p_quota = sub.add_parser("quota", help="Print the current redacted per-pool quota snapshot")
    p_quota.set_defaults(func=cmd_quota)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
