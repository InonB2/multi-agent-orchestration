# Private dependency boundary

This repository may receive fixes from a deployment that also has private
operational modules. A public module must not import those modules, copy their
deployment policy, or silently stop receiving generic fixes.

## Boundary pattern

Every private-to-public sync classifies each dependency before code is copied:

1. **Generalize** a dependency when its behavior is portable and its contract
   is useful to the framework. Put the implementation in a public shared
   module and test the contract here. Prefer an existing public primitive over
   a second implementation. Deployment policy must not travel with the
   primitive.
2. **Stub/interface** a dependency when the public code needs the capability
   but cannot own the deployment implementation. Define the smallest typed
   contract in public code, provide a deterministic no-op or repository-local
   default, and inject a deployment adapter. Public defaults must preserve
   current behavior.
3. **Leave split** when the behavior operates a particular machine, account,
   roster, or release layout rather than the framework. Record the split here
   as permanent. Generic fixes in either version must still be reviewed and
   cherry-picked deliberately.

Private imports must never be hidden behind a broad `try/except ImportError` in
public code. Optional behavior belongs behind a public contract. Private
adapters may import public contracts; public modules may not import private
adapters. Public contracts use framework concepts only and cannot contain
private paths, identities, credentials, hostnames, project names, or quota
policy.

For every future sync, update the table in the same change when the boundary
decision changes. A split row is a decision, not permission to ignore diffs:
review the diff for portable bug fixes, tests, and contract changes each time.

## File decisions

| File | Strategy | Public boundary and rationale |
| --- | --- | --- |
| `scripts/task_router.py` | **1 — Generalize** | The private lock calls are ordinary sidecar locking, already implemented more robustly by public `scripts/sidecar_lock.py`; use that canonical primitive rather than porting the policy-bearing parts of private `tasks_io.py`. The capability classifier is pure task-text classification and is portable, so it is included and tested. |
| `scripts/coordinator.py` | **1 — Generalize** | Replace the duplicated lock pair with `sidecar_lock`. Private reviewer-lineage, pairing, and session policy is deployment policy and is deliberately excluded; generic coordinator changes still require diff review. |
| `scripts/checkpoint.py` | **1 — Generalize** | Replace its duplicated queue lock pair with `sidecar_lock`. Queue semantics and paths remain public-repository behavior. |
| `scripts/config_loader.py` | **3 — Leave split** | The public file owns the portable `aoa.config.json` schema, environment overrides, model catalog, and CLI. The private file currently has a smaller deployment-specific configuration surface. Replacing either wholesale would remove supported public configuration or impose an unversioned private shape. |
| `scripts/load_balancer.py` | **3 — Leave split** | The private implementation is coupled to deployment pool clocks, catalog policy, quota refresh, and mutable-runtime layout. The public implementation owns its documented fail-closed AOA routing contract and tests. The algorithms may share fixes, but their policy and state models are intentionally separate. |
| `scripts/router.py` | **2 — Stub/interface** | Public routing accepts a minimal `PoolTelemetry` snapshot contract with an empty default. A deployment can inject an adapter for its pool monitor; the router consumes only normalized pool state and never imports private pool modules or policy. |
| `scripts/dispatch_worker.py` | **2 — Stub/interface** | Mutable output paths resolve through a public `runtime_paths` shim. Its default remains the repository root for backward compatibility; deployments can set `AOA_RUNTIME_DIR` to keep mutable state outside an installed release. |
| `scripts/session.py` | **3 — Leave split** | Private session initialization invokes machine-scheduled quota, reset-watchdog, and local transcript jobs. Those jobs are operational wiring, not a portable framework session contract. Public session initialization keeps only repository-owned telemetry refresh. |
| `scripts/agent_telemetry.py` | **3 — Leave split** | Private PID monitors, detached monitor spawning, automatic expiry, and sweep commands encode the lifecycle of local dispatch wrappers. Public telemetry remains the simpler explicit start/stop/status API. |

## Shared-lock decision

Only `acquire_lock` and `release_lock` from private `tasks_io.py` are generic.
The public repository already has the stronger `sidecar_lock` implementation,
including stale-lock metadata and tested cross-process helpers. The private
status enums, transition policy, process exits, and fixed task paths are not a
generic lock dependency, so `tasks_io.py` is not copied as a second source of
truth. The three consumers above use `sidecar_lock` directly.

## Adapter contracts

`PoolTelemetry.snapshot()` returns normalized `PoolState` values. An empty
snapshot means "no additional pool signal" and preserves watchdog/dashboard
behavior. A pool can mark an engine unusable or provide `remaining_pct`; the
router treats this as availability/load input, not as authority to select a
model outside the existing PTME rules.

`runtime_paths.runtime_path(*parts)` resolves below `AOA_RUNTIME_DIR` when set
and below the repository root otherwise. Deployments override the environment;
they do not patch public constants or add private path names to this repository.

## Open questions

- If the private deployment should adopt the public configuration schema, it
  needs a versioned migration and representative sanitized fixtures. Until
  those exist, `config_loader.py` remains split.
- A future shared load-balancing policy requires a public, versioned quota
  snapshot schema (including staleness and unknown-quota semantics). The small
  router telemetry contract is not that policy and should not grow into it by
  accident.
