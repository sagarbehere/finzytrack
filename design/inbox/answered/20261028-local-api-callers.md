# Who may call the sidecar's local API once it serves data?
- Asked: 2026-10-28 by the implementer of S0048 (BL-0105)
- blocks: BL-0115, and so the first slice that serves ledger or app data over HTTP (L10-09 `/api/boot`, BL-0102's workspace routes); not S0048, which serves only `/health`
- Rubric item: 6 (security posture: who can authenticate to the API)
- ADRs checked: ADR-0040 (security foundations: CSP, the shell token for path-taking endpoints, LAN mode), ADR-0007 (shell and sidecar), ADR-0038 (API contract), ADR-0062 (one instance). None says who may call the API on loopback in the desktop build.

## Context (plain language, a concrete example first)
Since S0048 the sidecar answers HTTP on `127.0.0.1:<port>`. The window calls it, and so can any other program on the same computer, and so can any web page open in the user's browser. Example: the user visits a hostile web page while Finzytrack runs. The page cannot read the port from the handshake, but it can try the ports one by one. Two attacks then work against a local server that checks nothing:
- **Writing (CSRF):** the page sends `POST http://127.0.0.1:<port>/api/w/<key>/…` as a "simple" request. The browser sends it without asking the server first. The page cannot read the answer, but the server acts on it.
- **Reading (DNS rebinding):** the page's own domain is made to point at `127.0.0.1`. To the browser the server now has the page's origin, so the page can read the answers: balances, payees, file paths.

Today only `/health` is served (it says the version), so nothing is at risk yet. ADR-0040 asks for the shell token only on path-taking endpoints and in LAN mode, and ARCHITECTURE §5 already has the shell inject a token into the window (`--shell-token-env FT_SHELL_TOKEN`).

## Options
1. **Every API request carries the shell token** (a header the window sends, from the shell's injection script; the browser and headless builds get it another way, decided with BL-0095), and the server also refuses a `Host` header other than `127.0.0.1:<port>` or `localhost:<port>`. A web page has neither, so both attacks fail. Cost: one middleware and its tests; the acceptance harness passes the token.
2. **Only a `Host` and `Origin` check:** refuse a `Host` that is not ours (stops rebinding) and a write whose `Origin` is not the window's (stops CSRF). No token to distribute, but it trusts every local program and relies on browsers sending `Origin`.
3. **Keep ADR-0040 as written:** the token only on path-taking endpoints. Both attacks stay open for every other route.

## Recommendation
Option 1, built with the shell token in M2 before the first data route lands, recorded as an amendment to ADR-0040. It closes both attacks for every route at once and uses the token the design already has. The `Host` check costs nothing and also protects the browser build.

## Answer (owner, verbatim, with date)
Owner, 2026-10-28, in session: "For the model, let's go with B. For the security question, option 1." (the model part answers another question).

To apply (BL-0115, M2, before the first route that serves ledger or app data): option 1, every API request carries the shell token and the server refuses a `Host` other than `127.0.0.1:<port>` or `localhost:<port>`, recorded as an amendment to ADR-0040.
