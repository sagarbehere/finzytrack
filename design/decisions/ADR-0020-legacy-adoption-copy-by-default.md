# ADR-0020: Legacy adoption: Copy by default, Share gated; the legacy install keeps working

This record decides how v0.4 brings in a v0.2.x or v0.3 install, and how it coexists with a legacy app on a shared ledger.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** workspaces, lifecycle, data safety

## Context

Users jump from v0.2.x to v0.4.0, and the owner keeps running the old app alongside v0.4 for a while. The legacy app re-renders whole files on every save: it drops comments, bakes in lots, copies glob-included entries into the root (double counting) and persists plugin entries. It has no external-edit check.

## Decision

(owner, 2026-10-24)
- **v0.4 has its own app root** (its Tauri identifier dirs, ADR-0007) and treats a legacy `config/` + `data/` root as **input it reads but does not change** (INV-26). The legacy Windows root is never written.
- **Detection** probes the known roots (packaged location, a source checkout's `backend/`) **and** any folder the user picks with "Choose your old Finzytrack data folder", so a v0.3 install at a custom location is found. The probe is read-only. **Every open** of a path also runs the legacy-root probe (review:23 §6 step 0): a path under a known legacy root, or one a legacy `config.yaml` points to, never gets an in-place `.finzytrack/`; the user is offered Copy (or gated Share), and the legacy guard attaches to whichever path is taken.
- **Plan (pure, shown in full, gate-class):** the legacy active ledger becomes workspace 1; other root ledgers are offered as checkboxes, off by default; the old demo ledger is never adopted. For each ledger one choice:
  - **Copy (default):** a new portable workspace folder; the ledger set is copied (root, includes, sidecar, documents into `<folder>/documents/`). Copy reads the sources under every legacy lock and checks that the copy loads the same as the source. Refused with a reason when an include resolves outside the root's folder.
  - **Share (advanced):** a workspace with an external-ledger reference to the legacy ledger; both apps write the same file. **Refused or gated with an explicit warning** for ledgers with a glob include or an entry-synthesizing plugin.
- **Tool state is always copied** into each workspace's `.finzytrack/`: rules (with the secret names they use), dashboards (read at their old format, upgraded lazily), themes, workspace settings. App-level keys go to `app/settings.yaml`. Legacy secrets are split per workspace; the AI key goes to `app/secrets/app.env`. Legacy OFX mappings are input for a later, consented ledger-metadata step (ADR-0027).
- **No legacy ledger file is written by adoption.** Re-importing settings later is deferred (CAP-LEGACY-REIMPORT).
- **Coexistence guard on Share workspaces:** take every recorded legacy `data/.write.lock` with the legacy primitive (ADR-0005), timeout ≥ 30 s with a status, then v0.4's own per-file locks; run the full extended fingerprint under the locks; detect a running legacy app on the same ledger and show a banner (a notice, not read-only mode); after an external rewrite, compare entry multisets and report double counting or plugin-shaped entries with the path to the legacy app's own backup, **never repair**.
- Entries v0.4 creates have no legacy `id`, so the legacy app cannot edit them.
- **Downgrade note** in the release notes and user docs: going back is safe only for single-file ledgers without plugins, and formatting is lost.

## Alternatives considered and rejected

- **In-place migration of the legacy root.** Rejected: the old app must keep working (INV-26).
- **Share by default.** Rejected: a legacy save double-counts glob ledgers and accumulates plugin entries, and same-transaction edits are lost.
- **Read-only mode while a legacy app runs.** Rejected: the damage happens when the old app writes, whether or not v0.4 runs.

## Consequences

- An adoption corpus test checks that `config/`, `data/ledger.db*`, backups, sessions and non-shared ledgers are byte-identical after adoption and a v0.4 session; a lock-interleaving test runs against the packaged v0.2.2 build on three OSes.
- A copied workspace is portable, so it can later move to hosted mode by Export/Import.

## References

review:23 §4.5, §4.6, §7.3 Q5; review:21 §3 step 7; review:F steering 3; ADR-0005, ADR-0007, ADR-0017, ADR-0027; INV-26; CAP-LEGACY-ADOPT, CAP-LEGACY-COEXIST, CAP-LEGACY-REIMPORT.
