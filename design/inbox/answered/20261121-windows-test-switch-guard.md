# BL-0286: the Windows test switch's guard: state its limit until BL-0087 removes the switch, widen the scan again, or move the switch out of the product's source?
- Asked: 2026-11-21 by the orchestrator (before any BL-0286 slice; facts checked on `next` at 3284e4cd, on macOS)
- blocks: BL-0286 only. Its row says "before BL-0087 turns writes on", so BL-0087 waits for this answer.
- Rubric item: none of 1 to 8. A second fix round on one mechanism needs the owner's options note (PROCESS.md §3, review budget).
- ADRs checked: ADR-0069 (the Windows adapter) and ADR-0061 with its amendment of 2026-11-14 (the Windows port). Neither names the switch: it is S0128's test-only choice, recorded in the S0128 journal, not in an ADR.

## Situation
Until BL-0087, the product refuses every file operation on Windows. The engine's real-disk tests still need to run the product's own code on the Windows adapter, and a **test switch** lets them do that. The switch is a context variable, `ON`, in its own product module, `engine/src/ledger_engine/fs/under_test.py`. Only the selection point, `fs/this_os.py`, reads it, and only beside a `win32` check. The 17 real-disk test modules turn it on through the `windows_port` fixture in `engine/tests/conftest.py`.

What keeps the product from turning it on is an AST scan, `switch_uses` in `engine/tests/architecture/test_platform_selection.py`. It refuses product code that imports the switch module, uses any of its names, applies `getattr`, `setattr`, `vars` or `__dict__` to a name bound to `this_os`, the switch or `fs`, or uses `contextvars` outside the path model and the switch.

This scan has had two review rounds:
- **S0128 round 1, V-F1 (Medium, class c):** the first guard checked names only, and four reflection shapes got past it. The fix moved the switch into its own module and widened the scan.
- **S0128 round 2, V2-F2 (Low, class c):** six more shapes get past it. This is BL-0286.

What I checked today:
- **All six shapes get past the scan, and all six reach `ON` at run time** in any process that has imported `this_os`. I ran each one against `switch_uses` and then in the engine's interpreter. The six: `getattr` with a dotted module path as its first argument, `object.__getattribute__`, `operator.attrgetter`, `sys.modules[...]`, a walk of `gc.get_objects()`, and `inspect.getmembers`. (The row says two of the six are "live". I could not reproduce that split: all six reach the variable.)
- **A seventh shape also gets past it:** `this_os.supported.__globals__["under_test"]`. That makes three rounds that each found new shapes. The S0128 retro already concluded that a name-ban scan of this kind cannot be complete.
- **No product module uses any of these shapes today.** The engine's only `gc` uses are `gc.disable()` and `gc.enable()` around the save and the jobs. Nothing in the backend or `scripts/` names the switch.
- **A behaviour test backs the scan up:** `tests/fs/test_windows.py::test_the_product_on_windows_still_refuses_file_operations_and_never_loads_the_adapter`. It starts a fresh engine process on the Windows runner and requires that the port and the legacy locks are refused, `supported()` is False, and the Windows adapter module was never imported. It proves the product's real paths don't select the adapter. It cannot prove that no code anywhere could flip the switch.
- **The switch ends with BL-0087.** BL-0087's row and the S0128 journal say it "turns it on by selecting the adapter for good". At that point `under_test.py`, its fixture and `switch_uses` are deleted, and with them every shape in this row.

## Likelihood
Very low, and the exposure lasts only until BL-0087, which is next after the pre-BL-0087 rows. Two kinds of code could flip the switch:
- **Our own product code.** It would have to reach the variable through deliberate reflection, with a name assembled to dodge the scan. Every slice is reviewed by a verifier.
- **A user's Beancount plugin.** It runs inside the engine process, and the scan never covered plugins. A plugin can already read and write any file the user can, so the switch gives it nothing new.

v0.4 isn't released, so nobody runs the engine on Windows yet. If the switch were flipped, the product would use the same Windows adapter that BL-0087 will select, just before BL-0087's must-fix lines are done. That is a wrong availability state that fails closed, not lost bytes. It is class (c), as the verifier said.

## Options (cheapest first)
1. **Stated limit; BL-0286 closes when BL-0087 removes the switch.** Add one "Stated limit" line to `switch_uses` saying a name scan cannot see every reflection path (the seven shapes as examples), and that the fresh-process runner test is the behavioural check. Then move BL-0286 to "moves with BL-0087 (IMP-0070)", and add a line to BL-0087: "delete `fs/under_test.py`, its fixture and `switch_uses`". This is the treatment IMP-0066 gives a missed shape in a check (a stated limit), and the review budget's "Tooling, gates" row needs no fresh round, since this is not a guard of real data or privacy. Cost: a docstring line and two row edits, about 10 minutes.
2. **Widen the scan for the seven shapes.** Add each shape as a refused pattern, with must-not-pass cases. Cost: about 30 lines in the test and one verifier round. This is the third round on a mechanism that has found new shapes every round. Others are already easy to name (`type(m).__getattribute__`, `functools.reduce(getattr, ...)`, `sys._getframe().f_globals`), so I expect a fourth round to find more. Not recommended.
3. **Structural: no switch in the product's source.** `this_os` selects from a table of adapters keyed by platform, read on each call, with `darwin` and `linux` in it and no `win32`. On Windows, the `windows_port` fixture imports the Windows adapter from test code and puts it into the table with pytest's `monkeypatch`. `under_test.py`, `switch_uses` and its 15 cases are deleted. The existing checks then carry the guarantee:
   - only `this_os` imports an adapter module (an AST import scan, which reflection cannot hide from);
   - dynamic import by name is allowed only in `ledger/beancount_port.py` (`check_engine_boundary.py`);
   - the fresh-process test shows that the adapter module is never loaded in the product, so `sys.modules`, `gc` and reflection have nothing to find.

   The one residual: a ledger's `plugin "ledger_engine.fs.windows"` line would import the module through Beancount's plugin loader. Something would still have to put it into the table, and a plugin is arbitrary code anyway.

   Cost: a small product change at the selection point (net code removed), one verifier round, a fresh round if a fix touches product code, and one manual CI run for `engine-windows`. Then BL-0087 adds `win32` to the table and deletes the fixture's three lines. This is the S0128 retro's "cheaper gate" ("keep the mechanism out of the product's source tree"), applied after the fact.

## Recommendation
**Option 1.** The thing the guard protects lives only until BL-0087, which comes next. The risk is our own deliberately obfuscated code, or a plugin that can already do worse. A behaviour test on the runner already holds the product's real paths. Option 3 is the clean design and the right shape for the next test-only switch, but here it would spend a slice, a verifier round and a Windows CI run on code BL-0087 deletes days later. If you'd rather have no window at all, choose option 3, not option 2.

Review history of the mechanism (the switch and `switch_uses`): S0128 round 1, V-F1, Medium, class c, fixed (its own module, a wider scan, 11 must-not-pass cases; loosening mutants `guard-no-reflection`, `guard-contextvars-anywhere`, `guard-dict-access`, `switch-selected-without-the-context` all killed). S0128 round 2, V2-F2, Low, class c, became BL-0286. No High on this mechanism.

**A related point for you to decide separately.** The path model has the same pattern, and it stays after BL-0087: `fs/pathmodel.simulating` is a test-only context variable in the product's source, guarded by a name scan (`model_switches`, same test file). I checked that `sys.modules["ledger_engine.fs." + "pathmodel"].__dict__["_cur" + "rent"]` gets past that scan too. If one were flipped, the product would split and compare paths by another OS's rules. My suggestion: a backlog row (class c, M1b exit round) offering it the same choice (a stated limit, or option 3's structure, with the tests installing the simulated model from test code). Say if you want that row.

## Answer (owner, verbatim, with date)
Owner, 2026-11-21, in session: "option 1"

**Decision: option 1.** A stated limit in `switch_uses` (the seven shapes, the runner's fresh-process test as the behavioural check), with no third round on the scan. BL-0286 moves with BL-0087 (IMP-0070), whose row now says that turning writes on deletes the switch, its fixture and the scan. The path model's `simulating` question was not answered, and no row was opened for it.
