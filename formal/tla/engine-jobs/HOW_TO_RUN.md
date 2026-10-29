# How to run the engine-jobs model checks

The TLA+ model of `design/specs/engine-jobs.md` §13 (spec with simplification B of 2026-10-28: the engine lock, Back always recovers first; and of 2026-10-29: Retry recovers first too; model with fidelity review 4's fixes and simplification B, MAPPING.md §5). It runs as a plain shell script with Java;
no AI is involved. MAPPING.md says what the model checks and how it maps to the spec.

## Before the first run

- Java 21 (`java -version` should say 21; Temurin 21.0.8 on the owner's Mac).
- Nothing else: the first run downloads `tla2tools.jar` 1.7.4 into `formal/tla/tools/` and refuses it unless
  its SHA-256 equals the one in `formal/tla/tools.lock` (`formal/tla/fetch_tools.sh`). The jar is not committed.

## The commands, per tier

From the repository root (estimates for this Mac, measured or derived below):

```sh
formal/tla/engine-jobs/run_all.sh --tier S                 # small worlds and every mutant: about 45 min
formal/tla/engine-jobs/run_all.sh --tier M                 # medium: an estimated 5-30 min
formal/tla/engine-jobs/run_all.sh --only L_save_all,L_r4_switch,L_save_deep,L_world   # large: hours
formal/tla/engine-jobs/run_all.sh --only L_session         # one night each, alone (disk below)
formal/tla/engine-jobs/run_all.sh --only L_recover_all
formal/tla/engine-jobs/run_all.sh --only L_world           # the bounded simulation of the whole world
formal/tla/engine-jobs/run_all.sh --kind mutant            # only the seeded bugs
formal/tla/engine-jobs/run_all.sh --list                   # what would run; runs nothing
```

An unknown or empty `--tier`, `--kind` or `--only` value is refused (exit 2), so a typo never runs nothing quietly.
Options: `--workers N` (TLC worker threads; default all cores), `--heap 12g` (default half the RAM),
`--force` (run again configurations that already have a result). `SIM_TRACES=… SIM_DEPTH=…` size the simulation
(default 200,000 traces of at most 200 steps).

## What it does

- It runs the configurations in `configs.txt`, in that order. Each line says the result it must have:
  - `PASS`;
  - `VIOLATE:<property>` for a seeded bug (a mutant), or for `MUT_appscoped_runs` and
    `MUT_finishing_confirmed_only`, which check a wording the spec replaced and must fail (reachability
    checks, MAPPING.md §5).
- One log per configuration: `results/<name>.log`. After every configuration it rewrites `results/SUMMARY.md`:
  result, expected result, distinct states, depth, wall time and distinct states per second, the total per
  tier, and **ALL AS EXPECTED** or **SURPRISES**. A log of an older model, `.cfg`, tool or simulation size
  shows as `stale` and does not count: run it again. A log whose verdict is `ERROR` or `INCOMPLETE` is
  run again on resume.
- TLC runs under `nice -n 10`, so the Mac stays usable.
- **Ctrl-C** stops the running TLC, deletes its temporary state folder (`results/states/`), records the stopped
  run as `INCOMPLETE` with the states it reached, and writes the summary. Run the same command again to resume:
  a configuration is skipped while the model, its `.cfg`, the jar and the Java version are unchanged; the
  stopped one starts again from the beginning.
- A large run's state folder can reach tens of GB: `L_session` and `L_recover_all` likely need 50-100 GB
  free, the other large worlds 10-30 GB. Run those two alone with `--only`, one per night; `--tier L` runs
  every large world in a row and could take two days or more. After a power loss during a run, delete
  `formal/tla/engine-jobs/results/states/` by hand.

## Reading the results

| Result | Meaning |
|---|---|
| `PASS` as expected | the design holds in that world |
| `VIOLATE:<property>` as expected | a mutant breaks exactly that property, so the model can catch that mistake (for the two reachability checks: the old wording still fails) |
| a row with "As expected = no" | needs a look: read its log (TLC prints the shortest trace that breaks the property) |

## How long it takes

Measured on the owner's Mac (Apple M4 Pro, 14 cores, 48 GB) on 2026-10-28 with 14 TLC workers, on the
model after simplification B. Other work may have loaded the Mac during these runs, so single times can be
up to twice what a quiet Mac gives. The agent ran the small tier in parts of at most 10 minutes (`--only`
lists).

| Tier | Configurations | Result | Size | Wall time |
|---|---|---|---|---|
| S | 30 safety worlds (`S_save_orphan` removed by simplification B) | all PASS | 2,548 (`S_create_nonempty`) to 6,618,093 distinct states (`S_r4_attempts`); 38.6 million together | 1 s to 156 s each (`S_switch`); 735 s together |
| S | 5 liveness worlds (`LS_boot`, `LS_quit`, `LS_free`, `LS_free_cc`, `LS_free_orphan` new in simplification B) | all PASS | up to 790,140 (`LS_boot`) | 449 s together (`LS_boot` 375 s, against 215 s on the model after review 4 with the same 790,140 states: the Mac was busier) |
| S | `LS_jobs` | **not re-run by the agent** (one run takes longer than an agent tool call); the owner's `--tier S` run covers it | 1,723,585 on an earlier model | 642 s on an earlier model |
| S | 92 mutant configurations (90 mutants; the reviewers' own among them, MAPPING.md §6) | each violates its property | | 237 s of checking, about 10 min with start-up (longest `MUT_cnc_auto_lift`, 61 s) |
| S | all of the above but `LS_jobs` | **all as expected, 127 configurations** | | 1,421 s of checking, about 30 min with start-up; with `LS_jobs` about 40-45 min |
| M | `LM_free`, `M_recover_temps`, `M_r4_switch` | not run on this model | | `LM_free`: 219 s on an earlier model; others below |
| L | `L_save_all`, `L_r4_switch`, `L_session`, `L_recover_all`, `L_save_deep` | not run on this model | | estimate below |
| L | `L_world` (bounded simulation) | not run on this model | default 200,000 traces of ≤ 200 steps (about 11.5 million states) | estimate below |

Simplification B removed the orphaned engine's own steps, so the worlds where it wrote shrank:
`S_recover_lock` 151,810 → 31,173 (74,675 before review 4 added the writing orphan). Worlds without an orphan
kept their size (`S_save_crash` 2,814,748). The large worlds that had a writing orphan (`L_save_deep` and
`L_world`, a lingering engine after an app crash; `L_recover_all`, `BootOrphan`) lose those interleavings, so
the estimates below, made before review 4 added them, stand; in exchange any lingering engine now holds the
engine lock (before, only one that held ledger-set locks counted), which adds a little where `MaxOrphan` is 1.
`results/SUMMARY.md` lists every configuration, those never run here as "not run".

Estimates for what was not run, and how they were derived. The model grew between 84c8b8c9 and now: the
small worlds have 1-6 times as many states as before (`S_save_crash` 573,953 → 3,377,888; `S_recover`
440,068 → 2,282,270; `S_queue` about the same), mostly because Quit anyway and the screen exits now have
no counters (§13 requires that). Distinct states per second stayed at 50,000-90,000 with 12 workers.
- `M_r4_switch`: 149,002 states at 84c8b8c9; under a minute now.
- `M_recover_temps`: 7,890,754 states in 207 s at 84c8b8c9; now an estimated 8-50 million, 2-12 minutes.
- `L_save_all`, `L_r4_switch`: at 84c8b8c9 each passed 23.5 million states in 10 minutes with the queue
  almost at its peak (an estimated 50-100 million in all); now an estimated 50-600 million states,
  between 10 minutes and 2 hours each.
- `L_session`, `L_recover_all`: at 84c8b8c9 the queue was still growing fast after 20 minutes (40-58
  million states); now likely beyond a billion states: many hours to a day each, and tens of GB of state
  files. Optional; one per night with `--only`.
- Fidelity review 2 grew the small worlds by 1-2.5 times (`S_switch` 2,266,121 → 6,570,883; `LS_boot`
  564,830 → 921,756): Cancel of a switch's own job, a power loss in the quit-note world, R4's sync step.
  The medium and large estimates above are from before it and may be low by that factor.
- `L_save_deep`: a deeper `L_save_all` (two saves, two power losses, three starts): likely hours.
- `L_world`: a bounded random simulation, not an exhaustive check (the whole world at once is far beyond a
  laptop); 200,000 traces, an estimated 2-10 minutes. Raise `SIM_TRACES` for more coverage.
- `LL_jobs` (the large liveness world of 84c8b8c9) is dropped: its properties are checked in the small
  liveness worlds, and TLC checks liveness 20-40 times slower per state.

## The conformance traces (S0059)

`traces/corpus.json` holds TLC-simulated traces of small worlds that `backend/tests/platform/test_engine_jobs_conformance.py`
replays through the queue table and the scheduler shell (no Java at test time). Regenerate it after any change to
`EngineJobs.tla`, to a `.cfg` it uses or to the generator (the test fails until then, naming this command):

```sh
python3 formal/tla/engine-jobs/gen_traces.py           # about 15 s; Java 21 and the pinned jar
python3 formal/tla/engine-jobs/gen_traces.py --check   # is the corpus current? (no Java)
```

The worlds, the seed, the trace depth and what the walk leaves out are in the generator's header; MAPPING.md §7 says
how the model's actions map to the code.

## Changing the model

- Edit `EngineJobs.tla`; edit the worlds and mutants in `gen_cfgs.py`, then run
  `python3 formal/tla/engine-jobs/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_engine_jobs.py` checks that the property and mutant names still
  match the spec's §13 and that MAPPING.md lists exactly the model's actions.
