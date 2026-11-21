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
- One helper for every load a test compares (agent; round 1, V-1 and V-2): `engine/tests/fresh_engine.py`'s `fresh_engine()`. Before its body it sets aside the Beancount modules a fresh engine has not imported (`at_start()`: asked of a fresh `python -I -c "import ledger_engine.protocol.server"` once per run, so it follows a Beancount upgrade instead of naming `documents` and `pad`); after the body it drops every module the body imported and puts the set-aside ones back. The existing helpers (`conftest._forget_ledger_plugins`, `parity.fresh_plugins`, the loops in `test_preview_ops.py`) drop only modules whose file is in a test folder, so none could drop Beancount's own steps.
- Why it puts the modules back (agent): the fake's loads rely on the steps staying imported once warmed (`tests/ledger/memledger._warm`, its stated fidelity limit: the fake's observation refuses a set that names a module). A first version left them dropped, which would make a fake-world test after a `fresh_engine` test fail; with the restore the rest of the process finds its module cache as it was. The other side of that choice: since the restore can leave the steps imported, every load a test compares runs in `fresh_engine()`, not only the first.
- Where it is used: `test_rebuild_ops.py` (`fresh_setup` and every op `call`), `ledger/test_open.py` (the open and the comparison load in `test_the_open_loads_with_the_overlay_load_and_publishes_what_it_parsed`), `ledger/test_rebuild.py` (`go`, `hint_of`, `hint_of_refused`), `test_preview_ops.py` (the open and the preview in `test_the_preview_s_fingerprint_names_the_plugin_code_the_open_s_stamp_names`, replacing its plugin-only drop loop).
- `engine/CLAUDE.md`'s test rule and the conftest docstring name the helper (PROCESS.md §7; agent). The roadmap's M1b line marks BL-0299 done (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none added or changed (no product code). No test's assertions changed; only where its loads run.
- Base failure: at the base, `pytest tests/test_rebuild_ops.py` fails `test_a_saved_ledger_is_rebuilt_through_the_protocol` with `('loaded', 'published', 3) != ('fresh', 'fresh', 2)`; `tests/ledger/test_open.py` alone fails `test_the_open_loads_with_the_overlay_load_and_publishes_what_it_parsed` (`modules=()` against `documents`, `pad`); `test_rebuild.py::test_a_file_edited_during_the_load_is_a_moved_disk` and `test_preview_ops.py::test_the_preview_s_fingerprint_names_the_plugin_code_the_open_s_stamp_names` fail run alone by node ID. All four pass at the head, alone and in the suite.
- Mutant (V-2): the rebuild's stamp records no module lookups (`rebuild.py`: `tuple(sorted(set(used.modules)))` replaced by `()`): killed by `test_rebuild_ops.py` alone (1 failed), after `test_open_ops.py` (1 failed; at e6003f35 it survived there), and by `ledger/test_rebuild.py` (4 failed). Restored after.
- Population (the sweep, V-1), built by behaviour, not by name, over all 94 `test_*.py` files under `engine/tests` (the sandbox blocks `diskutil`, so `fs/test_darwin.py`'s one diskutil test fails in every run below and is not counted):
  - every file alone: at e6003f35, 92 run (2 collect nothing off Linux/Windows: `fs/test_linux.py`, `fs/test_windows.py`), 1 fails: `ledger/test_open.py` (V-1). At the head: 0 fail.
  - the whole suite with Beancount's lazy steps forgotten before every test (a scratch pytest plugin, outside the repo): with only V-1 and V-2 fixed it found the two node-ID cases above (`test_rebuild.py`, `test_preview_ops.py`); at the head, 0 fail outside `ledger/test_write_sequence_model.py`, whose 13 failures are the fake's deliberate warm-up (`memledger._warm`, cached once per process) undone by the plugin, which no real order does now that `fresh_engine()` restores.
  - the whole suite with the steps imported before every test: 0 fail at the head.
  - the normal suite at the head: 11166 passed, 24 skipped, 1 failed (`diskutil`).
  - Limit: the sweeps vary only Beancount's lazily imported steps and the file order; another lazily imported module would need its own sweep. The files the sweeps touched: `test_rebuild_ops.py`, `ledger/test_open.py`, `ledger/test_rebuild.py`, `test_preview_ops.py`; the sweeps found no other test whose result depends on the steps.
- Gates at the head: engine ruff format and check clean; `pyright_platforms.py engine` 0 errors on Darwin, Linux and Windows; `check_invariants.py` and `run_meta_tests.py` below.

## Verifier
- Round 1, head `e6003f35`: PASS WITH FINDINGS, no High, no class (a)/(b). Both fixed with tests and docs only, so no further round (IMP-0075).

| ID | Severity | Class | Disposition |
|---|---|---|---|
| V-1 | Medium | c | `ledger/test_open.py`'s open test had the same order dependence (fails alone, also at the base). Fixed with the shared `fresh_engine()`; the sweep over every engine test file found two more (`test_rebuild.py`, `test_preview_ops.py`), fixed the same way (Hand-off). |
| V-2 | Low | c | `fresh_setup` passed by order: when an earlier file had imported the steps, neither load recorded them, and the "stamp records no modules" mutant survived after `test_open_ops.py`. `fresh_engine()` sets the steps aside first, so the setup's load always records them; the mutant is killed alone and after `test_open_ops.py`. |

## Retro
- Build about 40 min, fix about 70 min (estimates), 1 verifier round, findings 0H/1M/1L. Cheaper gate: one run of the suite with Beancount's lazy steps forgotten before every test found all three remaining order dependences at once, where running files alone found one; a sweep over the module state, not the file order, is the check to reach for first. Process checks: none fired.
