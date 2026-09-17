#!/usr/bin/env python3
"""Public contract for optional deployment-provided quota-pool telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class PoolState:
    """Normalized state for one engine quota pool."""

    id: str
    engine: str
    usable: bool = True
    remaining_pct: float | None = None
    status_reason: str = ""


class PoolTelemetry(Protocol):
    """Interface implemented by deployment adapters."""

    def snapshot(self) -> Sequence[PoolState]:
        """Return a point-in-time normalized pool snapshot."""


class NullPoolTelemetry:
    """Default provider: no additional availability or quota signal."""

    def snapshot(self) -> Sequence[PoolState]:
        return ()


NULL_POOL_TELEMETRY = NullPoolTelemetry()
