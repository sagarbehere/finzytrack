# S0006: The main sidecar's package skeleton and its CI gates (BL-0037, backend part)

## Brief
- Goal: create `backend/` as the main sidecar's empty package skeleton (ADR-0054: GPLv3, never imports Beancount) with its pinned toolchain and the gates that every later backend slice inherits: ruff format and lint, pyright strict, import-linter contracts, deptry, pytest; a T1 job; the pre-push and pre-commit hooks already call these tools once `backend/` exists. The licence check counts only what is bundled.
- Implements: BL-0037 (backend part); ARCHITECTURE.md §2.1–2.4 (package shape, contracts 1, 3 and the Beancount ban), §10 (pins), §11 (layout); ADR-0054 decision 5 ("a T1 check proves the main environment cannot import beancount"); ADR-0042 (licence check); EP-22 (strictest type checking), EP-24 (pins, 14 days), EP-28 (no sleeps).
- Capabilities: none (no user-visible behaviour).
- Invariants touched: INV-02 (the main sidecar never imports Beancount; the engine side is BL-0041), INV-07 (`date.today` banned by lint), INV-12 (backend half of the layering contracts; status stays pending until the frontend boundaries exist, BL-0012), INV-23 (no data in the skeleton).
- Risk class / lane: Medium / full (gates every later backend slice depends on; no ledger, finance or security behaviour).
- Out of scope: the engine environment and its contracts (BL-0041, M1a); the frontend and shell skeletons (next slices); the kernel-layers revision for the engine split (BL-0041).
- Acceptance tests:
  - `backend/tests/architecture/test_contracts.py`: the contracts hold on the tree; beside it, a planted violation breaks its contract (Beancount or beanquery imported from any package; `os`, `pathlib`, `pydantic` in `domain`; `domain` importing `platform`; `platform` importing `api`; `mirror` importing `workspaces`); must pass: a higher layer importing a lower one; `beancount` and `beanquery` are not importable in the environment; the lockfile has neither (and the lock check sees a planted one).
  - `scripts/tests/test_check_licences.py`: only the runtime closure of the lockfile is bundled (dev tools in the venv are not); names match across spelling; a runtime package missing from the venv fails closed; must not pass: a licence not on the allowlist, a classifier name such as "BSD License", an `AND` with one disallowed part; an `OR` with one allowed option passes; a pending licence warns.
  - Gates run green at head: ruff format/lint, pyright strict (0 errors), lint-imports, deptry, pytest, the licence check.
- Expected doc impact: `backend/CLAUDE.md` (the Beancount rule after ADR-0054; the gate commands); `design/ARCHITECTURE.md` §10 (build backend pin) and §11 (`.python-version`); `design/backlog.md` BL-0037 progress and a new item for classifier-only licence names; `.github/workflows/ci.yml` (backend job; uv in repo-checks for the licence step); `.githooks/pre-push` (contracts, deptry, all backend tests); `THIRD_PARTY_NOTICES` regenerated (still empty for Python); IMPROVEMENTS entry for the new T1 job. No user docs.
- Open questions: none.
- Base SHA: fa37a0c.

## Decisions
- The main sidecar gets no runtime dependencies yet; each arrives with the slice that first imports it (deptry would flag an unused one) (agent).
- Layers that do not exist yet are optional in `.importlinter` (parentheses); the slice that creates a package removes them. `app.platform.adapters` is not in the domain purity list yet: import-linter refuses a forbidden internal module that does not exist, and the layers contract already forbids `domain → platform` (agent).
- The licence check counts the runtime closure of the lockfile (`uv export --no-dev`) instead of everything in the venv, which would list the dev tools as bundled (agent; ADR-0042 is about what ships). Found on the way: pip-licenses reports classifier names that never match SPDX ids (BL-0050, before the first runtime dependency).
- ruff bans `datetime.date.today` (INV-07) and `time.sleep` (EP-28) everywhere for now; a later slice that needs either in a shell module exempts that file with a reason (agent).
- Python 3.13.16 comes from `backend/.python-version`; the build backend is `uv_build` 0.13.0, matched to uv (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept or refuse, each with its must-not-pass test: the Beancount ban (planted `beancount`/`beanquery` in three packages), domain purity (planted `os`, `pathlib`, `pydantic`), kernel layers (planted upward imports) beside `test_allowed_direction_passes` (downward imports, incl. `workspaces → mirror`); the environment and lockfile checks (with a planted lock entry); the licence filter (dev tools excluded, missing runtime package fails closed) and the allowlist (unlisted, classifier name and `AND` refused; `OR` and pending pass).
- Self-mutation: 12 tried on `.importlinter` and `scripts/check_licences.py`, 10 killed at first. Survivors: siblings collapsed into one layer (no test that `workspaces` may import `mirror`; then the planted import named a package missing from the tree, so a second fix planted it) and option lines kept in the export parse (no option lines in the fixture). Both now have tests: 12 tried, 12 killed.
- Population: none claimed.
