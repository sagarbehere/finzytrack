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
