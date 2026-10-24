# ADR-0039: Errors, logging and diagnostics

This record decides the error taxonomy, how errors reach users, and how the app logs safely.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** agent (within the decided stack), following owner-approved positions and owner rules on wording
- **Area:** backend, frontend, observability, privacy

## Context

v0.3 had three backend error channels (HTTP errors raised from core, string-matched messages, catch-all 500s) and seven frontend patterns. Logs were cwd-relative text with no correlation ids; some info lines contained financial data; frontend errors never left the console.

## Decision

- **Taxonomy** in `domain.errors`, codes in one registry: `Invalid` (422, located issues), `NotFound` (404), `Conflict` (409), `ContractRefused` (409 needs acknowledgement / 422 refused), `Forbidden` (403), `Unavailable` (503, retryable), `Internal` (500). Each code declares its class, status, retryable flag, presentation, a plain-language message, the data-safety statement and the next step, and a typed `details` model.
- **Expected outcomes are tagged unions**, not errors.
- **Translation once, at the edge:** no module below `api` knows an HTTP status. **No string matching on messages anywhere.**
- **No swallowing:** `except Exception` only in the API handler, the job runner and the sidecar's top loop. The write contract never fails open (INV-22).
- **One frontend path:** every failure becomes one typed `AppError`; one `presentError` maps code to presentation: toast-class errors auto-dismiss and stay in history, modal-class errors are sticky, located issues go to form fields, retryable errors offer Retry.
- **Messages** say what happened, whether the data is safe, and what to do next, in plain English for non-native readers; "Your ledger is unchanged" only when it is true.
- **Logging:** one structured JSON-lines log per installation at `<app_local_data_dir>/logs/finzytrack.jsonl` (never in a workspace folder, never cwd-relative), using **structlog on stdlib logging**; the shell logs to its own `shell.jsonl` with the same schema. Every line carries session id, correlation id, workspace key, source, feature and app version. Size-based rotation sized per deployment.
- **The frontend logs into the same stream** through `platform/logger.ts` (the only `console.*` user) and a client-log endpoint; global handlers route uncaught errors to the log and to the user.
- **Privacy by construction:** events are declared in a registry with typed field kinds; the redaction processor is **default-deny** (money, plain strings, untrusted text, secrets and outside paths are redacted). Debug mode and AI diagnostics are opt-in and time-limited.
- **"Report a problem"** builds a bundle plan (logs, versions, phases, workspace statistics, never ledger content), scrubs free text, shows exactly what is inside, and saves one zip.
- **No telemetry and no crash-reporting service** (ADR-0047).

## Alternatives considered and rejected

- **A plain stdlib JSON formatter.** Rejected: no context binding or processor pipeline.
- **Tauri's log plugin for frontend events.** Rejected: a second file outside the typed redaction, absent in browser mode. Kept only for the shell's own lines.
- **"Remember not to log X" rules.** Rejected: redaction is the mechanism.

## Consequences

- Every registry code has a test that triggers it through the API; unused codes fail T1.
- A redaction corpus test emits every event with hostile values.

## References

review:25 §6, §6A; review:10 D11; ADR-0008, ADR-0047; INV-22, INV-23; EP-13, EP-15, EP-26, EP-27; CAP-DATA-LOGS.
