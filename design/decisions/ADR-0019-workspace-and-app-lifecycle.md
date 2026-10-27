# ADR-0019: App and workspace lifecycle: assess, plan, never write newer

This record decides how the app boots, opens workspaces, sets up new ones and migrates their formats.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** lifecycle, migrations

## Context

v0.3 had no model of the install: seven mechanisms derived the same facts, the setup wizard could overwrite an existing ledger, boot silently created an empty ledger when the configured file was missing, and lifecycle backups were best-effort (review:15).

## Decision

(owner, 2026-10-24)
- **Two pure assessments.** `assess_app` runs eagerly at boot: `RECOVERING`, `APP_CONFIG_INVALID`, `NEWER_APP_STATE`, `APP_UPGRADE_PENDING`, `NO_WORKSPACE`, `APP_READY`. `assess_workspace` runs lazily on open: `RECOVERING`, `WS_CONFIG_INVALID`, `LEDGER_UNAVAILABLE`, `NEWER_WORKSPACE`, `UPGRADE_PENDING`, `READY` (sub-state `WARMING`).
- `GET /api/boot` answers before the app is ready; the API answers "not ready" until it is. `NO_WORKSPACE` shows the Welcome screen (Create, Open, Import, Try the demo); a failed last workspace shows Welcome with a banner. **Never an auto-created ledger.** A missing ledger is `LEDGER_UNAVAILABLE` with Retry / Locate / Stay / Forget (INV-29).
- **Setup is a plan:** `plan_create_workspace`, `plan_adopt_workspace`, `plan_import_workspace` are pure, show every file to be written, never overwrite, apply with a backup, then open through the normal path. There is no hot-reload and no wizard-only service state.
- **No backup, no write** for every lifecycle step.
- **One migration framework** for every user-owned artifact (recipes, rules, config, ledger-metadata moves): declarative, idempotent, dry-runnable, backed up, with per-artifact version stamps. Lossless steps run automatically with an "Upgraded — undo" notice; semantic or ledger-writing steps need consent. Runs use apply-then-record (ADR-0012).
- **App-level artifacts migrate eagerly at boot; workspace artifacts migrate lazily**, only on the user's next save or on consent, never on open (INV-30).
- **Never write a format newer than this app understands, per workspace** (INV-17). A workspace or app state from a newer app is refused with "restore backup of run X". A newer-format dashboard or rule file is read-only. A step that raises `min_reader` is gate-class (consent).
- **Content packs** (widget library, seed dashboards, rule templates, themes, demo) are versioned and read-only. A file whose bytes equal any shipped version counts as pristine.
- Hosted provisioning is not built (ADR-0041).

## Alternatives considered and rejected

- **Services built before the wizard and hot-reloaded into its result** (legacy). Rejected: stale values and divergent paths.
- **Eager migrations on open.** Rejected: synced folders on two machines would get conflicted copies; unreachable workspaces; slower boots.
- **Downgrade support or read-only mode for newer workspaces.** Rejected as standard: refuse and offer restore (read-only only if it turns out cheap).

## Consequences

- Boot, workspace open, lifecycle run and sidecar are statecharts with exhaustive tests (ARCHITECTURE §7).
- The version-independent restore format (ADR-0012) lets any later version restore an earlier run.

## Amendment (2026-10-27): the territory phases; the switch (ADR-0063)

Owner: "1. Yes to forbidding two workspaces sharing a file. If it'll help we can simplify further: No sharing anywhere, no multiple instances of Finzytrack anywhere. Period. Finzytrack is just about one instance, working with one workspace at a time." (2026-10-27, in session; the full quote is in ADR-0063). Details marked "(agent)" are ADR-0063's.
- `assess_workspace` gains two phases (ADR-0017 amendment, INV-32): **`LEDGER_OUTSIDE_FOLDER {path}`** (a file of the ledger set, or one a save may create, lies outside the workspace's territory) and **`FOLDER_IN_OTHER_WORKSPACE {path, other_workspace}`** (its territory is equal to, overlaps or nests another registered workspace's). Both are refused like the other failed phases, record nothing (INV-29) and offer Stay. `plan_create_workspace`, `plan_adopt_workspace` and the test copy refuse with the same codes (agent: the names).
- The outside-folder check runs after recovery on open (ADR-0012: recovery "before any other step"), on the recovered files, and again at each rebuild of the open workspace after an outside edit: a rebuild that finds a file outside the territory is not published, and saves are refused until it is fixed (agent).
- Opening a workspace while another is open is a switch (ADR-0018 amendment, ADR-0063 decision 1): assess the target, quiesce and close the current one, then the normal open path (recovery first when a journal is pending, then the assessment). A failed open after the close offers Back to the previous workspace.
- **Unchanged:** the rest of this ADR.

## References

review:15 §1–§4; review:23 §4.1–§4.3; review:25 §7, §9.7; ADR-0012, ADR-0017, ADR-0022, ADR-0041; INV-16, INV-17, INV-29, INV-30; CAP-DATA-BOOT, CAP-DATA-NEWER-REFUSE, CAP-WS-CREATE, CAP-WS-UNAVAILABLE; ADR-0063.
