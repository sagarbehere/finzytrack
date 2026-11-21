# S0142: The rebuild test runs alone (BL-0299)

## Brief
- Base: `cb802bb53edb9af9d611298b7910c63d9f34a8f4` (`next`).
- Goal: `engine/tests/test_rebuild_ops.py::test_a_saved_ledger_is_rebuilt_through_the_protocol` passes when its file runs alone, as it does in the full suite. The owner agreed the diagnosis and option 1 (owner, 2026-11-21, in session: "Yes").
- Diagnosis (verified at the base): the shared `setup` fixture's `overlay.load` (`tests/test_save_ops.py:76`) imports Beancount's lazily imported steps `beancount.ops.documents` and `beancount.ops.pad` and records them in the fingerprint, which is the rebuild's hint. The rebuild's `real_load` then runs in the same process, finds both in `sys.modules` and records no module lookup, so the published stamp has `modules=()`, `publishing.within` fails, and the second rebuild loads again (`('loaded', 'published', 3)`). In the full suite an earlier test imported them before the fixture's load, so neither load records them and the two agree. Dropping what the setup's load imported makes the test pass (a scratch conftest plugin, outside the repo).
- The product cannot meet it: every job that loads runs in a fresh engine that it then retires (`WorkResult.ran_user_code`), a retry gets a fresh engine, and no engine op runs two recording loads (engine/CLAUDE.md, "One load with plugins per engine process"). So this is class (c), a gap in our tests.
- Implements: BL-0299 (closed here). engine-jobs §5.1 (a fresh engine per load), unchanged.
- Capabilities: none. Invariants touched: none (the test carries `INV-22`; its assertions are unchanged).
- Risk class / lane: Low / light (test-only and a test docstring). Review row: "Other product code" does not apply (no product code); one verifier round.
- The bytes it protects: none (test only).
- Which real setup needs this, and can a policy forbid the rest? No product setup; the test's own order dependence. Has this family needed two or more fix rounds before? The module recording (S0099, BL-0124) had several rounds; this slice changes no product code of it.
- Acceptance: the file passes alone (it fails alone on the base: shown below) and the full engine suite passes.
- Expected doc impact: `engine/tests/conftest.py` docstring of `_forget_ledger_plugins` (the false sentence about leftover modules; the suite's order dependence stated as a limit); BL-0299 to done with the owner's quote. No new backlog row (owner's pace rules).
- Open questions: none.

## Decisions
- How the test drops what the setup's load imported (agent): a fixture in `test_rebuild_ops.py` that wraps the shared setup: it notes `sys.modules`, runs `save_setup`, and drops every module that appeared, as a fresh engine for the rebuild would not have them. The existing helpers (`conftest._forget_ledger_plugins`, `parity.fresh_plugins`, the loops in `test_preview_ops.py`) drop only modules whose file is under a test folder, so they would not drop Beancount's own steps; none fits. Only the rebuild test uses the new fixture: the other tests in the file do one rebuild and do not compare two.
- `engine/CLAUDE.md`'s test rule gains one clause, so it no longer says a plugin is all a two-load test must drop (PROCESS.md §7; agent).
- The roadmap's M1b line marks BL-0299 done (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none added or changed (no product code). The test's assertions are unchanged; only its fixture changed.
- Base failure: at the base, `pytest tests/test_rebuild_ops.py` fails `test_a_saved_ledger_is_rebuilt_through_the_protocol` with `('loaded', 'published', 3) != ('fresh', 'fresh', 2)`; at the head the file passes alone (17 passed). With the fixture's `del sys.modules[name]` replaced by `pass` (a hand mutant), the file fails alone again in the same way, so the drop is what makes it pass.
- Self-mutation: not applicable (test-only; no check guarding data).
- Population: what the setup imports when its file runs alone, listed in a scratch script outside the repo: `beancount.ops.documents`, `beancount.ops.pad` (2 modules; the fixture drops whatever appears, so it does not depend on this list).
- Gates at the head: engine ruff format and check clean; `pyright_platforms.py engine` 0 errors on Linux and Windows; `check_invariants.py` OK; `run_meta_tests.py` OK (1012 tests). Full engine suite in the sandbox: 11166 passed, 24 skipped, 1 failed: `tests/fs/test_darwin.py::test_volume_id_is_the_uuid_diskutil_reports`, because the sandbox blocks `diskutil` ("unable to use the DiskManagement framework"; the same command fails alone in it). It is not this slice's change; the pre-push hook runs the suite outside the sandbox.
