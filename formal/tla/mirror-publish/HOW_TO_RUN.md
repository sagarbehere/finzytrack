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

Measured on the owner's Mac (Apple M4 Pro) on 2026-11-08 with 8 TLC workers, on the model of S0085:

| Tier | Configurations | Result | Size | Wall time |
|---|---|---|---|---|
| S | `S_publish`, `S_power`, `S_ledger` | all PASS | 53,342, 80,227 and 28,450 distinct states | 1 s each |
| S | 7 mutants | each violates its property | 240 to 24,736 distinct states | 0-1 s each |
| S | 9 witnesses | each reached | | 0-2 s each |
| S | all 19 | **all as expected** | | about 20 s together |
| M | `M_all` | not run (the owner's, BL-0110) | | |
| L | `L_all` | not run (the owner's, BL-0110) | | |

Model sha256 4ef12f36ab63. Each run rewrites `results/SUMMARY.md` with the numbers per configuration.

## Changing the model

- Edit `MirrorPublish.tla`; edit the worlds, mutants and witnesses in `gen_cfgs.py`, then run
  `python3 formal/tla/mirror-publish/gen_cfgs.py` (it rewrites `cfg/` and `configs.txt`).
- `python3 -m unittest scripts/tests/test_tla_mirror_publish.py` checks the names against the spec and MAPPING.md.
