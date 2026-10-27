# ADR-0021: Workspace policy: AI on by default, scoped secrets, copy-based sharing

This record decides the AI scope per workspace, how secrets are scoped, and how settings are shared between workspaces.

- **Status:** Accepted. Amended by ADR-0063 (2026-10-27): "Copy to workspace…" is carried across a switch.
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** workspaces, AI, secrets

## Context

A user may keep personal and business books in separate workspaces. A business ledger may need to stay away from cloud models. A rule file synced from elsewhere must not be able to pull an unrelated secret into a field such as a host or URL. The user wants the same dashboards in two workspaces but different rules.

## Decision

(owner, 2026-10-24)
- **AI provider is app-level** (one provider, model and key). Each workspace has an **"Allow AI in this workspace"** switch in `.finzytrack/settings.yaml`, so the policy travels with the folder. **Default: on.** This means: AI stays unavailable until the user configures a provider; once one is configured, every workspace may use it unless the user switches it off for that workspace. The default never enables AI by itself. An imported workspace keeps its value.
- **Secrets are scoped to one workspace.** A workspace's rules resolve `${VAR}` only from `app/secrets/<key>.env`. The AI key lives in `app/secrets/app.env` and is not referenceable from rules. Secret **names** are declared in the folder; values never are.
- After a move or import, a **Connect** card lists each missing secret and the rule that uses it. A rule missing a secret is disabled, never run with an empty value.
- Secrets are resolved through an injected per-user resolver, never a raw environment lookup from tenant text. The OS keychain is later hardening (CAP-SEC-KEYCHAIN).
- **Sharing is copy-based in v0.4.0:**
  - "New workspace → start from the settings of another workspace" copies dashboards, rules, themes and settings (not the ledger, not secret values);
  - "Copy to workspace…" on a dashboard, rule or theme keeps its `origin` and runs the target's validator; accounts missing in the target are reported, never "fixed" (INV-15);
  - shipped dashboards and widgets are already shared by reference into the content pack (`pack:core/net-worth@3` style URIs; a later `lib:` source fits without a format change).

## Alternatives considered and rejected

- **A provider per workspace.** Not needed now; it can be added later without breaking anything.
- **A live app-level user library referenced by workspaces.** Deferred (CAP-WS-LIBRARY): a `lib:` reference breaks the tar round trip unless embedded, and editing a shared dashboard for one workspace would silently change the other.
- **Secrets readable across workspaces or from the process environment.** Rejected: leaks through synced rule files.

## Consequences

- Per-workspace secret scoping is a row in the isolation matrix on desktop too.
- Finzytrack AI mode is deferred (ADR-0034, ADR-0043); bring-your-own providers and local models stay.

## Amendment (2026-10-27): "Copy to workspace…" across a switch (ADR-0063)

Owner: "1. Yes to forbidding two workspaces sharing a file. If it'll help we can simplify further: No sharing anywhere, no multiple instances of Finzytrack anywhere. Period. Finzytrack is just about one instance, working with one workspace at a time." (2026-10-27, in session; the full quote is in ADR-0063). Details marked "(agent)" are ADR-0063's.
- **Superseded:** "Copy to workspace…" writing into a target workspace that is not open. **Instead** (agent): the item is held in the user container, the switch to the target runs, and the target asks "Add <item> from <source>?" and runs its own validator; accounts missing in the target are reported, never fixed (INV-15). Nothing writes a workspace that is not open.
- "New workspace → start from the settings of another workspace" reads the source's tool state without opening it, and writes only the new folder (unchanged in effect); it is refused while an unfinished save that could not be undone names a file it would read (ADR-0063 decision 2).
- The deferred live library (CAP-WS-LIBRARY) stays deferred; building it needs an ADR that revisits ADR-0063 ("no sharing anywhere").
- **Unchanged:** the rest of this ADR, including copy-based sharing of settings and shipped items shared read-only by `pack:` reference.

## References

review:23 §1.3, §2, §2a, §7.3 Q1–Q2; review:13 §5 Q2; review:18 M-3; ADR-0017, ADR-0033, ADR-0034; INV-13, INV-15, INV-27; CAP-WS-ALLOW-AI, CAP-WS-SECRETS, CAP-WS-COPY-ITEM, CAP-WS-LIBRARY; ADR-0063.
