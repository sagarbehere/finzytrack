# How to run the mirror-publish model checks

The TLA+ model of the mirror's generation publish (`design/specs/mirror/publish.md` §10; durability only, EP-19,
BL-0113). It runs as a plain shell script with Java; no AI is involved. MAPPING.md says what the model checks and how
it maps to the spec and the code.

## Before the first run

- Java 21 and nothing else: the first run downloads `tla2tools.jar` 1.7.4 into `formal/tla/tools/` and checks its
  SHA-256 against `formal/tla/tools.lock` (`formal/tla/fetch_tools.sh`).

## The commands

From the repository root (`run_all.sh` calls the shared runner `formal/tla/run_model.sh`, which the engine-jobs model
uses too; its options and results are described in `formal/tla/engine-jobs/HOW_TO_RUN.md`):

```sh
formal/tla/mirror-publish/run_all.sh --tier S          # small worlds, every mutant and witness: about 20 s
formal/tla/mirror-publish/run_all.sh --tier M          # M_all: not measured (the owner's)
formal/tla/mirror-publish/run_all.sh --tier L          # L_all: not measured (the owner's)
formal/tla/mirror-publish/run_all.sh --list            # what would run; runs nothing
```

Results go to `results/` (not committed), with `results/SUMMARY.md`.

## How long it takes

Measured on the owner's Mac (Apple M4 Pro) on 2026-11-08 with 8 TLC workers, on the model of S0085's round 1 fixes
(model sha256 90acfd933696):

| Tier | Configurations | Result | Size | Wall time |
|---|---|---|---|---|
| S | `S_publish`, `S_power`, `S_purge`, `S_ledger` | all PASS | 48,572, 85,363, 11,792 and 26,542 distinct states | 0-2 s each |
| S | 7 mutants | each violates its property | 294 to 23,481 distinct states | 0-1 s each |
| S | 11 witnesses | each reached | 136 to 17,831 distinct states | 0-1 s each |
| S | all 22 | **all as expected** | | about 18 s together |
| M | `M_all` | not run (the owner's, BL-0110) | | |
| L | `L_all` | in pieces (the owner's, BL-0110): no violation in 364M distinct states, depth 34, after 27 h (2026-11-14); see "Running `L_all` in pieces" | | |

Each run rewrites `results/SUMMARY.md` with the numbers per configuration.

## Running `L_all` in pieces (the owner's run, from 2026-11-13)

`L_all` is too large for one sitting: after 27 h it had found 364M distinct states, every behaviour up to depth 34, no
violation, with 125M states still queued (2026-11-14 11:50). It runs in pieces, from TLC's own checkpoint (TLC writes
one every 30 minutes: "Checkpointing completed at ..." in the log). Its state lives in
`results/states/L_all/26-11-13-08-39-33/` (about 30 GB, ignored by git); keep it until the run ends. Do not change
`MirrorPublish.tla` or `cfg/L_all.cfg` meanwhile: a checkpoint resumes only the same model.

**Resume** (from `formal/tla/mirror-publish/`; `run_all.sh` is not used here, since it deletes the state folder when
a configuration starts or stops):

```sh
cd formal/tla/mirror-publish
echo "=== resumed $(date '+%Y-%m-%d %H:%M:%S') from the checkpoint" >> results/L_all.log
nohup caffeinate -i nice -n 10 java -XX:+UseParallelGC -XX:MaxRAMPercentage=50 \
  -cp ../tools/tla2tools.jar tlc2.TLC -config cfg/L_all.cfg -workers 14 \
  -recover results/states/L_all/26-11-13-08-39-33 -nowarning MirrorPublish.tla >> results/L_all.log 2>&1 &
```

The log should show TLC starting its recovery from the checkpoint and then `Progress(...)` lines with counts above the
last ones. If the recovery fails, the choices are a fresh run (`run_all.sh --only L_all --force`, from the start) or
the bounded result above as a stated limit.

**Pause** (any time it is running): wait for the next `Checkpointing completed at` line after the resume, then stop
the process (`pkill -f 'tlc2.TLC -config cfg/L_all.cfg'`). Stopping between checkpoints loses at most the half hour
since the last one, but stop right after a completed checkpoint: the queue files a checkpoint names may already be
consumed after it. A Mac that sleeps is fine; `caffeinate -i` keeps it awake while the run lasts.

**When it ends**: `Model checking completed. No error has been found.` (or a violation with its trace) is the last
part of `results/L_all.log`. Record the result in the table above (distinct states, depth, the wall time summed over
the pieces), run `formal/tla/mirror-publish/run_all.sh --tier L --list` to see the summary's view, delete
`results/states/` (30 GB or more), and close BL-0110. A violation is read from its trace first: it becomes a test or a
backlog row, unless it is a High data-safety issue (PROCESS.md §3).

## Changing the model

- Edit `MirrorPublish.tla`; edit the worlds, mutants and witnesses in `gen_cfgs.py`, then run
  `python3 formal/tla/mirror-publish/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_mirror_publish.py` checks the names against the spec and MAPPING.md.

## The conformance traces (S0091)

`traces/corpus.json` holds TLC-simulated traces of small worlds that `engine/tests/mirror/test_publish_conformance.py`
replays through the rebuild and the publish over the in-memory file system (no Java at test time; MAPPING.md §5).
Regenerate it after any change to `MirrorPublish.tla`, to a `.cfg` it uses, to its generator or to the engine-jobs
generator it imports (the test and `scripts/tests/test_tla_mirror_traces.py` fail until then, naming this command):

```sh
python3 formal/tla/mirror-publish/gen_traces.py           # about 15 s (3 worlds); Java 21 and the pinned jar
python3 formal/tla/mirror-publish/gen_traces.py --check   # is the corpus current? (no Java)
```
