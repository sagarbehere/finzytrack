# ADR-0066: The features' headless logic lives in `composables/`; INV-12 says "composables"

This record renames the frontend features' `hooks/` folder to `composables/` and changes INV-12's wording to match, the Vue 3 word for the same thing (ADR-0053).

- **Status:** Accepted. Note of 2026-10-29: decision 3's check made exact (S0056 verifier round 1).
- **Date:** 2026-10-29
- **Deciders:** owner: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it." (2026-10-29, in session; S0056)
- **Area:** frontend, invariants
- **Amends:** INV-12's rule text; the folder name in ADR-0036's structure (its amendment of 2026-10-29).
- **Unchanged:** INV-12's meaning and enforcement (`frontend/eslint.boundaries.js` places every file under `src/features/*`, whatever the sub-folder); ADR-0036's structure otherwise.

## Context

ADR-0053 chose Vue 3 and read "hooks" in the design documents as composables. S0055 rewrote the documents for Vue but kept the folder name `features/<name>/hooks/`, because the planned integrity tests import from it, and left INV-12's owner-owned text ("domain modules, hooks or machines") as it was. Both were offered to the owner as open items. A folder called `hooks/` in a Vue app, beside git and Claude Code hooks in the same repository, invites the misreading that S0055 cleaned up.

## Decision

1. **The folder is `features/<name>/composables/`** (owner, 2026-10-29): ARCHITECTURE.md §3.1, ENGINEERING.md EP-11, the boundary test fixtures and the planned integrity tests use it. No product folder existed yet, so nothing in `src/` moves.
2. **INV-12 reads "logic sits in domain modules, composables or machines"** (owner, 2026-10-29). The meaning is unchanged: it is a rename, neither a tightening nor a weakening.
3. **The retired-terms check refuses a frontend `hooks/` folder** in the current-state documents (agent; IMP-0048): a `hooks/` path segment that starts a path, or one under `features/`. Git's `.githooks/`, Claude Code's `.claude/hooks/` and `core.hooksPath` pass.

## Alternatives considered and rejected

- **Keep `hooks/` and read it as composables** (S0055's choice). Rejected by the owner.
- **Refuse every bare "hooks" in the documents.** Rejected: git hooks, Claude Code hooks and Vue's lifecycle hooks are real and current.

## Consequences

- A feature built from now on puts its composables in `composables/`; the planned integrity tests (`frontend/tests/unit/integrity/transactions.test.ts`, `widgets.test.ts`) import from there.

## References

INV-12; ADR-0036, ADR-0053; S0055 journal (open items 1 and 2); S0056 journal; IMP-0048.

## Note (2026-10-29): decision 3 made exact (S0056 verifier round 1, F4)

Decision 3 means any frontend `hooks/` folder, and only that. The check's first wording ("a `hooks/` segment that starts a path, or one under `features/`") missed `frontend/src/hooks/`, `src/hooks/` and "the features' `hooks` folder", and refused prose such as "hooks/guards", "hooks/skills" and Claude Code's `hooks/guards.py`. **Instead** it refuses `src/hooks` (so `frontend/src/hooks/` too), `features/<x>/hooks`, a bare `hooks/` that names the folder alone (a tree or list entry, "`hooks/`") and "`hooks` folder"/"directory"; a `hooks/` followed by a name passes. Stated limit: a bare `hooks/useX.ts` with no `src/` or `features/` before it passes. Tests: `scripts/tests/test_check_retired_terms.py` (the verifier's probes, both ways).
