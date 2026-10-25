# S0025: Tests stub components by import, not by name (L9.M-7)

## Brief
- Goal: close review finding L9.M-7 ("9 test files stub components by name; renames break silently"; review:L9-verif M-7: "prefer `findComponent` by import over string stubs") before any component test exists: a lint ban in test files on name-keyed `stubs` (mount options, quoted keys, the global config, computed keys) and on `findComponent`/`findAllComponents` by `{ name }`; stubbing goes through `vi.mock` of the module and lookups through the imported component.
- Implements: L9.M-7 (M0, review:27 §1(c)); EP-30 (refuse the shape instead of checking each stub's name).
- Capabilities: none. Invariants touched: none (the INV-07 bans are repeated in the test-file block so they still apply there).
- Risk class / lane: Low (lint rule, test-only); one verifier round.
- Acceptance tests: `frontend/tests/unit/gates.test.ts` "[L9.M-7]": six refusals, one must-pass (stub and find by import, find by `ref`), INV-07 still enforced in tests, app code untouched.
- Expected doc impact: `frontend/eslint.config.js` comment; backlog L9.M-7 done; roadmap §1; this journal.
- Open questions: none. Base SHA: 33186026.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Ways around it, each tested: a quoted `'stubs'` key; `config.global.stubs`; `config.global['stubs']`; `findAllComponents`; scoping the block to no files; losing INV-07 in tests by the later block replacing the rule's options.
- Self-mutation (by hand: the mutants runner cannot link `node_modules`, S0018): 5 tried (each selector removed; the find pattern broken; the block's files emptied), 5 killed.

## Verifier
- Round 1, head 21f7218: PASS WITH FINDINGS. Attacks: 7 (every VTU 2.5.1 finder, stub key forms, other VTU paths, INV-06/07 still in tests, app code untouched, selector mutants by inspection, glob coverage).
- F1 Medium: `getComponent({ name })` passed. Fixed: the finder pattern covers `find|get` and `All`.
- F2 Low: a quoted `'name'` key passed. Fixed. F3 Low: template-literal `stubs` keys passed. Fixed; deliberate indirection is stated as left to review. F4 Low: `testFiles` no longer exported. D1: roadmap wording fixed.
- Low slice, no High: no further round (ADR-0058).

## Retro
- I wrote the brief's key-form list and then forgot to apply it to the second selector; listing each selector against each form (a small matrix) is the cheaper check.
- Process cost: about 20 min building; verifier 2 min; fixes 10 min.
