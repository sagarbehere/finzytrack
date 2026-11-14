# How to run the engine-jobs model checks

The TLA+ model of the durable core of `design/specs/engine-jobs.md` (§13: the save, its rollback, recovery, Keep,
create and copy under crashes, power loss, I/O errors and an outside editor; durability only, EP-19, ADR-0068 D5).
It runs as a plain shell script with Java; no AI is involved. MAPPING.md says what the model checks and how it maps
to the spec and the code.

## Before the first run

- Java 21 (`java -version` should say 21; Temurin 21.0.8 on the owner's Mac).
- Nothing else: the first run downloads `tla2tools.jar` 1.7.4 into `formal/tla/tools/` and refuses it unless
  its SHA-256 equals the one in `formal/tla/tools.lock` (`formal/tla/fetch_tools.sh`). The jar is not committed.

## The commands, per tier

From the repository root:

```sh
formal/tla/engine-jobs/run_all.sh --tier S                 # small worlds, every mutant and witness: about 1-2 min
formal/tla/engine-jobs/run_all.sh --tier M                 # medium (M_recover_temps): seconds
formal/tla/engine-jobs/run_all.sh --tier L                 # large (L_save_all, L_recover_all, L_save_deep, L_world): about 16 h
formal/tla/engine-jobs/run_all.sh --only L_save_deep       # one configuration
formal/tla/engine-jobs/run_all.sh --kind mutant            # only the seeded bugs (--kind witness: only the witnesses)
formal/tla/engine-jobs/run_all.sh --list                   # what would run; runs nothing
```

An unknown or empty `--tier`, `--kind` or `--only` value is refused (exit 2), so a typo never runs nothing quietly.
Options: `--workers N` (TLC worker threads; default all cores), `--heap 12g` (default half the RAM),
`--force` (run again configurations that already have a result). `SIM_TRACES=… SIM_DEPTH=…` size the simulation
of `L_world` (default 200,000 traces of at most 200 steps).

## What it does

- It runs the configurations in `configs.txt`, in that order. Each line says the result it must have:
  - `PASS` for a base world (kind `base`);
  - `VIOLATE:<property>` for a seeded bug (kind `mutant`);
  - `VIOLATE:W_<name>` for a vacuity witness (kind `witness`): a base world with the negation of a branch as its
    invariant, so TLC must find a state where the branch is reached (MAPPING.md §6.2).
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
- After a power loss during a run, delete `formal/tla/engine-jobs/results/states/` by hand.

## Reading the results

| Result | Meaning |
|---|---|
| `PASS` as expected | the design holds in that world |
| `VIOLATE:<property>` as expected | a mutant breaks exactly that property, so the model can catch that mistake |
| `VIOLATE:W_<name>` as expected | the world reaches that branch, so its `PASS` is not vacuous |
| a row with "As expected = no" | needs a look: read its log (TLC prints the shortest trace that breaks the property) |

## How long it takes

Measured on the owner's Mac (Apple M4 Pro, 14 cores, 48 GB) on 2026-11-01 with 14 TLC workers, on the model of S0072
round 1 (model sha256 23acd902b1d1); the S tier again on 2026-11-11 on S0101's model (a folder sync covers entries only, `RS_Flush`). Earlier models' numbers (the queue, the screens and liveness) are in git history.

| Tier | Configurations | Result | Size | Wall time |
|---|---|---|---|---|
| S | 14 base worlds | all PASS | 20 (`S_create_nonempty`) to 235,407 distinct states (`S_keep`) | 0-3 s each |
| S | 29 mutants | each violates its property | | 0-2 s each |
| S | 24 witnesses | each reached | | 0-2 s each |
| S | all 67 | **all as expected** | | 69 s together (S0101) |
| M | `M_recover_temps` | PASS | 85,156 distinct states | 5 s |
| L | `L_save_all` | PASS | 670,086 distinct states | 61 s |
| L | `L_recover_all` | PASS | 4,088,438 distinct states | 243 s |
| L | `L_save_deep` | PASS | 2,167,593,483 distinct states, depth 98 | 55,417 s (15.4 h) |
| L | `L_world` (bounded simulation, 200,000 traces of at most 200 steps) | PASS | | 512 s |

The medium and large rows were measured on the owner's Mac on 2026-11-13 and 2026-11-14 with 7 TLC workers, beside
another run (the mirror-publish `L_all`), on S0111's model (model sha256 02e556707512): all 72 configurations as
expected (BL-0110). The model changed after them only in a comment of `EditKept` (S0111's close: §6.4, and the
"Limit (older backup)" note), so their results hold for the current model, but `run_all.sh` hashes the whole file and
shows their logs as `stale`; a rerun is needed only for a clean summary.

The model lost the queue, the sessions' screens and the engine's processes in S0072, so its worlds are far smaller
than before (`S_save_crash`: 2,711,371 distinct states before, 21,236 now). The medium and large worlds combine more
faults (two or three saves, two power losses, three starts, every boot journal with its temps).

## The conformance traces (S0064, S0072, S0084, S0091)

`traces/corpus.json` holds TLC-simulated traces of small save, create, copy and recover worlds that
`engine/tests/ledger/test_save_conformance.py`, `test_create_conformance.py` and `test_recover_conformance.py` replay
through the engine over the in-memory file system (no Java at test time). Each trace is stored as
steps that name only the variables that changed (`gen_traces.delta`). Regenerate it after any change to
`EngineJobs.tla`, to a `.cfg` it uses or to the generator (the test fails until then, naming this command):

```sh
python3 formal/tla/engine-jobs/gen_traces.py           # about two minutes (31 worlds); Java 21 and the pinned jar
python3 formal/tla/engine-jobs/gen_traces.py --check   # is the corpus current? (no Java)
```

The worlds, the seed, the trace depth and what the walk leaves out are in the generator's header; MAPPING.md §7 says
how the model's actions map to the code.

## Changing the model

- Edit `EngineJobs.tla`; edit the worlds, mutants and witnesses in `gen_cfgs.py`, then run
  `python3 formal/tla/engine-jobs/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_engine_jobs.py` checks that the property and mutant names still
  match the spec's §13 and that MAPPING.md lists exactly the model's actions.
