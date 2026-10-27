# ADR-0017: The folder is the workspace; state placement

This record generalizes "the ledger" into portable workspace folders and decides where every kind of state lives.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** workspaces, state placement

## Context

The owner wants to switch ledgers seamlessly, and wants a set of books to be a self-contained folder: tar it on desktop, import it into a future hosted version, and it works, and the reverse. The user's data is never held hostage. The state-placement rule (facts in the ledger; tool config apart; caches rebuildable) was decided earlier for every lens.

## Decision

(owner, 2026-10-24)
- **A workspace is a folder with a `.finzytrack/` subfolder** (INV-27). It holds:
  - the **ledger set**: root, glob-expanded includes, the price sidecar, the documents folder (new and copied workspaces write `option "documents" "documents"`, relative);
  - **tool state** in `.finzytrack/`: a slim `workspace.json` manifest, `settings.yaml` (workspace settings and policy), `dashboards/`, `rules/`, `themes/`. Each artifact file is self-describing (`format`, `origin`).
- **Machine-local state never travels:** mirror generations and model caches (cache dir); backups, import sessions, lifecycle and write journals, per-machine fields, trust answers (`local/<key>/`); locks; logs; AI chat history (in memory); UI memory (scoped localStorage). It is keyed by the **registry key**.
- **Identity has two parts:** the lineage id in the manifest (travels, never rewritten automatically) and the machine-local registry key. A copy is detected and the user chooses "same or copy"; only "copy" writes a new lineage id.
- **No absolute paths and no secret values in the folder.** Secret *names* are declared by the rules that need them (ADR-0021). Workspace settings have no path-typed fields; `ledger_root` is relative and non-escaping.
- **External-ledger reference** instead of "local placement": when the user keeps an existing ledger folder untouched, the app creates a workspace folder whose manifest says `ledger: external`; the absolute path lives only in the machine-local registry row. Such a workspace is badged *not portable* until "Bring the ledger into this folder".
- **`assess_portability(ws, desktop|hosted)`** is pure and returns "Portable" or a per-file list of issues. With the hosted profile it is the hosted import validator. Desktop and hosted share one `finzytrack-workspace` format package.
- **A ledger root belongs to at most one workspace per machine** (registry deduplicated by canonical root path).
- **State placement (INV-04 as amended):** facts in the ledger; tool config and workspace policy in `.finzytrack/`; app-level settings in `app/`; caches rebuildable, in the cache dir; working state in machine-local dirs; UI memory in scoped localStorage. No new out-of-ledger state without a strong argument.

## Alternatives considered and rejected

- **One shared `config/` + `data/` for all ledgers** (legacy). Rejected: rules and dashboards of different books mix, and nothing is portable.
- **A separate "local placement" layout.** Rejected: two layouts, two code paths.
- **Backups, mirror or sessions inside the folder.** Rejected: sync clients and git would carry or clobber them, and SQLite WAL corrupts in synced folders.
- **An artifacts index or `last_opened_*` fields in the manifest.** Rejected: churn on every open or save causes sync conflicts.

## Consequences

- A raw tar or sync of the folder is supported; a validated Export/Import adds a file list and hashes. (The Export/Import *archive* capability is deferred past v0.4.0, ADR-0043; the raw folder still travels.)
- Opening a workspace never writes the folder (INV-30).

## Amendment (2026-10-27): no file in two workspaces (ADR-0063)

Owner: "1. Yes to forbidding two workspaces sharing a file. If it'll help we can simplify further: No sharing anywhere, no multiple instances of Finzytrack anywhere. Period. Finzytrack is just about one instance, working with one workspace at a time." (2026-10-27, in session; the full quote is in ADR-0063). Details marked "(agent)" are ADR-0063's.
- **Superseded:** "a ledger root belongs to at most one workspace per machine (registry deduplicated by canonical root path)". **Instead:** no file of a ledger set belongs to two registered workspaces. Two sets overlap when a file is in both, a file of one lies in a documents folder of the other, their documents folders are the same or nested, or a glob include of one matches a file of the other; "the same file" is the write path's canonical identity (`path_key` and `canonical_path`, defined in the engine-scheduler spec), plus device and inode for files that exist (agent). Registering, adopting, creating or opening an overlapping workspace is refused with the shared file named (ADR-0019 amendment).
- **The external-ledger reference stays**, bound by that rule: one external ledger cannot be referenced by two workspaces, nor by a reference and an in-place workspace.
- Each registry row keeps a machine-local record of its workspace's last known set, which the check compares against (agent; INV-04: working state, never in the folder).
- **Unchanged:** the rest of this ADR.

## References

review:23 §0, §1.1–§1.5, §7.1–§7.2; review:12 §3 (state placement); ADR-0018, ADR-0019, ADR-0021, ADR-0043; INV-04, INV-27, INV-30; CAP-WS-PORTABLE, CAP-WS-PORTABILITY-CHECK, CAP-WS-BRING-IN; ADR-0063.
