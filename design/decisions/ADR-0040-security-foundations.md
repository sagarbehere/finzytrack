# ADR-0040: Security foundations are part of the shell from the first build

This record states the security requirements every v0.4 build meets from the start, and how security work is handled in a public repo.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** security, privacy

## Context

Ledger text, payees, metadata, import files, emails, recipes and model output are all data a user may not have written. The repository is public (INV-23). The owner decided not to ship a separate security release of the old line; v0.4.0 is to ship quickly and users are encouraged to upgrade.

## Decision

(owner, 2026-10-24)
- **Data is never markup** (INV-09): assistant and user markdown is rendered with HTML disabled and sanitized (markdown-it + DOMPurify) through one `SafeMarkdown` component, the only raw-HTML site. Recipe and chart option strings are data; HTML and JavaScript formatters are rejected by the validator; chart tooltips never inject formatter output as HTML.
- **A strict Content Security Policy** for the desktop webview and as an HTTP header in browser and headless mode (`script-src 'self'` plus the hash of the pre-paint script, no inline script, `object-src 'none'`).
- **IMAP verifies server certificates** (`ssl.create_default_context()` on every connect).
- **No pickle cache is read or written** next to a ledger; `beancount_port` disables Beancount's cache.
- **Secrets never reach the model:** secrets are redacted on every read the AI can see; rules hold `${NAME}` references only; secret values live in machine-local `app/secrets/`, scoped per workspace (ADR-0021).
- **Query engines are confined** (ADR-0016): the hardened SQLite runner; BQL statement allowlist.
- **Ledger loads go through `LoadPolicy`** (ADR-0010). On desktop, a ledger that runs code (`insert_pythonpath`, non-allowlisted plugins, plugins that evaluate non-literal config) asks "This ledger runs code … Trust?" on first open; the answer is machine-local; until trusted, it loads under the restrictive policy.
- **Destructive document operations never touch ledger sources** (the orphan scan excludes includes, the sidecar and every `*.beancount` file).
- **Headless LAN mode:** when the backend is bound beyond loopback, path-taking endpoints (open by path, Locate, the server file picker) are disabled or require the shell token.
- **The webview has no file-system access** (no Tauri `fs` plugin); its only route to disk is the backend API (ADR-0007).
- **Approvals bind to server-side plans** (ADR-0033).
- **Public-repo rule:** tests and repros that work against a released version stay in the private review archive until v0.4.0 ships. On `next`, a fix lands with a neutral regression test that states the requirement, not the attack.

## Alternatives considered and rejected

- **Patch releases of the v0.2 line during the rebuild.** Rejected by the owner (few users); v0.4.0 ships quickly instead, and users are encouraged to upgrade.
- **A hand-written markdown renderer or a "safe" regex escaper.** Rejected: not safe by construction.
- **A security review only at the end.** Rejected: the security red team runs once per area and for each change that adds a new input (ADR-0044).

## Consequences

- Hostile-input corpora (markup in payees and metadata, chart formatters, markdown, SQL/BQL escapes, path traversal) are repo assets that run in T1.
- Release notes say that known security issues of earlier versions are fixed and encourage upgrading.

## Amendment (2026-10-25): desktop CSP details from spike S2

Owner: "I am accepting Q16 recommendation." (2026-10-25, in session)
- `connect-src` includes `ipc: http://ipc.localhost` (Tauri's IPC); without it every IPC call raises a CSP violation and falls back to postMessage.
- Tauri's automatic CSP rewriting for scripts is turned off (`dangerousDisableAssetCspModification: ["script-src"]`), so the configured hash of the pre-paint script is the only inline script allowed; a modified inline script is blocked and reported. A test checks that the shipped pre-paint script matches the configured hash.

## References

review:13 §1, §4; review:14 §0 (N1 constraint); review:18 §1–§2; review:23 §5.1; review:F §2 item 2; ADR-0007, ADR-0010, ADR-0016, ADR-0021, ADR-0033, ADR-0044; INV-09, INV-23.
