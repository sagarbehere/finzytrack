# S0106: Measurements (M1a item e)

## Brief
- Goal: the owner's M1a item e (owner, 2026-11-10, in session: "e. Measurements: BL-0082, BL-0114, then the ADR-0058 re-measurement."). Base commit `8fb3002e`. Four pieces, in this order:
  1. **BL-0235: the measurement scripts work again, and cannot rot silently.** Run on a small ledger at `8fb3002e`: `engine/scripts/measure_mirror_build.py` crashes (`Stamp` lacks `published_by`, since S0088); `engine/scripts/measure_save.py` crashes (`SavePlan` has no `mirror` since S0087; it lacks `app_cache` and `app_folder`; `rebuild.run` needs its `Target`; `CountingDir.sync_file` drops the identity the restore needs since S0101); `backend/scripts/measure_mirror_reads.py` crashes on a mirror with fewer than 1,000 postings. Root cause: `scripts/` is outside pyright's `include` in both packages, so no gate reads these files; `pyright scripts` finds 11 errors in the engine's and 8 in the backend's (`gen_engine_job_texts.py` included). Fix: every script fixed; `scripts` joins pyright's `include` in `engine/` and `backend/` (a tightening, IMPROVEMENTS.md); a smoke test per package runs every `measure_*.py` on a tiny ledger and checks that the set of scripts it runs is exactly the set in the folder (a new script without a case fails). `measure_save.py` measures the save as the product runs it now: the open's publish first, then the preview's load, the save, the rebuild with its publish (BL-0112's "publish still to come"), the restore.
  2. **BL-0082: the protocol's cost per write and per large result.** A new `backend/scripts/measure_protocol.py` runs the sidecar's own engine command (`app.main.engine_command`) in an isolated app folder: `ping` round trips; a real `save` over the protocol (after the `open` op published the mirror; the plan's fingerprint read from the published `meta`), timed from the client with its gate and committed events; and the round trip of a save request the engine refuses right after decoding it, with the whole 54k file and with a tiny file, which isolates the hop's size-dependent cost. Then a new `engine/scripts/measure_large_result.py`: a result the size of every posting of the ledger (the mirror's `postings` rows, the widest BQL result), encoded as a reply line: its bytes against `MAX_LINE_BYTES`, the encode and decode time, and the decode's peak memory, replacing the row's estimates. No BQL op exists (CAP-ENGINE-BQL, M7), so the engine's own refusal of an oversized reply (`engine.result_too_large`) is not built here: a guard on every reply would turn a write op's oversized "saved" into a refusal, a false outcome, so it is designed with the op that can produce one. Moving that part to M7 needs the owner's words (inbox, not blocking).
  3. **BL-0114: `/health` while the sidecar does read work of its own during a 54k load.** No route serves the mirror (BL-0126, M2, behind BL-0115). `measure_health.py` gains a mode where the sidecar process itself (the real `app.main.main`) runs mirror reads and the QueryRunner on worker threads during the load, as the reads will run behind a route (ARCHITECTURE.md §2.5); the route's own JSON encoding on the event loop is not in it (stated limit; BL-0126 already carries the measurement with the route).
  4. **The ADR-0058 re-measurement** (decision 5): product code against process code in lines, by the categories of ADR-0058's context, and per slice the building time against the review-and-fix time, from the journals' retro lines, with the count of journals parsed and not parsed.
- Every number: the machine, the load average at the time, alternating A/B pairs where two things are compared, the method, and where the raw data is (the session scratchpad; not committed: it holds paths).
- A user-facing absolute broken (PROCESS.md §6: a write over 1 s at demo size or over 5 s at 54k entries) goes to the owner with the numbers (inbox), never tuned away.
- Implements: ADR-0054 Consequences (the measurement), ADR-0058 decision 5, roadmap M1a exit ("`/health` answers under 100 ms during a 54k load; the protocol overhead per write and per large BQL result measured"), PROCESS.md §6 ("Safety outranks every number").
- The bytes it protects: none. No product code changes; the scripts run only on generated ledgers in scratch folders.
- Capabilities: none (tooling and measurement).
- Invariants touched: none in rule; INV-20's absolutes and INV-10 are measured, not changed.
- Risk class / lane: Medium / light. Tooling and measurement with no ledger or user data; widening pyright's scope is a gate tightening.
- Acceptance tests (tests first, failing on `8fb3002e`): `engine/tests/test_measure_scripts.py` (each engine `measure_*.py` runs on a tiny ledger and prints its report; the cases equal the folder), `backend/tests/test_measure_scripts.py` (the same for the backend's, the mirror built by the engine's script in its own environment); `pyright` over `scripts/` in both packages (green).
- Expected doc impact: the backlog rows (BL-0235 done; BL-0082 measured, its guard part to the owner; BL-0114 done with numbers, the route-level run on BL-0126; BL-0112 and BL-0205 updated with the new numbers where measured); the roadmap's M1a lines; `design/specs/engine-jobs.md`'s "Limit (speed)" if the save's numbers change; ARCHITECTURE.md §2.0's "not built yet" line on the protocol-overhead measurement; `engine/CLAUDE.md` and backend pointers for the new scripts; IMPROVEMENTS.md (pyright scope). No user docs, no generated docs, no contract change.
- Open questions: whether BL-0082's reply-size guard moves to M7 with the BQL op (inbox; does not block this slice).

## Decisions
- **The root cause is the type gate's scope** (agent): pyright never read `scripts/` in either package, so each signature change since S0087 broke a script unseen. `scripts` joins `include` in both `pyrightconfig.json` files (IMP-0064, a tightening); the backend's pin (`tests/architecture/test_gates.py`) moves with it and its strictness scan now reads every included folder; the engine's config had no pin, so `engine/tests/architecture/test_type_gate.py` adds one (same shape). The smoke tests add what types cannot see: a script that runs but fails (as `measure_mirror_reads.py` did on a small mirror).
- **Fixed as found by running each script** (agent): `measure_mirror_build.py` (`Stamp.published_by`, "open": its load is the open's); `measure_save.py` (no `mirror` field; `app_cache`, `app_folder`; the open's publish first, which the gate needs since S0087; `rebuild.run`'s `Target`; `CountingDir.sync_file` **and** `CountingDir.create` returned None, dropping the identity both return since S0101; `open_child` counted too); `measure_mirror_reads.py` (a mirror with fewer than 1,000 postings); `gen_engine_job_texts.py` (six reads of private text helpers, marked as the tests mark them; its output is unchanged, `check_generated.py` green). No clock in the engine's scripts either (the ruff ban covers them): `measure_save.py` takes fixed dates and its `now_ns` from a file the disk stamps, as the tests do.
- **`measure_health.py` refuses to report when a load failed** (agent): it ignored `bean-check`'s exit code, so a failing load would have been counted as a load.
- **BL-0082's write cost is measured with a real save over the protocol** (agent): the backend has no preview op yet (M1b), so the plan's fingerprint is read from the published mirror's `meta` (the open's stamp); for a change to one file whose includes stay the same it equals the plan's (`as_before`), and the engine accepted it in every run. The hop alone is isolated by a save the engine refuses right after decoding it (an unreadable fingerprint), sent with the whole file and with one line.
- **BL-0082's reply-size refusal is not built; the row moves to M7** (owner, 2026-11-12, in session, relayed by the orchestrator: "Move to M7 with BQL (Recommended)"; the option as put to the owner: "The numbers are measured and meet M1a's exit item; the refusal and paging are one design built with the op they protect. The row moves to M7."; `design/inbox/answered/20261112-large-reply-guard.md`): no op returns rows before CAP-ENGINE-BQL (M7), and a guard on every reply would turn a write op's oversized "saved" into a refusal (a false outcome).
- **BL-0114 is closed with numbers measured without a route; the route-side measurement moves to M2 with BL-0126** (owner, 2026-11-12, in session, relayed by the orchestrator: "Rest moves to M2 (Recommended)"; the option as put to the owner: "Close the measured part in M1a; the route-side measurement moves to M2 with BL-0126, which must also keep response encoding off the event loop (the 94 ms finding goes on its row)."). Agent's reasoning, kept: the risk the row names is the sidecar's own read work competing with `/health` (worker threads, the interpreter lock); the reads now run inside the real sidecar process. A route adds its request and its answer's JSON on the event loop; BL-0126 (M2) already carries the measurement with that route, reworded to say so. The owner may read this as needing the route; the numbers (5 ms against 100 ms) leave a wide margin.
- **No user-facing absolute is broken** (agent): on the product's path (verifier F1: a fresh engine for the save and for the rebuild), from sending the save to the new mirror generation 3.10-3.13 s at 54k (ADR-0008 line 47: the 5 s includes the mirror update), 0.61 s at demo size (1 s), a margin of about a third there; the verifier measured 0.63-0.75 s, and 0.91 s once under heavy load. Nothing escalated for speed; the demo margin is stated on the roadmap, BL-0112 and the spec.
- **BL-0205 (a copy's peak memory) is not measured here** (agent): its row allows BL-0082 or the ADR-0058 re-measurement, but it measures the copy job (M3) and neither of those runs a copy. It stays open in M3, unchanged.
- **The ADR-0058 re-measurement script stays in the session scratchpad** (agent): a one-off measurement whose method is written out below; committing it would add a script under `scripts/` that needs its own meta-test (PROCESS.md §9 asks for evidence first).
- Doc impact as briefed: backlog rows BL-0235 (done), BL-0082 (measured, open), BL-0114 (done), BL-0112 (the numbers with the publish), BL-0126 (wording); roadmap §1; `design/specs/engine-jobs.md` "Limit (speed)"; ARCHITECTURE.md §2.0 "Not built yet" (also corrected: mirror build and writes are protocol ops since S0063-S0088); `engine/CLAUDE.md`, `backend/CLAUDE.md`; IMPROVEMENTS.md IMP-0064. ADR-0008's own "Speed at 54k" amendment text is history and is not edited (ADRs are append-only); the spec carries the current numbers.

## Measurements
- **Machine:** the owner's Mac (Mac16,8, Apple M4 Pro, 14 cores, 48 GiB), macOS 27.2, APFS (the system volume's temp folder), Python 3.13.16. Shared and loaded: the 1-minute load average is logged before every run (`sysctl vm.loadavg`); it ranged 5 to 50 over the session.
- **Ledgers:** generated at measurement time by `backend/scripts/gen_demo.py --anchor 2026-03-31` (`demo`: 5,581 entries, one 0.9 MB file; `54k`: 54,018 entries, 108,241 postings, one 8.7 MB file), in the session scratchpad; copies only, every run on a fresh copy.
- **Method:** alternating A/B pairs from one driver script (A then B, then the next pair), so a load swing hits both sides. Raw output: the session scratchpad's `raw/` folder (`save-demo.txt`, `save-54k.txt`, `health.txt`, `build.txt`, `result.txt`, `reads54.json`, `remeasure.txt`; not committed: they hold scratch paths). Times below are min-max over the pairs.
- **Save, A/B (5 pairs per size; A = `engine/scripts/measure_save.py --runs 1`, in the engine's process; B = `backend/scripts/measure_protocol.py --runs 1`, through the protocol from the sidecar's client).** Round 2, load 6.7-15.7 (a first round, load 5-50, without the rebuild in B, is kept in `raw/round1/`; it agrees except two 54k B runs, 180 and 413 ms, in which the hop alone was slow too, 68 and 146 ms):

  | | demo | 54k |
  |---|---|---|
  | A: save inside the engine | 62-66 ms | 98-101 ms |
  | A: of which the durable order's folder syncs (11) | 43-46 ms | 45-49 ms |
  | A: open (load, build, publish) | 0.28-0.29 s | 2.48-2.51 s |
  | A: preview's load (before the confirm) | 0.13 s | 1.47-1.49 s |
  | A: rebuild with publish, inside the engine | 0.27 s | 2.95-3.00 s |
  | A: restore (recovery's undo) | 0.03 s | 0.04-0.05 s |
  | B: request line | 0.94 MB | 9.14 MB |
  | B: client's encode of it | 1.6-1.7 ms | 14.6-15.4 ms |
  | B: save through the protocol (send to reply) | 77.8-85.3 ms | 125.5-132.0 ms |
  | B: send to gate / gate to committed / committed to reply | 5.5-5.9 / 71.7-79.3 / 0.4-0.5 ms | 34.8-37.3 / 89.8-95.5 / 0.4-0.5 ms |
  | B: a save refused right after decoding, whole file (the hop alone) | 3.4-3.5 ms | 27.5-29.7 ms |
  | B: the same with a one-line file | 0.26-0.29 ms | 0.32-0.38 ms |
  | B: `ping` round trip, median of 50 | 0.03-0.05 ms | 0.03-0.04 ms |
  | B: rebuild with publish, through the protocol | 282-287 ms | 2.75-2.82 s |
  | **B: from sending the save to the new generation published** | **0.36-0.37 s** | **2.88-2.94 s** |

  **These B figures are not the product's path** (verifier F1): B pinged a fresh engine before the save and ran the rebuild in the save's engine, so neither engine start was timed, and the rebuild reused the save's imports. The product's path, measured after the fix (`measure_protocol.py`, default; against `--warm`, which starts each engine's process before its time but leaves its imports in; 5 alternating pairs, load 6.2-13.5, raw `raw/path-demo.txt`, `raw/path-54k.txt`):

  | | demo: product / warm | 54k: product / warm |
  |---|---|---|
  | Save, send to reply | 197-202 / 75-85 ms | 247-257 / 126-141 ms |
  | Send to gate | 125-127 / 5.6-6.0 ms | 158-160 / 36-38 ms |
  | Rebuild with publish | 408-414 / 408-415 ms | 2.85-2.88 / 2.84-2.87 s |
  | **Save to new generation published** | **0.61 / 0.49-0.50 s** | **3.10-3.13 / 2.97-3.01 s** |
  | The hop alone (decode-only refusal, whole file) | 3.4-3.6 ms | 28-30 ms |

  Each fresh engine costs about 0.12 s (the save's: product less warm). The verifier's product-path figures (its pairs, heavier load): demo 0.63-0.75 s (0.91 s once), 54k 3.30 s.

  The protocol's cost per write: 25-31 ms at 54k (B's save less A's), matching the hop alone (27.5-29.7 ms, of which the client's encode is half); 12-19 ms at demo size. ADR-0054 estimated "well under 10 ms" for "a few kB": the plan carries the whole file (BL-0106), so the request is the file's size. Against the absolutes: 2.88-2.94 s of 5 s at 54k, 0.36-0.37 s of 1 s at demo size.
- **A query result on the protocol (`engine/scripts/measure_large_result.py --runs 5`, load 5.0):** one row per posting, encoded as a reply line, decoded as the client decodes. 54k: all 29 mirror columns 79.47 MB (474% of `MAX_LINE_BYTES`, 16.78 MB; 81.85 MB, 488%, on a 54k ledger generated with the anchor 2026-11-12: the size depends on the generator's dates), encode 160 ms, decode 196 ms, 262 MB of Python objects; 7 columns 9.43 MB (56%), encode 35 ms, decode 52 ms, 57 MB. Demo: 8.19 MB (49%) and 0.98 MB.
- **`/health` (`backend/scripts/measure_health.py --size 54k --loads 3`, 3 pairs; A without reads, B with `--reads-mirror` of the 54k mirror and 4 read threads; load 5.6-7.0), milliseconds:**

  | | idle max | reads only: median / p99 / max | during 3 loads: median / p99 / max | reads done (alone / during loads) |
  |---|---|---|---|---|
  | A1 | 0.92 | - | 0.61 / 0.90 / 1.95 | - |
  | B1 | 0.82 | 1.10 / 4.56 / 23.21 | 1.55 / 4.94 / 5.31 | 233 / 132 |
  | A2 | 7.44 | - | 0.73 / 0.95 / 2.19 | - |
  | B2 | 1.72 | 1.07 / 3.19 / 10.45 | 1.22 / 3.40 / 4.34 | 224 / 140 |
  | A3 | 6.43 | - | 0.47 / 0.82 / 7.06 | - |
  | B3 | 10.71 | 1.44 / 11.69 / 16.37 | 1.22 / 3.62 / 4.53 | 222 / 140 |

  Each read is one of: `Reader.info`, `Reader.ledger_errors(200)`, monthly expenses by account, `account_balances`, every posting (capped at 10,000 rows by the QueryRunner). The loads took 1.49-1.59 s (A) and 1.71-1.83 s (B). Against the M1a exit's 100 ms: at most 23 ms in these runs. The verifier's own runs spread wider: with the reads, at most 4.75-31.6 ms during the loads and 6.2-18.7 ms with the reads alone. Under 100 ms either way.
- **Mirror build at 54k (`measure_mirror_build.py`, 3 runs, load 5.0-5.5):** load 1.39-1.41 s, rows 0.90 s, build (rows and write) 1.11-1.14 s, file 77.2 MB, peak RSS 317 MB. **Mirror reads at 54k** (`measure_mirror_reads.py --runs 5`, load 7.2): `Reader.info` 0.24 ms, typical queries 12-33 ms (the S0052 figures hold).

## ADR-0058 re-measurement (decision 5, at the end of M1a)
Method: the scratchpad's `remeasure.py` at `8fb3002e` (M1a's state before this slice). Lines are physical lines of tracked code files (`.py .ts .tsx .vue .js .mjs .cjs .rs .sh .tla .cfg`, and the hook files), by folder, in ADR-0058's categories. Times come from each journal's Retro section: the first "Build ... N min/h" (or "implementation", "skeleton", "drafting", "writing" about N) and "fix ... N min/h"; read by hand where the patterns miss or a retro states several spans: S0010 ("shell draft and lock work about 1 h 15 min", fixes 35 min), S0031 ("one implementer pass (about 3 h)", fixes 1.5 h + 1 h), S0073 (29 + 17 min, fixes 11 min), S0050 (fix 25 + 15), S0054 (fix 80 + 45 + 15), S0061 (fix 2.3 h + 9.4 h). S0029 and S0030 state only a total, S0033 and S0049 only a fix time: not counted. They are the implementers' own estimates, with or without the review rounds' wall time as each retro says.
- **Product code against process code** (ADR-0058's context after S0007: product none, process about 3,600 lines, oracle harness about 2,600):

  | Category | Lines | Files |
  |---|---|---|
  | Product code (`backend/src`, `engine/src`, `frontend/src`, `shell/src-tauri/src`) | 28,978 | 132 |
  | Product tests (`backend/tests`, `engine/tests`, `frontend/tests`, `frontend/e2e`) | 63,012 | 181 |
  | Process code (`scripts/` but the oracle, `.githooks`, `.claude/hooks`, `.github`) | 22,080 | 77 |
  | of which the check scripts (`scripts/*.py`) | 8,004 | 25 |
  | of which their meta-tests (`scripts/tests`, every tracked file) | about 12,700 | |
  | Product-safety tooling (`scripts/oracle`) | 6,751 | 25 |
  | Formal models and their trace tools (`formal/`) | 5,739 | 106 |
  | Product tooling (package scripts, `shell/` build, engine stubs) | 2,473 | 16 |

  Process code is 0.76 times the product code (it was all there was at M0). Of the 8,004 lines of check scripts, about 4,700 enforce product rules or guard data (the concept counters, money and dates, the engine boundary, the write corpus, the mirror surface, the failure modes, the privacy scan, the staged-file guard, the licence and Cargo checks, two generators) and about 3,300 check the process itself (invariants, capabilities, retired terms, deferrals, the push gate, the gate timer, the meta-test runner, generated files, the mutation runner, the orient helper).
- **Building time against review-and-fix time per slice:** 106 journals at `8fb3002e`; 71 state a building time (35 do not: S0000, S0001, S0004, most of S0014-S0053, and the two-implementer slices S0067-S0071); 66 state both.

  | | Slices with both | Build median | Fix median | Fix total / build total |
  |---|---|---|---|---|
  | M0 (S0000-S0034) | 18 | 50 min | 25 min | 615 / 1,070 min = 0.57 |
  | M1a (S0035-S0105) | 48 | 54 min | 33.5 min | 3,302 / 4,270 min = 0.77 |
  | M1a without S0061 (8 review rounds) | 47 | 53 min | 32 min | 2,600 / 4,193 min = 0.62 |

  Plainly: in M1a a typical slice took about an hour to build and about half an hour more to fix what review found; the write-path slices with formal models took the most fixing (S0061, S0080, S0081, S0057).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
(Round 1 fixes: the smoke tests are parametrized over every case, and `test_a_script_that_fails_fails_its_case` in both packages is the planted failing script; the protocol case runs the product's path and `--warm`.)
- Checks that accept, tag or refuse, each with its must-not-pass test:
  - `engine/tests/test_measure_scripts.py::test_every_measurement_script_has_a_case` and the backend's twin: refuse a `measure_*.py` without a case. Must not pass: a planted `measure_zz.py` in each folder made each fail (by hand; planted files removed).
  - The smoke cases (each script exits 0 and prints its report's parts): on `8fb3002e` the engine's `measure_save` and `measure_mirror_build` cases and every backend case fail (`measure_mirror_reads` on the small mirror, `measure_health` without the reads option, `measure_protocol` without the script), and both inventory tests.
  - `engine/tests/architecture/test_type_gate.py::test_pyright_config_is_exactly_the_strict_one`: refuses any other config; fails on `8fb3002e`'s config (no `scripts`). `test_strictness_comment_check_sees_a_planted_one` is its planted case. `backend/tests/architecture/test_gates.py`: the pin moved to the wider `include`, its strictness scan reads every included folder.
  - In the scripts (measurement guards, not product checks): `measure_save.py` refuses to report unless the open published, the save landed, the rebuild loaded and published, the restore ended `Resolved` and the bytes are back; `measure_protocol.py` unless the save landed, the rebuild published and the decode-only save was refused `protocol.invalid_params`; `measure_health.py` when a `bean-check` load failed. No test plants a failing load for `measure_health.py`; a run would print nothing, which the smoke test would catch only if every load failed.
- Self-mutation: not run (optional on tooling; no check here guards ledger data, finance results, real-data privacy or product security).
- Population: the measurement scripts, found by behaviour (every tracked script under `scripts/`, `engine/scripts/`, `backend/scripts/`, `frontend/scripts/` and `shell/` that reads a timer): 4 measurement scripts at `8fb3002e` plus 2 new, all 6 covered; the other 9 that read a timer are gate tooling with their own meta-tests (`gate_timer.py`, `run_meta_tests.py`, the acceptance launcher, the oracle harness and its launcher, and their tests).

## Verifier
- Round 1, head 74ead312: PASS WITH FINDINGS, 0H/3M/3L. No invariant broken; the 1 s and 5 s absolutes held in every run. No further round (tooling and measurement, no High; ADR-0058).
- F1 Medium: the published save times left out the product's two engine starts (the open and a landed save each retire their engine, `ran_user_code=True`, `engine_jobs/save.py` `_answered`). Fixed: `measure_protocol.py` measures the product's path by default (the retire and the engine-lock wait inside the save's time, a retire before the rebuild), `--warm` kept; pairs rerun; roadmap §1, BL-0112, BL-0082 and the engine-jobs "Limit (speed)" carry the product's figures and the demo margin of about a third.
- F2 Medium: the smoke tests were not default-deny (a case without its own test function never ran). Fixed: one test parametrized over `CASES` in each package, and a planted script that exits 1 fails `run` (`test_a_script_that_fails_fails_its_case`).
- F3 Medium: BL-0114 closed without the route-side measurement. Resolved by the owner ("Rest moves to M2 (Recommended)", 2026-11-12): recorded on BL-0114 and BL-0126, with the verifier's attack 10 (FastAPI 0.143 serializes a response on the event loop; one capped `SELECT * FROM postings` result, 10,000 rows, took 80 ms in `jsonable_encoder` plus 14 ms in `json.dumps`) on BL-0126.
- F4 Low: "79.5 MB" said to be 79.04 MB reproducibly. Not adopted as stated: the script gives 79.47 MB on the anchor-2026-03-31 ledger and 81.85 MB on the verifier's anchor-2026-11-12 ledger (rerun in this round); 79.04 MB was not reproducible with it. The size depends on the generator's dates, so BL-0082, the answered inbox note and the journal give 79.5-81.9 MB (4.7-4.9 times `MAX_LINE_BYTES`), the roadmap "about 80 MB".
- F5 Low: the time parser missed retros. S0002-S0009 were already counted by the widened parser; S0010, S0031 and S0073 are now read by hand, and the method names them. M0 0.57 over 18 slices (the verifier estimated about 0.60), M1a 0.77 (0.62 without S0061); 71 of 106 journals state a building time, 35 do not.
- F6 Low: `/health`'s spread reported from both sets of runs: with the reads, at most 4.3-31.6 ms during the loads and 6.2-23.2 ms with the reads alone (BL-0114, the journal, the roadmap's "at most 32 ms").

## Retro
- Build about 150 min, fix about 60 min (mostly waiting for another session's load to fall below 15 before rerunning), 1 verifier round, findings 0H/3M/3L. Cheaper gate: measure the user-facing path end to end through the scheduler's own rules (which engine each job gets) before publishing a number, and make every inventory test parametrized over its cases from the start.
