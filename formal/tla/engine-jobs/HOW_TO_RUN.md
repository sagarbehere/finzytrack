# How to run the engine-jobs model checks

The TLA+ model of `design/specs/engine-jobs.md` §13 (at 1cf1fb4c). It runs as a plain shell script with Java;
no AI is involved. MAPPING.md says what the model checks and how it maps to the spec.

## Before the first run

- Java 21 (`java -version` should say 21; Temurin 21.0.8 on the owner's Mac).
- Nothing else: the first run downloads `tla2tools.jar` 1.7.4 into `formal/tla/tools/` and refuses it unless
  its SHA-256 equals the one in `formal/tla/tools.lock` (`formal/tla/fetch_tools.sh`). The jar is not committed.

## The commands, per tier

From the repository root (estimates for this Mac, measured or derived below):

```sh
formal/tla/engine-jobs/run_all.sh --tier S                 # small worlds and every mutant: about 25 min
formal/tla/engine-jobs/run_all.sh --tier M                 # medium: an estimated 5-20 min
formal/tla/engine-jobs/run_all.sh --tier L                 # large: hours; L_session and L_recover_all overnight
formal/tla/engine-jobs/run_all.sh --only L_world           # the bounded simulation of the whole world
formal/tla/engine-jobs/run_all.sh --kind mutant            # only the seeded bugs
formal/tla/engine-jobs/run_all.sh --list                   # what would run; runs nothing
```

Options: `--workers N` (TLC worker threads; default all cores), `--heap 12g` (default half the RAM),
`--force` (run again configurations that already have a result). `SIM_TRACES=… SIM_DEPTH=…` size the simulation
(default 200,000 traces of at most 200 steps).

## What it does

- It runs the configurations in `configs.txt`, in that order. Each line says the result it must have:
  - `PASS`;
  - `VIOLATE:<property>` for a seeded bug (a mutant);
  - `VIOLATE:<property>` for a world where the spec as written breaks a property (findings N1-N3, MAPPING.md §5).
    The world's `P_` companion runs with the model's proposed fixes and must pass.
- One log per configuration: `results/<name>.log`. After every configuration it rewrites `results/SUMMARY.md`:
  result, expected result, distinct states, depth, wall time and distinct states per second, the total per
  tier, and **ALL AS EXPECTED** or **SURPRISES**.
- TLC runs under `nice -n 10`, so the Mac stays usable.
- **Ctrl-C** stops the running TLC, deletes its temporary state folder (`results/states/`), records the stopped
  run as `INCOMPLETE` with the states it reached, and writes the summary. Run the same command again to resume:
  a configuration is skipped while the model, its `.cfg`, the jar and the Java version are unchanged; the
  stopped one starts again from the beginning.
- A large run's state folder can reach several GB; keep about 20 GB free. After a power loss during a run,
  delete `formal/tla/engine-jobs/results/states/` by hand.

## Reading the results

| Result | Meaning |
|---|---|
| `PASS` as expected | the design holds in that world |
| `VIOLATE:<property>` as expected | a mutant breaks exactly that property, so the model can catch that mistake; or a spec finding shows as expected |
| a row with "As expected = no" | needs a look: read its log (TLC prints the shortest trace that breaks the property) |

## How long it takes

Measured on the owner's Mac (Apple M4 Pro, 14 cores, 48 GB) on 2026-10-28 with 12 TLC workers, on the
final model (other work running, load about 10). An idle Mac is faster.

| Tier | Configurations | Result | Size | Wall time |
|---|---|---|---|---|
| S | 24 safety worlds as the spec is written; 5 of them break a property through findings N1 or N2 and stop early (MAPPING.md §5) | all as expected | 196 to 6,666,629 distinct states (`S_fs_weak`) | 1 s to 87 s each |
| S | 7 `P_` companions (those worlds, and the two liveness worlds below, with the proposed fixes) | all PASS | up to 6,819,452 (`P_S_r4_attempts`) | up to 78 s |
| S | 5 liveness worlds as written (`LS_boot`, `LS_jobs`, `LS_quit` pass; `LS_free`, `LS_free_cc` break `EngineCanAlwaysBeFreed` through N3) | all as expected | up to 1,843,235 (`LS_jobs`) | up to 655 s (`LS_jobs`) |
| S | 63 mutants | each violates its property | | 150 s together (longest `MUT_cnc_auto_lift`, 59 s) |
| S | all of the above | **ALL AS EXPECTED, 99 configurations** | | about 24 min together |
| M | `LM_free` (spec as written) | violates `EngineCanAlwaysBeFreed` (N3), as expected | 25,864 | 4 s |
| M | `P_LM_free` (liveness, to completion) | PASS | 1,708,054 distinct, depth 104 | 219 s |
| M | `M_recover_temps`, `M_r4_switch` | not run on this model | | estimate below |
| L | `L_save_all`, `L_r4_switch`, `L_session`, `L_recover_all`, `L_save_deep` | not run on this model | | estimate below |
| L | `L_world` (bounded simulation) | a 200-trace smoke run passed (11,484 states, under 1 s) | default 200,000 traces of ≤ 200 steps (about 11.5 million states) | estimate below |

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
  files. Optional, overnight.
- `L_save_deep`: a deeper `L_save_all` (two saves, two power losses, three starts): likely hours.
- `L_world`: a bounded random simulation, not an exhaustive check (the whole world at once is far beyond a
  laptop); 200,000 traces, an estimated 2-10 minutes. Raise `SIM_TRACES` for more coverage.
- `LL_jobs` (the large liveness world of 84c8b8c9) is dropped: its properties are checked in the small
  liveness worlds, and TLC checks liveness 20-40 times slower per state.

## Changing the model

- Edit `EngineJobs.tla`; edit the worlds and mutants in `gen_cfgs.py`, then run
  `python3 formal/tla/engine-jobs/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_engine_jobs.py` checks that the property and mutant names still
  match the spec's §13 and that MAPPING.md lists exactly the model's actions.
