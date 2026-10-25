# May the Rust licence check use `cargo metadata` instead of cargo-deny?
- Asked: 2026-10-25 by S0010-shell-skeleton (verifier finding F3)
- blocks: nothing (the check runs today; only the ADR text is out of step)
- Rubric item: 4 (changing an ADR the owner decided)
- ADRs checked: ADR-0042 names "cargo-deny + cargo-about (Rust)" for the T1 licence check; ADR-0054 splits the allowlists per process; neither says how the shipped crate set is computed.

## Context (plain language, a concrete example first)
The desktop shell pulls in about 380 Rust packages. The licence check must list every package that ships and refuse any whose licence we do not accept. ADR-0042 named two tools for this: cargo-deny (checks licences against a list) and cargo-about (collects the licence texts for the notices file).

In S0010 I used Cargo's own `cargo metadata` instead: it lists every package with its licence and how it is used. The check walks from the shell through the dependencies that are compiled into the program (on Windows, macOS and Linux alike) and judges each licence with the same allowlist and rules the Python and JavaScript sides use. The verifier compared the result with Cargo's own `cargo tree` and found nothing that ships missing. cargo-about is still needed later, when the installers must contain the licence texts (M11).

## Options
1. Amend ADR-0042: the Rust licence list comes from `cargo metadata`, judged by the one allowlist; cargo-about collects the licence texts at bundling; cargo-deny is not used for licences (it may be added later for security advisories).
2. Use cargo-deny as named: a second allowlist in its own format (`deny.toml`) that must be kept in step with `licences.allow.toml`.

## Recommendation
Option 1: one allowlist and one set of rules for all three languages, with no extra tool to pin and update; the result is the same set of packages.

## Answer (owner, verbatim, with date)
"Amend ADR-0042: the Rust licence list comes from `cargo metadata`, judged by the one allowlist; cargo-about collects the licence texts at bundling; cargo-deny is not used for licences (it may be added later for security advisories)." (owner, 2026-10-25, in session). Option 1; recorded as the ADR-0042 amendment.
