# May nine invariant tests skip on Windows until the port turns them on, or does the Windows job stay red?
- Asked: 2026-11-17 by the implementer, S0127-windows-paths
- blocks: nothing (S0127 lands either way; only whether its `engine-windows` CI job can be green)
- Rubric item: the list below the numbered items ("loosening any gate")
- ADRs checked: ADR-0061 and its 2026-11-14 amendment (Windows tests from M1b on), ADR-0069 (the split, "keep red"); none says how a Windows-only skip in an invariant's enforcement file is treated. `scripts/check_invariants.py` (S0031) refuses every skip form in such a file, with one exception: a skip that cannot fire on macOS, written as `unittest.skipUnless(sys.platform == "darwin", ...)`.

## Context
S0127 makes the engine's test suite load and run on the Windows CI runner. Every test that needs a later part of the port now skips there with one line naming its backlog item (BL-0280 the file system, BL-0281 the process group, BL-0282 Beancount's own paths and messages on Windows). Nine tests cannot skip, because they sit in files that enforce an invariant, and `check_invariants.py` refuses any skip there:
- `engine/tests/ledger/test_overlay.py` (INV-02): a plugin that decrypts while it is imported. On Windows the refusal names its subject differently (BL-0282).
- `engine/tests/ledger/test_parity_corpus.py` (INV-03), four tests: Beancount on Windows reports a glob that matches nothing differently from macOS ("does not match" against "does not exist"), so the corpus's expected errors are macOS's (BL-0282).
- `engine/tests/architecture/test_no_resident_ledger.py` (INV-10), four tests: they run a real open, save and rebuild, which Windows refuses until its adapter (BL-0280).
Example: on the runner, `test_beancount_gives_each_load_case_the_errors_it_is_written_for` finds `absolute-paths: {'LoadError': 1, 'DocumentError': 2}` where macOS finds none. Nothing is wrong with the invariants on macOS or Linux; these nine are true on the hooks' and the Linux job's systems and cannot be true on Windows before the port's later parts.

## Options (cheapest first)
1. **Keep the `engine-windows` job red on these nine until BL-0280 and BL-0282 make them pass** (no gate change). Cost: the job is red for a known reason, so a new Windows failure is harder to see among them.
2. **Allow a second skip form that cannot fire where the hooks run nor in the Linux job:** `if sys.platform == "win32": raise pytest.skip.Exception(<a reason naming an open backlog row>)` in an enforcement file, checked by `check_invariants.py` as strictly as the darwin form (exactly that shape, the reason a constant from `tests/windows_pending.py` that names an open row). A small loosening of the S0031 gate, Windows only; macOS and Linux enforcement unchanged.
3. Move the nine out of the enforcement files. Rejected: it weakens what the files enforce on macOS.

## Recommendation
Option 1 for now: the Windows port's parts are near (BL-0280 turns the four INV-10 tests on; BL-0282 the five Beancount ones), the job was already accepted red until the port lands (2026-11-17: "keep red"), and it needs no gate change. Option 2 if the owner wants a green Windows job before BL-0280: it would be one tooling slice (the `check_invariants.py` form and its must-not-pass tests); the S0031 gate has had one round of red-team fixes (S0031 verifier F1-F8), no High since.

## Answer (owner, verbatim, with date)
