# S0010: The Tauri shell skeleton, its gates and the Rust licence check (BL-0037, BL-0033, BL-0044)

## Brief
- Goal: create `shell/src-tauri/` as an empty Tauri 2 window over the frontend bundle, with the pinned toolchain (Rust 1.99.0, tauri 2.12.3), the decided config (identifier, macOS 14 minimum, a CSP without inline script, no plugin permissions), a lockfile that respects EP-24's 14-day rule, the T1 job (rustfmt, pedantic clippy), the pre-push checks, the Rust half of the licence check, and the GPLv3 text.
- Implements: ADR-0007 (+ 2026-10-25 amendment), ADR-0040 (CSP), ADR-0042 and ADR-0054 (licences), EP-24 (+ the security exception, ADR-0050 amendment); BL-0037 (shell part), BL-0033, BL-0044 (the text).
- Capabilities: none (the window is M0 infrastructure; CAP-DESK-WINDOW is M2).
- Invariants touched: none directly; INV-23 (the icon is a generated placeholder, no third-party asset).
- Risk class / lane: Medium / full; no ledger or user data, so one verifier round unless a High is found (ADR-0058).
- Acceptance tests: `scripts/tests/test_shell_config.py` (the committed config holds; planted identifier, macOS minimum, CSP and permission variants fail; a missing CSP fails); `scripts/tests/test_check_licences.py` Rust cases (normal dependencies of any depth ship; build and dev dependencies and their trees do not; the shell itself is not a dependency; an edge that is normal on some target ships; `/` means OR; a missing licence field is refused); the gates: `cargo fmt --check`, `cargo clippy --all-targets --locked -- -D warnings`, the licence check.
- Expected doc impact: ARCHITECTURE.md §11 (shell layout, sidecar per ADR-0056); README licence paragraph; backlog BL-0033 done, BL-0044 to M11, BL-0037 note, BL-0052; roadmap §1; IMPROVEMENTS IMP-0018; `THIRD_PARTY_NOTICES`. No user docs.
- Open questions: none.
- Base SHA: f3d2efa.

## Decisions
- Rust licences come from `cargo metadata`, judged by the one allowlist and `judge`: the shipped closure follows normal dependency edges on every target, so Windows- and Linux-only crates are counted (the Python side's BL-0050 gap does not arise). cargo-about is kept for collecting licence texts at bundling; cargo-deny is not used yet (agent).
- EP-24 for Cargo: 37 of 404 crates in a fresh resolve were under 14 days old; each was moved to its newest version published on or before 2026-10-11 with `cargo update --precise`, in four rounds (some could move only after their dependents did); 0 remain. The publish dates came from the crates.io API (the one-off scripts stay in the session scratchpad; the T1 check is BL-0052) (agent).
- The owner asked why crates were moved to older versions; the answer and the owner's security exception are in ADR-0050's amendment and IMP-0017.
- A debug build needs no `frontend/dist` (checked), so the shell job does not build the frontend (agent).

## Hand-off (ADR-0057)
- Checks with must-not-pass tests: the four config checks and a missing CSP; the Rust closure (build/dev edges excluded, a target-specific normal edge included, the root excluded, a missing licence refused).
- Self-mutation: 8 tried (licence closure, slash, missing licence; CSP and permission checks; a loosened CSP and a new permission in the committed config), 8 killed.

## Verifier
- Round 1, head e2ebb43: PASS WITH FINDINGS. Attacks: 8 (gates on a clean export; the licence closure against `cargo tree -e normal --target all` (nothing shipped missing); publish dates of all 406 locked crates (newest 2026-10-11, none yanked); a release build with an inline script; planted config variants; mutants; a release build without `frontend/dist`; the icon).
- F1 Medium (ADR-0040 + amendment): the CSP lacked `object-src 'none'` and `ipc:`, and Tauri's script rewriting was on, so an inline script in the bundle would get a nonce and run. Fixed in `tauri.conf.json`. The pre-paint hash waits for BL-0032; the `127.0.0.1` source for the sidecar slice.
- F2 Medium (EP-29, ADR-0040, ADR-0007): the guard missed seven forbidden variants (upper-case directive, `script-src-elem`, `data:`/`https:` sources, `object-src *`, rewriting on, inline capabilities) and read only `default.json`. Fixed: the guard parses directives as browsers do (case-insensitive, first occurrence wins), allows only `'self'` and hashes as script sources, requires `object-src 'none'`, IPC and rewriting off, refuses inline capabilities and any permission outside an allowlist (empty in M0; `fs` and shell execution never), in every capability file; 17 planted variants beside two must-pass cases.
- F3 Medium (ADR-0042, rubric item 4): `cargo metadata` replaces the named cargo-deny. Owner chose option 1 (ADR-0042 amendment, 2026-10-25); ARCHITECTURE.md §10 updated.
- F4 Low: our source's licence field said GPL-3.0-or-later. Fixed to GPL-2.0-or-later in the shell, backend and frontend manifests (the README's rule; the distributed sidecar and shell are GPLv3, ADR-0054).
- F5 Low: productName and a missing cargo were untested. Fixed (a variant; a fail-closed test).
- F6 Low: ENGINEERING.md §0 did not list the Rust gates. Fixed (also the stale `tsc`/React wording there); ARCHITECTURE.md §5 no longer says PyInstaller (ADR-0056).
- Self-mutation of the fixes: 12 tried, 11 killed; `object-perms-skipped` is equivalent (an object permission with an empty identifier is refused anyway).
- Two Medium fixed, one to the owner: no further round (ADR-0058).

## Retro
- The guard was written for the four values in the brief, not for the ways a CSP can be loosened; the verifier's list of loosenings was the spec I should have started from (EP-30's other side: when a class must be supported, enumerate how it fails).
- The 14-day rule cost about 15 minutes of API lookups for 404 crates; BL-0052 makes it a check.
- Process cost: shell draft and lock work about 1 h 15 min; verifier about 15 min wall (the publish-date sweep); fixes about 35 min.
