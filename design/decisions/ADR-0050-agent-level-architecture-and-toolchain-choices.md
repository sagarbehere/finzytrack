# ADR-0050: Agent-level architecture and toolchain choices

This record collects the choices the architecture makes within the decided stack, each reversible by a later ADR.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** agent
- **Area:** architecture, stack, verification

## Context

The decided designs leave some structure, library and tooling choices open. None of these hits the escalation rubric. The framework itself is an owner decision (ADR-0006).

## Decision

1. **Vertical slices with a per-slice core/shell** (`core/`, `usecases.py`, `schemas.py`, `http.py`, `capabilities.py`, `registry.py`) over a shared kernel (`domain`, `platform`, `ledger`, `mirror`, `workspaces`), instead of horizontal `usecases/` + `domain/` trees.
2. **`integrations/` dissolved** into `platform/adapters` (I/O) plus pure registries in the slices; price sources build requests as data.
3. **`app/boot`** is the package name for lifecycle.
4. **pyright strict is the single Python type gate** (it checks `LiteralString` and is fast enough for T0); mypy is not run.
5. **openapi-typescript + openapi-fetch**, generated query keys from `x-ft-scope`, offline OpenAPI export (ADR-0038).
6. **pnpm** instead of npm (strict `node_modules`, `minimumReleaseAge`).
7. **One `PlanStore` and one confirm endpoint** shared by UI previews and AI proposals.
8. **`contracts/` at the repo root**, with `recipe.schema.json` moved there as the single source.
9. **TypeScript 6.0.3** (TypeScript 7 is blocked by typescript-eslint's peer range).
10. **react-router in declarative mode** (SPA; TanStack Query owns server state).
11. **A thin own `useECharts` hook** over `echarts` instead of a wrapper library.
12. **Dependency policy (EP-24):** exact pins in lockfiles; a release is adopted only when at least 14 days old (`uv` `exclude-newer = "14 days"`, pnpm `minimumReleaseAge: 20160`); each upgrade is its own slice with the full T1 suite.
13. **Executable models of the write core:** a Hypothesis `RuleBasedStateMachine` with crash injection, and a TLA+ spec (`formal/tla/WriteProtocol.tla`) checked by TLC (tools 1.7.4, Java 21, pinned by checksum), with `@tla_action` markers mapped both ways by a T1 test. Frontend workflows are XState v5 machines with model-based path coverage; backend workflows are pure transition tables with exhaustive tests.
14. **structlog 26.1.0 on stdlib logging** (ADR-0039).
15. **uuid7 ids** via `uuid-utils` for generated ids (plans, runs); transaction ids are derived, not generated (ADR-0026).

## Alternatives considered and rejected

- **mypy alongside pyright.** Rejected: two type dialects, no added protection.
- **`@hey-api/openapi-ts`.** Rejected (ADR-0038).
- **A React ECharts wrapper library.** Rejected: wrappers lag React releases; init, `setOption`, resize and dispose fit one small hook.
- **Framework or data-loader routing modes.** Rejected: TanStack Query owns server state.
- **npm.** Rejected: phantom dependencies weaken the boundary rules.

## Consequences

- The exact pins are listed in `design/ARCHITECTURE.md` §10 and enforced by the lockfiles.

## References

review:25 §2.1, §7, §8, §10, §13; review:26 EP-17–EP-19, EP-24; ADR-0006, ADR-0026, ADR-0038, ADR-0039.
