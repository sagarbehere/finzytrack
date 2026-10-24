# ADR-0002: Greenfield rebuild on `next`; v0.3.0 is a tag only

This record decides the shape of the v0.4.0 rebuild, its branch, and the fate of v0.3.0.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** repository, release

## Context

The review produced about 240 unique findings. About 9% of the frontend survives as code, and about a quarter to a third of the backend (review:21 §2). The decided designs rewrite most of the rest. A staged strangler inside one app was the earlier recommendation; it would keep two API generations, shims and transitional allowlists in one process.

## Decision

(owner, 2026-10-24)
- **v0.4.0 is a new app, built from an empty tree on the long-lived branch `next`** in the same repository. It grows to parity with v0.3.0 in vertical order: shell/boot → Accounts → Transactions → Import → Dashboards → Investments → Assistant → Settings.
- **v0.3.0 is never released.** `valuations` is merged into `main` and tagged `v0.3.0` and `legacy-final` on the same commit. Users go from v0.2.x straight to v0.4.0.
- `main` stays the legacy line: no feature work and no hotfix release during the rebuild. Release = merge `next` into `main` once the release gate holds (ADR-0043).
- **Nothing old lives in the new tree.** Code from v0.3.0 is *copied* in where it ports, with its tests, and meets every gate on arrival. There is no transitional allowlist.
- **The old app is a read-only reference and a differential oracle.** A read-only worktree of `legacy-final` sits next to the repo (the legacy worktree). The oracle harness runs it as a separate process on its own ports, only against ledger copies (ADR-0014).
- **The owner keeps using v0.3.0 day to day** from the legacy worktree, against the owner's live data folder, until v0.4.0 is ready. Never run v0.3 and v0.4 writing the same ledger at the same time.
- **Repo hygiene:** the owner moved the live config, data and the private review archive out of the checkout before `next` was created. The `.gitignore` on `next` keeps `data`, `backend/config/` and `dev-docs/` permanently. A T0 staged-file guard refuses real-data files, and agent settings deny `git clean -x/-X` and reads of the live data folder.
- **Versioning:** the rebuild ships as v0.4.0 (a minor bump signals breaking changes under 0.x). `1.0` is kept for when the format and API are declared stable.
- v0.4 adopts an existing v0.2 install non-destructively (ADR-0020).

## Alternatives considered and rejected

- **Incremental fixes on the v0.3 code.** Rejected: the frontend would be edited at ~90% of its lines anyway, paying for an intermediate state twice. It remains the fallback if G1 says no-go (ADR-0046).
- **Strangler inside one app.** Rejected: two API generations in one process, re-export shims, frozen endpoints, a staged writer hand-over and an old tree to delete before release. The greenfield keeps the strangler's benefits (old UI usable, API tests runnable, a writer to compare against) through the legacy worktree.
- **A new repository.** Rejected: it loses history, blame, tags, CI and the desktop build setup.
- **An orphan branch.** Rejected: its history is disjoint from `main`, and merging back is awkward.
- **Releasing v0.3.0.** Rejected: users would get a version that the rebuild replaces at once.

## Consequences

- Old bugs are not patched anywhere. Each integrity finding's repro becomes an acceptance test for the new app; where the old app shows the bug, the oracle lists it as an expected divergence.
- Release notes list every capability removed relative to v0.2.2, with its ADR. Capabilities that existed only in v0.3.0 need no removal entry.
- Downgrade note: if v0.2 or v0.3 rewrites a ledger that v0.4 edited, comments and splices are lost.
- A kill/continue point exists at G1 (ADR-0046).

## References

review:21 §1–§5; review:20 §9; review:F §2 item 1; ADR-0014, ADR-0020, ADR-0043, ADR-0046.
