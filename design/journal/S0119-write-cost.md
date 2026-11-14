# S0119: the write's cost at 54k, measured on the product's path (M1b, BL-0112)

## Brief
- Goal: M1b's fifth slice. Measure what one write costs at 54k entries on the product's path, now that the preview and the confirm exist (S0116, S0117). Measure each job the user's write runs, against ADR-0008's 5 s target at 54k and 1 s at demo size:
  - the `preview` (plan, contract steps 1-3, the overlay load);
  - the `save` (the confirm's job);
  - the background `rebuild` with its publish.
  Do this for three ledger layouts and report the numbers. Build no speed-up in this slice: the owner chose to measure first (below).
- The layouts, all generated, never real data (INV-23, INV-24):
  1. **One 8.7 MB file:** the demo generator's 54k ledger, as in S0106; it is the baseline that makes this run comparable.
  2. **12 files:** the same entries split across 12 files under the root, joined by `include` (one glob include, as spike S4's `big-multi` was), so the ledger set has 13 files.
  3. **One 20 MB file:** about 54k transactions in one file of about 20 MB, as spike S4's `big` was (its 10× scale-up of the legacy demo, which kept blank lines and gave every copy its own `id` and `content_hash` metadata; S4 measured 5.3-7.3 s there). Build it from our own generator's output by a stated, deterministic recipe; record the recipe, the file's size, its line count and its transaction count in the journal. If you cannot reach about 20 MB at about 54k transactions without data that does not look like a ledger a user has, stop and report.
  Plus **demo size** (the demo generator's demo ledger) for the 1 s target.
- How:
  - **Extend the existing scripts** (`backend/scripts/measure_protocol.py`, `engine/scripts/measure_save.py`) or add one script; keep each measurement script's test case in `backend/tests/test_measure_scripts.py` (the backend CLAUDE.md rule).
  - **The preview needs a producer.** The shipped engine's `PRODUCERS` stays empty (`test_the_shipped_engine_has_no_producer`). Run the preview through the test-only entry that registers the engine's test producer in that process only (as `tests/workspaces/test_writes_end_to_end.py` does), and say which and why. No producer is ever reachable in the shipped engine.
  - **One write is:** a preview of a one-transaction edit in the file that holds the most recent entries, then its confirm, then the rebuild that follows, each job in its own fresh engine as the scheduler gives them.
  - **Per run, report** each job's wall time, the time from the confirm's submit to the new generation published, and the user-visible total from the preview's submit to the publish (for the record; the 2026-10-31 amendment counts the target per job). Also each job's peak RSS where the existing harness reads it.
  - **Runs:** 5 alternating runs per layout, the machine's load average recorded, as S0106 did.
- Implements: ADR-0008's amendments of 2026-10-25 (the 5 s target includes the mirror update), 2026-10-27 (measure before building the parse cache) and 2026-10-31 (the 5 s target counts jobs); ADR-0064; BL-0112.
- Capabilities: none changes status.
- Invariants touched: INV-20 (measured, not enforced: its status stays pending, BL-0020), INV-23 and INV-24 (generated data only; no path of the owner's machine in the repo; runs named by date and order), INV-10 (peak RSS reported).
- Risk class / lane: Medium / full. Review budget row: **Tooling, gates, measurement**: one verifier round; the fix round fixes only a shape the script's own docstring promises to handle, a false statement of fact, or a finding in a guard of real data or privacy (INV-23, INV-24); a High is always fixed.
- The bytes it protects: none. It measures, and writes only into temporary copies the script makes.
- Mechanism questions (PROCESS.md §3 step 2): it adds no mechanism. The policy question came first: the owner chose to measure before building the step-1 parse reuse or the parse cache (below). Fix-round history of what it measures: the scripts were broken and repaired once (S0106, BL-0235).
- **What follows the numbers** (not built here):
  - If every job of every layout meets its target, BL-0112 closes: the parse cache is not built (ADR-0008, 2026-10-27).
  - If a job of a layout misses, the slice ends with the numbers. I bring the owner an options note: the cheapest first (a stated limit for that layout), then the step-1 parse reuse of the 2026-10-25 amendment, then the parse cache of the 2026-10-27 amendment. Its recommendation carries the overlay load's review history.
- Acceptance:
  - each new or changed measurement script has its case in `test_measure_scripts.py`, which runs it at a tiny size and checks the report's shape;
  - the case fails on the base commit (the new layouts and the preview's timing do not exist there);
  - the journal records the numbers, the recipe and the loads;
  - `design/specs/engine-jobs.md` §15 "Limit (speed)", the roadmap's M1b "Measured" paragraph and BL-0112 are updated with them.
- Expected doc impact: engine-jobs §15 (the speed limit's numbers, the 12-file and 20 MB rows), roadmap §4 M1b, BL-0112 (closed or narrowed with the numbers), `backend/CLAUDE.md`'s list of measurement scripts if one is added.
- Open questions: none.
- Base: 33e21e96.

## Decisions
- Measure first, and build the step-1 parse reuse or the parse cache only for a layout that misses, after an options note (owner, 2026-11-14, in session, choosing between "Measure first" and "Build the parse reuse, then measure": "Measure first (Recommended)").
- (agent) The 5 s target is judged per job, as ADR-0008's amendment of 2026-10-31 says ("The 5 s target counts jobs ... Each job keeps the 5 s target, the mirror update included"): the save's job is judged until the rebuild has published. The user-visible total from the preview's submit to the publish is reported beside it, for the owner's judgement at G1.

- (agent) **A new script, `backend/scripts/measure_write.py`, not an extension of `measure_protocol.py`.** `measure_protocol.py` drives the engine client directly to isolate the protocol's hop; one write is a different harness: it runs through the sidecar's own scheduler (`EngineJobs`) and write use cases (`app.workspaces.writes.submit_preview`, `submit_confirm`), so every job gets the engine the scheduler gives it (a fresh one after a job that ran the ledger's plugins, its start inside the job's time) and the rebuild is the one the landed save queues (`rebuild_submitter`; the script's `Jobs` subclass keeps each ticket by kind so it can be waited for). The case is `write` in `backend/tests/test_measure_scripts.py`.
- (agent) **The producer.** The script's engine command is the product's (`app.main.engine_command`: the venv engine, its bytecode folder, the engine lock, the app cache), checked to be `bootstrap_argv(python, site, ENGINE_RUN)` before its startup code is changed, and nothing else: it appends `engine/` to the path and registers the engine's test producer under `test.edits` as a lambda that imports `tests.ledger.producer` on its first call. Unlike the end-to-end test's entry (an eager import, the path inserted first), only the preview's engine imports the producer and the save's and rebuild's engines start as the product's do. It lives only in that string in a script that is not shipped (the backend's package is `app`; `[tool.uv.build-backend] module-name = "app"`); `test_the_shipped_engine_has_no_producer` is untouched and passes. The plan writes only into the script's test copy: the confirm goes through the promotion guard and the confinement to the copy's folder like every unpromoted mutator's, with the copy's mark read by the engine's own port (the end-to-end test's `recorded_mark`, copied).
- (agent) **The bytecode fill is awaited before the first open.** The sidecar starts the engine's bytecode fill on its first spawn and spawns without bytecode until the fill is complete (`EngineClient._bytecode_for_spawn`). Every launch after the first has it, so the script waits for the fill's mark (and fails on a logged `fill_failed`). Method difference from S0106: `measure_protocol.py` makes a fresh app folder per invocation and does not wait, so its engines started without bytecode while the fill ran beside them. Its numbers and these are not strictly comparable on the engine starts; the effect size is not measured.
- (agent) **The edit is an insert of one transaction**, the shape of the first planned mutator (adding a transaction) and of S0106's appended transaction: `Expenses:Food:Groceries 12.34 USD` against `Assets:Bank:Checking`, dated the latest entry's date, into the file that holds that entry (the insert routed by `source_edit.route_insert`, Fava's rule: the end of that file). A preview with any new error, or with a plan for another file, ends the run as unexpected; no run did.
- (agent) **The 12-file layout is 12 equal chunks, not one file per year.** The generator's 54k ledger spans 21 years (2006-2026), so a per-year split gives 21 files; the brief asks for 12. The root keeps every block that is not a dated entry (the header comments and the options) and one glob include, `include "parts/*.beancount"`; the dated entries go, in file order, into `parts/01.beancount` to `parts/12.beancount` (0.73 MB each); the last holds the most recent entries.
- (agent) **The 20 MB recipe** (below) is v0.3's own transaction shape: its demo generator gave every transaction `id` and `content_hash`, and an imported one `source_account`, `external_id` and `external_id_type "OFX"` (`backend/scripts/generate_fake.py`'s `txn` in the legacy tree, read only; spike S4's `big` kept those lines). With all five on every transaction the file is 21.3 MB with the same 54,000 transactions; with only the first two it would be 15.6 MB. 21.3 MB is "about 20 MB" and looks like a ledger written by v0.3's importer, so no stop was needed on that point.
- (agent) **The 20 MB layout misses; reported, not fixed.** Its preview fails every run: the plan carries the file's whole new text, the reply line passes 16 MiB, the client cuts it and stops the engine as a protocol violation (`engine.stopped`, "the reply line was cut off"; BL-0106). This is the reply side, not §5.5's request-size limit as round 0 wrote (verifier F2); §5.5 now states both. The failure came 5.30-5.41 s after the submit, and the engine writes its reply only after its last step, so the preview's work alone took over 5 s for this file. For the owner's options note, the same file was measured inside the engine with the existing `engine/scripts/measure_save.py` (5 runs). Seen on the way, not filed: the oversized preview counts as an engine stop toward "engine unavailable" (BL-0082's reply-size case, now reachable by a preview of a file over 16 MiB); BL-0106 (the plan sends edits) removes it.
- (agent) **Peak memory per job**: the harness reads no child's peak, so the script samples the running engine's resident size every 5 ms (macOS `proc_pid_rusage`), attributed to the job running when that engine was first seen, and also reads its lifetime peak footprint, which no sample can miss. Each job ran in exactly one engine in every run (the report's `<job>_engines`). A spike of the resident size shorter than 5 ms can be missed; the footprints agree with the samples to about 16 MB.
- (agent) **What is judged**: each job against its target (ADR-0008, 2026-10-31); the save's job until the rebuild has published, so "confirm to publish" is the save's figure. The rebuild's time runs from the save's answer to its own: it holds the retire of the save's engine, the lock wait and a fresh engine. The user-visible total from the preview's submit is reported for the record. At demo size it is 1.07-1.12 s, over 1 s, while every job is under; that is for the owner's judgement at G1.
- (agent) **Docs**: the speed limit is in engine-jobs §6.1 ("Limit (speed)"; §15 only indexes it), updated there. The roadmap had no M1b "Measured" paragraph; one is added to §1 after "Next: M1b". BL-0112 stays open, narrowed: every layout but the 20 MB one meets its targets, and that one waits for the owner's options note.

## Measurements
- **Machine:** the owner's Mac (Mac16,8, Apple M4 Pro, 14 cores, 48 GiB), macOS 27.2, APFS (the system volume's temp folder), Python 3.13.16 (sidecar and engine). Shared with other sessions: the 1-minute load average was logged before each invocation and by the script before each run; over the runs it ranged 5.2 to 30.1, falling through the session.
- **Ledgers:** generated at measurement time in the session scratchpad by `backend/scripts/gen_demo.py --anchor 2026-03-31` (`demo`: one 0.90 MB file; `54k`: one 8.71 MB file, 216,282 lines, 54,018 entries of which 54,000 transactions, 2006-04-01 to 2026-03-31). The layouts are derived by `measure_write.py` into a fresh test copy for every run:

  | Layout | Files | Size | Lines | Transactions | The edited file |
  |---|---|---|---|---|---|
  | `one` (54k) | 1 | 8.71 MB | 216,282 | 54,000 | the root, 8.71 MB |
  | `files12` (54k) | 13 | 8.71 MB | 216,284 | 54,000 | `parts/12.beancount`, 0.73 MB |
  | `meta20` (54k) | 1 | 21.34 MB (21,335,154 bytes) | 486,282 | 54,000 | the root |
  | demo (`one`) | 1 | 0.90 MB | | | the root |

- **The 20 MB recipe** (`measure_write.layout_of("meta20", ...)`, deterministic): from the generator's 54k file, after the first line of every transaction (a block whose first line is a date and `*`, `!` or `txn`), insert five lines; `i` counts transactions from 0 in file order:
  - `  id: "<uuid5(NAMESPACE_URL, "finzytrack-measure/<i>")>"`
  - `  content_hash: "<sha256 hex of "<i>" NUL and the transaction's text>"`
  - `  source_account: "<the account of its last posting>"`
  - `  external_id: "<YYYYMMDD of its date><i in 8 digits>"`
  - `  external_id_type: "OFX"`

  Everything else is unchanged. Result: 21,335,154 bytes, 486,282 lines, 54,000 transactions (SHA-256 of the file from the anchor-2026-03-31 ledger starts `77e5856d`).
- **Method:** a driver in the scratchpad ran 5 rounds; each round ran three invocations in turn: (A) the 54k ledger with `--layout one --layout files12` (the order reversed every other round), (B) the demo ledger with `--layout one`, (C) the 54k ledger with `--layout meta20`. Each invocation is a fresh sidecar process, app folder and cache; each run a fresh copy and a fresh workspace key, opened first (its time recorded, not judged). Raw output: the scratchpad's `raw/` (`r1-54k.json` to `r5-meta20.json`, `loads.txt`, `meta20-engine-side.txt`; not committed: they hold scratch paths). A first trial invocation under load 5-20, outside the five rounds, gave the same figures except one 12-file rebuild of 4.55 s at load 20; it is not counted.
- **One write per job, 5 runs each, median and range:**

  | | `one` (54k, 8.7 MB) | `files12` (54k, 13 files) | demo | `meta20` (21.3 MB) |
  |---|---|---|---|---|
  | Preview | 4.01 s (3.95-4.07) | 2.24 s (2.17-2.38) | 0.48 s (0.47-0.49) | fails after 5.36 s (5.30-5.41) |
  | Save (the confirm's job, to its answer) | 0.27 s (0.27-0.28) | 0.22 s (0.21-0.22) | 0.21 s (0.20-0.22) | not reached |
  | Rebuild with its publish (from the save's answer) | 2.97 s (2.95-3.07) | 3.05 s (2.96-3.10) | 0.41 s (0.40-0.43) | not reached |
  | **Confirm to the new generation published** | **3.24 s (3.21-3.35)** | **3.27 s (3.17-3.32)** | **0.62 s (0.60-0.63)** | |
  | User-visible: preview's submit to the publish | 7.25 s (7.17-7.43) | 5.55 s (5.35-5.63) | 1.09 s (1.07-1.12) | |
  | Open (for the record) | 2.92 s (2.80-3.31) | 2.98 s (2.82-3.27) | 0.29 s (0.28-0.31) | 3.24 s (3.22-3.27) |
  | The plan's text | 8.71 MB | 0.73 MB | 0.90 MB | the reply over 16 MiB |
  | Load average at the run | 5.9-30.1 | 5.9-25.6 | 5.5-20.5 | 5.2-18.9 |

  Every rebuild loaded in one try. Against the targets: at 54k every job of `one` and `files12` is under 5 s (the preview with about 1 s to spare in one file); at demo size every job is under 1 s; `meta20` cannot be written.
- **Peak memory of each job's engine** (resident size sampled every 5 ms / lifetime peak footprint), range over 5 runs:

  | | `one` | `files12` | demo |
  |---|---|---|---|
  | Preview | 490-498 / 474-482 MB | 368-369 / 352-354 MB | 94-95 / 78-79 MB |
  | Save | 124-126 / 109-110 MB | 54-55 / 39 MB | 57 / 41 MB |
  | Rebuild | 340-341 / 324-325 MB | 333-338 / 317-322 MB | 81-82 / 65-66 MB |

- **Why `meta20` fails:** the five rounds' runs report `ENGINE_STOPPED`; the script then gained the report of the client's `engine.stopped` events (a log sink only, nothing on the measured path), and a sixth `meta20` run (load 5.7, preview 5.39 s) gave `protocol_violation: the reply line was cut off (no newline before the end of the stream)`.
- **Size sweep (round 1, verifier F2):** where the preview crosses 5 s and where it fails, for the `meta20` shape. The 54k ledger cut after its N-th transaction (`--cut N`), then the recipe; 3 rounds, each running the six sizes in turn, one invocation per size (`--layout meta20 --cut N --runs 1`); load 5.1-36.0 (raw: the scratchpad's `raw/sweep/`). The JSON-escaped text is the file times 1.058.

  | N transactions | File | Escaped text | Preview | Save | Rebuild | Confirm to publish | Preview's peak RSS | Load |
  |---|---|---|---|---|---|---|---|---|
  | 25,300 | 10.00 MB | 10.58 MB | 2.30-2.49 s | 0.27-0.32 s | 1.57-2.35 s | 1.84-2.67 s | 408-415 MB | 5.1-19.1 |
  | 30,400 | 12.01 MB | 12.71 MB | 2.84-4.35 s | 0.29-0.48 s | 1.89-3.18 s | 2.18-3.65 s | 484-486 MB | 6.7-20.4 |
  | 35,500 | 14.03 MB | 14.84 MB | 3.34-3.42 s | 0.30-0.32 s | 2.17-2.33 s | 2.47-2.65 s | 554-559 MB | 14.2-36.0 |
  | 38,000 | 15.01 MB | 15.89 MB | 3.62-3.70 s | 0.31-0.33 s | 2.31-2.40 s | 2.63-2.73 s | 590-595 MB | 12.4-30.3 |
  | 40,000 | 15.80 MB | 16.72 MB | 3.71-3.80 s | 0.32-0.33 s | 2.41-2.93 s | 2.73-3.26 s | 615-620 MB | 10.9-25.9 |
  | 41,000 | 16.20 MB | 17.14 MB | fails after 3.80-3.82 s (`ENGINE_STOPPED`) | | | | | 9.9-22.1 |

  Every run below 16.2 MB published. 16 MiB is 16,777,216 bytes: the escaped text of the 40,000 cut (16,724,742 bytes) fits with its reply's envelope, the 41,000 cut's does not. What this changes: in this shape the preview stays under 5 s up to the largest file that can be written (3.8 s at 15.8 MB), and the full 54k at 21.3 MB fails after 5.3 s. The preview's time follows the number of transactions more than the bytes (the plain 54k file, 8.7 MB with 54,000 transactions, takes 4.0 s; 15.8 MB with 40,000 takes 3.8 s). So for one file the limit that binds is the size, between 15.8 and 16.2 MB of text here, and it fails by stopping the engine. The 12 MB runs spread most, under the session's highest loads.
- **The 21.3 MB file inside the engine** (`engine/scripts/measure_save.py --runs 5`, load 4.9; not the product's path: no fresh engines, no protocol; the file was written by ad-hoc code in round 0; since round 1 it is `cd backend && .venv/bin/python -I scripts/measure_write.py <54k root> --layout meta20 --write-layout DIR`, then `cd engine && .venv/bin/python -I scripts/measure_save.py DIR/meta20/main.beancount WORK --runs 5`, and the file it writes has the same SHA-256, `77e5856d...`): open (load, build, publish) 3.16-3.41 s; the preview's overlay load 1.88-1.94 s; the save 0.139-0.148 s; the rebuild with its publish 3.40-3.52 s.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- **Test first:** `backend/tests/test_measure_scripts.py` gained the `write` case (`measure_write.py` on the tiny ledger, all three layouts, one run: the report's parts, 13 files for `files12`, five more lines per transaction for `meta20`, every run `published` with each job's time and peak memory). On 33e21e96 with only the test changed: `2 failed, 4 passed` (`test_every_measurement_script_has_a_case`: the case names a script the folder lacks; `test_measurement_script_runs[measure_write.py]`: "No such file or directory"). With the script: `6 passed`.
- **Checks that accept, tag or refuse**, each with its must-not-pass test:
  - `test_every_measurement_script_has_a_case` now expects `measure_write.py`: unchanged check, its planted case is S0106's (a `measure_*.py` without a case fails it); it failed on the base, as above.
  - The `write` smoke case: must not pass without the script (fails on the base, above) or with a run that does not end `published` (its assertion on every run's `outcome`; `test_a_script_that_fails_fails_its_case` covers a script exiting non-zero).
  - The script's own guards (measurement guards, not product checks): it reports a run `published` only when the open published a generation, the preview answered `Previewed` with no new error and a plan for exactly the file it targeted, the confirm's job answered `Saved`, exactly one rebuild was queued and it answered `Loaded` with `published`, the generation number rose, and the file holds the plan's text; anything else ends that run with its outcome named (the `meta20` runs). It fails outright when the product's engine command is not the shape it changes, when the bytecode fill fails, or when a job gives no answer in 600 s (a stuck job, not a budget). Not tested by a planted failure each (stated limit: a guard that wrongly passed would show as a `published` run with a wrong time, which the smoke case cannot see).
- **Mutants:** not run (optional on tooling; no check here guards ledger data, finance results, real-data privacy or product security; the script writes only into its own temp folder and runs only on the ledger it is given).
- **Population:** the measurement scripts under `backend/scripts/`, found by name and behaviour (every `measure_*.py`; no other script there reads a timer for a report): 4 at the head (`measure_health.py`, `measure_mirror_reads.py`, `measure_protocol.py`, `measure_write.py`), 4 with a case. The layouts: the brief's three plus demo size, all four measured 5 times.
- **Round 1 (verifier F1, F4):** `test_measure_write_reports_a_run_that_does_not_publish` (a preview reply over 16 MiB that stops the engine: the run's outcome, its `preview_ms` and `engine_stops` kept, the summary `published: 0`; then a 1 µs `--deadline`: `not measured: open: no answer`, exit 0 with the report). On ddcb4020's script it fails (its summary counted the failed run's preview time; it has no `--deadline`); with the fix `7 passed`. `--write-layout` refuses an existing file (exit 2, checked by hand). New stated limits in the script's docstring: F3's, and the 5 ms sampling.
- **Gates** (before the commit): backend `uv sync --locked`, `ruff format --check`, `ruff check`, `pyright` (0 errors), `lint-imports` (10 kept), `deptry src`, `pytest` (3132 passed, 23 xfailed); `scripts/pyright_platforms.py backend` (Darwin and Linux, 0 errors); `scripts/check_invariants.py` (exit 0); `scripts/run_meta_tests.py` (981 tests, 0 failures). The engine's code is unchanged, so its gates were not run; its `measure_save.py` ran as a measurement only.
- **Gates, round 1:** the same backend gates (`pytest` 3133 passed, 23 xfailed), `pyright_platforms.py backend` (Darwin and Linux, 0 errors), `check_invariants.py` (exit 0), `run_meta_tests.py` (981 tests, 0 failures).

## Verifier
- Round 1, head ddcb4020: **PASS WITH FINDINGS**, 0H/2M/2L, 11 attacks; the machine's load was 31-64 during the verifier's reruns. Review budget row "Tooling, gates, measurement": one round, no fresh one.

  | Finding | Class | What | Disposition |
  |---|---|---|---|
  | F1 Medium | (c) | `waited()` and `fail()` raise `Measured` and nothing caught it: a stuck job, a refused submit, a failed open or no generation after the open crashed the script with exit 1 and no report, against its docstring. | Fixed (rule 1): `main` catches a run's `Measured` (and `OSError`, a failed mark read), records `not measured: ...` with the figures so far and the engine's stops, and goes on; the open's exclusive window ends in a `finally`; the summary covers published runs only (`published`, `not_published`); `--deadline` sets the runs' answer wait. Test: `test_measure_write_reports_a_run_that_does_not_publish` (an oversized preview reply that stops the engine, and a 1 µs deadline), which fails on ddcb4020's script (its summary counted the failed run's preview). |
  | F2 Medium | (d) | The docs said a 20 MB file cannot be written under §5.5's request-size limit; what fails is the preview's reply (the new text, JSON-escaped), cut at 16 MiB, with the engine stopped as a protocol violation. The verifier's cut: 14.22 MB previews and publishes, 16.20 MB fails. | Fixed (rule 2): engine-jobs §5.5 and "Limit (speed)", BL-0106, BL-0112, the roadmap and this journal state the reply side, the stop and the measured threshold; the size sweep below measures where the preview crosses 5 s and where it fails. |
  | F3 Low | (c) | The smoke case does not check how `files12` spreads the entries, nor where `rebuild_ms` and `total_ms` start. | Stated limit in the script's docstring. |
  | F4 Low | (c) | No script writes a layout to disk, so the in-engine 21.3 MB numbers needed ad-hoc code. | Fixed by a small option instead of a limit line: `--write-layout DIR` (with `--cut N`); the command is in Measurements. |

## Retro
- Build about 20 min, fix about 14 min (the size sweep included), 1 verifier round (measurement row: no fresh round), findings 0H/2M/2L (M: 1 c, 1 d; L: 2 c). Cheaper gate: a measurement that ends at a limit names the limit's real threshold before any option is put to the owner (here the verifier found the 16 MiB line binds at about 16 MB of file text, not at "20 MB", because the answer is the file's text escaped as JSON); and a measurement script's error path is tested with a run that does not publish from the start. Process checks: premise check before asking the owner (options note held until the verifier confirmed the numbers) 1 catch / 0 false positive; the size sweep turned a "speed" question into a "size" question before the owner was asked.
- Options note: `design/inbox/20261115-large-single-file.md` (BL-0112's last part).
