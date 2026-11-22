# S0143: Two stated limits (BL-0300, BL-0301)

## Brief
- Base: `0deadcbe6d7a3dfb590e7f0d24e841e23b768f39` (`next`).
- Goal: state two fail-closed behaviours as limits, as the owner decided (owner, 2026-11-21, in session: "I'm accepting the inbox note as written"; `design/inbox/answered/20261121-pace-and-scope.md`, change A; refuse first, IMP-0075): BL-0300 in `design/specs/mirror/publish.md` §2 "The folder itself" and §11 "Limit (a linked file in the mirror folder)"; BL-0301 in `design/specs/engine-jobs.md` §8.2. No tests (the owner dropped BL-0300's tests; BL-0301 is a limit, not a change).
- Implements: BL-0300, BL-0301 (closed here). ADR-0015 amendment of 2026-11-18 and the owner's "Never clear a linked file (Recommended)" (2026-11-19), unchanged.
- Capabilities: none. Invariants touched: none (INV-19: the docs follow the code).
- Risk class / lane: Low / light. Review row: "Docs only", the verifier on facts and doc consistency; no fresh round.
- The bytes it protects: none (docs only; both behaviours already fail closed).
- Which real setup needs this, and can a policy forbid the rest? A linked read-only file in the mirror folder needs a program writing in the app's cache folder; backslash paths are a natural Windows spelling. The policy is the owner's: refuse and state it. Has this family needed two or more fix rounds before? The read-only clear (S0132) had two; this slice changes no code of it.
- Acceptance: each statement checked against the code at the base (below); `check_invariants.py` and `run_meta_tests.py` green.
- Expected doc impact: the two spec sections, BL-0300 and BL-0301 to done, the roadmap's M1b line.
- Open questions: none (see the BL-0301 finding in Decisions: wider than the row said, same decision).

## Decisions
- BL-0300, checked at the base (agent): `publish.open_folder` calls `parent.clear_readonly(FOLDER)` on a non-folder at `mirror/` before `remove`; `fs/windows.py` `clear_readonly` raises `OSError(EMLINK)` for a read-only non-folder with more than one link; `opening.run` and `rebuild.run` catch only `CacheOnShare` around `open_folder`, and `protocol/opening.py`, `protocol/rebuild.py` say any `OSError` reaches the server as `engine.internal`. `read_published` answers None when `mirror/` is not a folder, so saves are refused `mirror_stale`. `_remove_tree` calls `clear_readonly` on each file before `remove`; the raise leaves the rest of that folder and its parents, and `clean` counts that top entry as one failed. POSIX `clear_readonly` does nothing (`fs/posix.py`), so the limit is Windows only. Stated in both places.
- BL-0301, checked at the base (agent) with a scratch probe of `copy_layout.rewrite` under `pathmodel.simulating(WINDOWS)` (outside the repo): an absolute `include "C:\\O\\x.beancount"` that must move refuses (line 0); `C:/O/x.beancount` is rewritten to `outside/x.beancount`; a relative `include "sub\\y.beancount"` alone is left (None); the same line beside a moved include refuses; a `document` line with `d\\r.pdf` (not moved) refuses at its line; `d/r.pdf` is left; `C:\\L\\d\\r.pdf` refuses; `C:/L/d/r.pdf` becomes `d/r.pdf`. So the refusal is **wider than the brief and the row said** ("a moved line"): every `document` line is matched by `_DOCUMENT` (`rewrite` loops over all of `parsed.document_lines`), so one with `\\` refuses whether or not it moves; and once any `include` or `option "documents"` line of a file is rewritten, every such line in it must match its shape, so an unmoved one with `\\` refuses too. The spec states the code's actual scope. It is the same decision (refuse, nothing written, `/` works), only reached more often by a Windows user who writes `document` paths with `\\`; raised to the owner in the hand-off report, not escalated (it fails closed and the option chosen does not change).
- The reason the user sees: `cannot_rewrite`, `msg:copy.cannot_rewrite`, text from `backend/src/app/platform/engine_jobs/copy_text.py`: "Finzytrack could not change a line that names another file in <file> (line <n>)." (the line only for a `document` refusal). The rewrites run in `prepare` (step 2, before step 1 makes anything: `create.run_steps`), so nothing is written.
- The limit also holds on macOS and Linux for a path holding a backslash (the shapes are the same on every OS); stated in one clause.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks that accept, tag or refuse: none added or changed (docs only). No mutants (no check). Population claims: none.
- Gates at the head: `check_invariants.py` exit 0 (silent); `run_meta_tests.py` OK, 1012 tests in 40 modules, 0 failures.

## Verifier
- Round 1 at 29823c68 (docs only: facts and doc consistency): PASS WITH FINDINGS, no High, no class (a) or (b). Every statement of publish.md §2 and §11 matches the code; in engine-jobs §8.2 all but one parenthetical do.

| ID | Severity | Class | Finding | Outcome |
|---|---|---|---|---|
| F-1 | Low | d | §8.2's parenthetical said an unmoved `\\` include refuses when any other line of its file is rewritten; only a rewritten `include` or `option "documents"` line triggers it (a rewritten `document` line leaves it as it is) | Fixed in the spec and BL-0301's row (docs only, IMP-0075) |
| F-2 | Low | d | The roadmap sentence read as if BL-0300 and BL-0301 moved to M11 | Fixed: the roadmap's M1b paragraph rewritten (updated 2026-11-22) |

- Question (no finding): the swap's `<name>.<tag>.tmp` also goes through `_remove_tree`; a linked read-only file there is not placeable in advance (the build's random tag).
- Process note: the implementer's pushes ran inside the sandbox (a command around the push keeps it sandboxed, so the hook's `mktemp`, `ps` and `diskutil` were refused); the orchestrator pushed 29823c68 with the bare `git push origin next`, whole gate green.

## Retro
Build about 25 min (implementer's estimate), fix about 5 min, 1 verifier round, findings 0H/0M/2L. Cheaper gate: when stating a limit, run the refusal's code on each shape the limit names (the verifier's repro found F-1 in one case). Process checks: none fired.
