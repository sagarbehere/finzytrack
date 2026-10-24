# ADR-0038: API contract: one envelope, plans and confirm, offline OpenAPI, openapi-typescript

This record decides the API's shape and how the frontend client is generated from it.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** agent (within the decided stack), following owner-approved positions
- **Area:** API, codegen

## Context

v0.3 generated its client with `openapi-typescript-codegen`, which is unmaintained, only after the developer restarted the backend; streaming endpoints were hand-written; recipe endpoints returned raw JSON outside the envelope. Domain types were lost at the wire.

## Decision

- **Schemas are the single source** (EP-16). `backend/scripts/export_openapi.py` imports the app with a no-I/O container and writes `contracts/openapi.json` **offline**; no server is started. The pre-commit hook re-exports and regenerates when a schema, router or error file is staged; CI regenerates from a clean checkout and fails on a diff. The old "restart the backend, then regenerate" rule is retired.
- **`openapi-typescript` + `openapi-fetch`** (types plus a small runtime with `AbortSignal` and middleware). Hand-written, thin TanStack Query hooks per feature use keys from a generated `keys.gen.ts`.
- **Vendor extensions** on every operation: `x-ft-scope: app | config | ledger` (route-scope test, key factory) and `x-ft-writes: ledger | config | none` (invalidation).
- **Domain types survive codegen:** schema annotations (`x-ft-type: Money`, `AccountName`, `CivilDate`, `WorkspaceKey`, `TransactionId`, `UntrustedText`, …) map to TS brands; a generated `decode.gen.ts` lifts wire values to domain values at the edge (Money grammar check, `Temporal.PlainDate`).
- **One envelope for every endpoint:** `{data, meta: {request_id, workspace_id, ledger_version, config_version}}` or `{error: {code, class, message, data_safe, next_step, issues, retryable, retry_after_ms, details, correlation_id}, meta}`. Recipe documents travel as `data` with unknown properties preserved.
- **Writes follow plans as data:** a mutating capability exposes `…:preview` returning a `WritePlan {plan_id, preview_hash, diff, new_error_keys, warnings}`, and one shared `POST /api/w/{ws}/plans/{plan_id}:confirm {preview_hash, acknowledged_error_keys}`. A non-destructive insert with nothing to acknowledge may combine both. AI proposals use the same store and endpoint.
- **Lists are cursor-paginated and filterable.**
- **Streams:** chat speaks the AI SDK UI message stream v7 (ADR-0034); other streams (email fetch, rebuild progress) are typed SSE event unions exported into the spec.
- A breaking-change check (`oasdiff`) against the previous release's spec fails on an unlabelled break.

## Alternatives considered and rejected

- **`openapi-typescript-codegen`.** Rejected: unmaintained.
- **`@hey-api/openapi-ts`.** Rejected: its generated query hooks build their own keys (no prefix invalidation by scope), and it was still at 0.x with frequent breaking minor releases.
- **A raw-JSON exception for recipes.** Rejected: AI capabilities call use cases directly, so the reason is gone.

## Consequences

- A schema change and its client regeneration land in the same commit.
- Acceptance tests still run against a running backend that the agent starts on its own port and data dir (INV-24).

## References

review:25 §4.1–§4.3, §13 items 5, 7; review:10 D9; ADR-0018, ADR-0034, ADR-0037, ADR-0050; INV-19, INV-24, INV-28; EP-14, EP-16, EP-21.
