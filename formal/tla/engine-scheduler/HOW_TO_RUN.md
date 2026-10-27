# How to run the engine-scheduler model checks

The TLA+ model of `design/specs/engine-scheduler.md` §13 (draft 5). It runs as a plain shell script with
Java; no AI is involved. MAPPING.md says what the model checks and how it maps to the spec.

## Before the first run

- Java 21 (`java -version` should say 21; Temurin 21.0.8 on the owner's Mac).
- Nothing else: the first run downloads `tla2tools.jar` 1.7.4 into `formal/tla/tools/` and refuses it unless
  its SHA-256 equals the one in `formal/tla/tools.lock` (`formal/tla/fetch_tools.sh`). The jar is not committed.

## The commands

From the repository root:

```sh
formal/tla/engine-scheduler/run_all.sh                         # everything: small, medium, large
formal/tla/engine-scheduler/run_all.sh --tier S                # the small tier and every mutant (minutes)
formal/tla/engine-scheduler/run_all.sh --tier S,M              # add the medium tier
formal/tla/engine-scheduler/run_all.sh --kind mutant,finding   # only the seeded bugs and the findings
formal/tla/engine-scheduler/run_all.sh --only L_world          # one configuration
formal/tla/engine-scheduler/run_all.sh --list                  # what would run; runs nothing
```

Options: `--workers N` (TLC worker threads; default all cores), `--heap 12g` (default half the RAM),
`--force` (run again configurations that already have a result).

## What it does

- It runs the configurations in `configs.txt`, in that order: small configurations and mutants first,
  then medium, then large. Each line says the result it must have: `PASS`, or `VIOLATE:<property>` for a
  seeded bug (a mutant) or a finding (the spec as written, without one of the model's fixes).
- One log per configuration: `results/<name>.log`.
- After every configuration it rewrites `results/SUMMARY.md`: result, expected result, distinct states,
  depth, wall time and distinct states per second per configuration, the total time per tier, and
  **ALL AS EXPECTED** or **SURPRISES**.
- TLC runs under `nice -n 10`, so the Mac stays usable.
- **Ctrl-C** stops the running TLC, deletes its temporary state folder (`results/states/`), records the
  stopped run as `INCOMPLETE` with the states it reached, and writes the summary. Run the same command
  again to resume: finished configurations are skipped while neither the model nor their `.cfg` has
  changed; the stopped one starts again from the beginning.
- A large run's state folder can reach several GB; keep about 20 GB free. If the Mac loses power during a
  run, delete `formal/tla/engine-scheduler/results/states/` by hand.

## Reading the results

| Result | Meaning |
|---|---|
| `PASS` as expected | the design holds in that world |
| `VIOLATE:<property>` as expected | the seeded bug (or the spec without a fix) breaks exactly that property: the model can catch that mistake |
| a row with "As expected = no" | needs a look: read its log (TLC prints the shortest trace that breaks the property) |

## How long it takes

Measured on the owner's Mac (Apple M4 Pro, 14 cores, 48 GB) on 2026-10-27, while other work was running
(load about 10); an idle Mac is faster.

Each large run below was stopped by me (owner's request: review the model before long runs); the
numbers are what it reached. TLC explores breadth first, so a run whose queue of unexplored states is
still growing has usually not reached the middle of its state graph; the estimates use that and the
measured rate, and are rough (they can be off by several times).

| Tier | Configuration | Result | Distinct states | Depth | Wall time | Rate (distinct/s) |
|---|---|---|---|---|---|---|
| S | 22 safety worlds | all PASS | 165 to 9,564,383 (`S_switch`) | 10-117 | 1 s to 144 s each; 234 s together | |
| S | 4 liveness worlds | all PASS | 2,194 to 927,317 (`LS_jobs`) | 43-102 | 2 s to 397 s each; 480 s together | |
| S | 51 mutants + 4 findings | all violate their property | | | 104 s together | |
| M | `M_r4_switch` | PASS | 149,002 | 104 | 5 s | 30,000 |
| M | `M_recover_temps` | PASS | 7,890,754 | 51 | 207 s | 38,000 |
| M | `LM_free` (liveness) | PASS on the model version just before the last two fixes; stopped on the final model | 2,184,080 (complete); 1,110,208 when stopped | | 1,124 s; stopped at 981 s | 1,900 / 1,100 |
| L | `L_save_all` | stopped | 23,527,076 (queue 3.0M, growing slowly) | 48 reached | 657 s | 35,800 |
| L | `L_r4_switch` | stopped | 23,671,693 (queue 2.6M) | 51 reached | 600 s | 39,500 |
| L | `L_session` | stopped | 16,834,865 (queue 7.6M, growing); 58,152,806 (queue 20.9M) in an earlier 20-minute run | 19-23 reached | 600 s; 1,200 s | 28,000-48,000 |
| L | `L_recover_all` | stopped | 9,359,587 (queue 3.2M); 40,066,906 (queue 10.7M, growing) in an earlier 20-minute run | 17-23 reached | 600 s; 1,200 s | 15,600-33,000 |
| L | `L_world` | stopped | 11,256,112 (queue 7.4M) | 8 reached | 600 s | 18,800 |
| L | `L_save_deep`, `LL_jobs` | not run on the final model | `LL_jobs` (earlier version): 1,297,684 in 1,200 s, queue growing | | | 1,100 |

Estimates for the full runs (6-8 workers on this Mac under load; an idle Mac with 14 workers is
perhaps 1.5-2 times faster):
- `L_save_all`, `L_r4_switch`: the queue had almost stopped growing, so roughly 2-4 times the states
  reached: 50-100 million states, about 30 minutes to 1.5 hours each.
- `L_session`, `L_recover_all`: the queue was still growing fast at 20 minutes: more than 200 million
  states; several hours each, and a few GB of state files.
- `L_save_deep`: a deeper version of `L_save_all` (two saves, two power losses, three starts): likely
  hours.
- `L_world` (the whole world at once): only depth 8 after 10 minutes with 7.4 million states queued;
  the worlds above reach depths of 50-120, so this is far beyond a laptop (days or more, and likely more
  disk than is free). It is listed so the owner can see the size; the smaller worlds cover its parts.
- Liveness runs (`LM_free`, `LL_jobs`) are 20-40 times slower per state than safety runs (TLC checks
  liveness mostly on one thread): `LL_jobs` likely many hours.
- The small tier with every mutant and finding takes about 14 minutes; with the medium tier about 35.

## Changing the model

- Edit `EngineScheduler.tla`; edit the worlds, mutants and findings in `gen_cfgs.py`, then run
  `python3 formal/tla/engine-scheduler/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_engine_scheduler.py` checks that the property and mutant
  names still match the spec's §13 and that MAPPING.md lists exactly the model's actions.
