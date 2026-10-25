# ADR-0042: Licence: owner code GPL-2.0-or-later; notices and licence checks; legal advice before v0.4.0

This record decides the licence of the project's own code and how third-party licences are tracked.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** legal, release

## Context

The project is GPL v2, and the README does not say "or later". Beancount, which the app bundles in its sidecar, is GPL-2.0-only. The app also bundles Apache-2.0 code (the charts library, and the OpenAI SDK in the same Python bundle). The FSF considers Apache-2.0 incompatible with GPL v2. Declaring the owner's code "or later" does not resolve the bundle question, because Beancount stays GPL-2.0-only. This record is not legal advice.

## Decision

(owner, 2026-10-24)
- **The owner's own code is declared GPL-2.0-or-later** (`LICENSE` and README wording, in the first commit on `next`).
- **Get legal advice before v0.4.0** on the bundle question.
- **A `THIRD_PARTY_NOTICES` file** at the repo root and in the packaged app, generated from the lockfiles.
- **Automated licence checks in T1** over the Python, JS and Rust dependency trees against an allowlist: pip-licenses (Python), license-checker-rseidelsohn (JS), cargo-deny + cargo-about (Rust). T1 fails on a dependency whose licence is not allowlisted and when the notices file is stale.

## Alternatives considered and rejected

- **Leave the licence as it is.** Rejected: the "or later" question and the bundle question stay unaddressed.
- **Assume "or later" solves the bundle conflict.** Rejected: it does not, because Beancount is GPL-2.0-only.

## Consequences

- A new dependency with a licence outside the allowlist needs an explicit allowlist change, reviewed like any rule change.
- The legal advice may lead to a superseding ADR (for example a change in bundled components).
- **Amended by ADR-0054 (2026-10-25):** Beancount runs in its own engine process; the main sidecar never imports it and is distributed under GPLv3. The licence check runs per process with two allowlists.

## References

review:F §2 item 9, §1 (F63 note); review:22 §2 (licence); review:25 §10 (licence checks); ADR-0007.
