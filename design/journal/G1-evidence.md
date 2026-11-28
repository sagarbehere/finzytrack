# G1: the go/no-go evidence pack (2026-11-28)

The checkpoint after M1b, before any write capability ships (ADR-0046; roadmap §4 G1). This record holds the evidence and, below, the owner's decision. G1 ran on 2026-11-28 from M1b's exit (d3022ac4): slices S0163 to S0165 (G1), S0166 (M2's first, beside it).

## Decision
- Pending: the owner's answer (go; go with named changes; no-go).

## The criteria (ADR-0046)

| # | Criterion | Result | Met |
|---|---|---|---|
| 1 | A full manual CI run green at G1 | green at 01a5bfa8 (every job of T1 and T2), after one test-placement fix | yes |
| 2 | ≥ 200 old-vs-new comparisons, none unexplained | 397 comparisons; 395 classed by the harness, 2 explained by hand; no booked difference in any | yes |
| 3 | < 5% unjustified refusals on copies of the owner's books | 0 of 128 run (0%; 95% upper bound about 2.3%) | yes |
| 4 | A write ≤ 1 s at demo size, ≤ 5 s at 54k, mirror update included | each wait meets it (owner's reading, below): demo 0.45 s / 0.58 s; 54k 3.8 s / 3.1 s | yes |
| 5 | ≥ 80% of ≥ 20 planted bugs caught, ≥ 4 classes, ≥ half in product code | 24 of 28 (85.7%); product code 21 of 23 (91.3%) | yes |
| 6 | Pace and projected time to v0.4.0 (no ceiling) | below | the owner's judgement |
| 7 | Cross-cutting lens reviews and a spot audit (PROCESS.md §4) | three lenses and a spot audit: no High | yes |

### 1. The full manual CI run
`run-next-ci` on `main` dispatches T1 (`ci.yml`) and T2 (`nightly.yml`: cargo-age and the packaged builds) on a ref of `next`. M1b-exit.md's line that `nightly.yml` "cannot be started by hand" was wrong: this dispatcher runs it (it last ran at M1a's exit). 
- At 30368ddc: every job green except `engine-windows`, 2 failed of 12,556 (12,554 passed, 49 skipped): S0165's new test over the in-memory fake went into a module of real-disk tests, which made the conftest run that module with the POSIX path model on Windows. Tests only; fixed in 6c05834d (S0165's journal, "After landing"). Green there: repo-checks, backend, backend-windows, engine (Linux), frontend, shell, oracle-unit, cargo-age (all crate dates re-read), packaged builds on macOS, Linux and Windows.
- At 01a5bfa8 (2026-11-28): **success**, every job green: repo-checks, backend, backend-windows, engine (Linux), engine-windows, frontend, shell, oracle-unit, cargo-age, and the packaged builds on macOS, Linux and Windows.
- A run the coordinator started by mistake with a made-up commit failed at checkout and is not evidence (the cancel was refused by the permission check).
- macOS: the T0 pre-push gate passed at each push (d3022ac4..30368ddc, 30368ddc..01a5bfa8); the owner's acceptance run through the harness after S0166: 39 passed, 21 skipped.

### 2. Old-vs-new comparisons (`scripts/oracle/harness.py`)
- **Repo corpora (S0164's runs):** 127 at the default size (nasty 9, demo 15, the write corpus's 17 load folders 103) and 210 on the demo (70 per mutator, seed 9): 336 classed, 1 left: write corpus `documents` ren-004, explained by hand (the legacy writes the folder scan's two generated documents with the renamed account; the folder still has the old name; documents only, no money; BL-0274's closing line).
- **The owner's books** (L1, the main ledger; the owner's `cp -RL` copy in the T-local folder; 20 per mutator, seed 9; run by the owner at 30368ddc): 60 cases: 32 MATCH, 27 EXPECTED_DIVERGENCE (FIX-L3-02 5, FIX-L3-05 2, M28 18, FIX-L3-05+M28 2), 1 UNEXPLAINED: upd-002. Diagnosed by the coordinator on structure only (no ledger content read): the ledger set held exactly the intended change; the one divergence was a new file beside the root, temp-shaped (a `.prices.beancount` name with a random suffix), about a ledger's worth of text with no price directive, in the run's slowest case (68.8 s). Run again alone in a fresh work folder by the owner: MATCH (11.9 s). Explained by hand: a temp of a legacy write left when the harness stopped the legacy app; nothing of the ledger set diverged.
- The owner's second root (L2, a CAS test copy of the main ledger) is left out (owner, 2026-11-28: "skip it").
- **The rule behind the count:** BL-0047 was built for G1 (owner, 2026-11-28: "Build BL-0047 now (Recommended)"; S0164): a new Beancount error is explained only when undoing its tagged cause leaves exactly the reference's errors.

### 3. Refusals on copies of the owner's books (`scripts/oracle/g1_refusals.py`, S0163)
- On the owner's copy (L1), run by the owner from a clean clone at 10fca248: `--per-mutator 50`: 150 generated, 128 run (50 updates, 28 renames, 50 imports), 22 not expressible (renames whose account also appears in a metadata or `custom` value: no metadata primitive until BL-0313, M4), **128 saved, 0 Save anyway, 0 refused**. The first run at 20 per mutator: 49 run, 49 saved.
- On the repo corpora: demo 57 of 57 saved; nasty 19 of 19; the write corpus 125 saved, 3 refused (2 justified: edits of an encrypted-named file, ADR-0059; 1 unjustified: an insert into the root refused because the ledger includes a plain-text `.asc` file, BL-0319, M4): 0.8%.
- **What it measures:** the write core (the splice, the contract's steps, the save) through the engine's test producer: no mutator exists yet. The tool's stated limits (S0163's journal) were accepted by the owner ("All as stated limits"); every refusal it calls justified is read by hand before it enters this pack (there were none on the owner's books).

### 4. Write cost (`backend/scripts/measure_write.py`)
Measured by the coordinator at 30368ddc on the owner's Mac (load 2.3 falling to 1.8), 5 runs each, the generator's ledgers (anchor 2026-03-31):

| Ledger | Preview | Confirm to the mirror published | Total (for the record) | Peak memory (preview's engine) |
|---|---|---|---|---|
| Demo | 0.45 s | 0.58 s | 1.03-1.04 s | 95 MB |
| 54k, one 8.7 MB file | 3.77-3.82 s | 3.07-3.09 s | 6.85-6.91 s | 488-498 MB |
| 54k, 12 files | 2.09-2.11 s | 3.01-3.03 s | 5.11-5.12 s | 367-369 MB |

- **The reading** (owner, 2026-11-28: "Each wait counts (Recommended)"; ADR-0008 amendment of 2026-11-28; S0165): the preview's wait and the wait from Confirm to the mirror's publish each meet the target. Every wait does, at both sizes; the totals are over and are reported here.
- The timing stops at the mirror's publish, not at the screen's redraw (no screen exists until M2's views).
- The spot audit reran S0119's numbers independently and found them reproducible.

### 5. Seeded defects (plants in throwaway clones of d3022ac4; never in `next`)
28 plants, written from product code and specs before any test was read, each alone, against the test suites the push gate runs:

| Class | Plants | Caught |
|---|---|---|
| Write path (save, contract, journal and recovery, backups, the gate, confinement) | 11 | 10 |
| Mirror (build, publish, freshness, reads) | 7 | 6 |
| Finance (money and dates, precision, the D1 diff) | 5 | 5 |
| Not product code (privacy, guards, check scripts) | 5 | 3 |
| **All** | **28** | **24 (85.7%)** |

- The four survivors were real gaps (the read-back compared by length only; a generation name matched by prefix; a float literal passing the money check; a threshold off by one); each became a test in S0165 that kills its plant.
- One more plant was an equivalent mutant (SQLite hands the authorizer lower-case names) and was replaced.
- Limits of the run: the sandbox refuses `ps` and `diskutil`, so the backend's process tests were skipped and one macOS volume test fails on every tree; lint, types and the frontend were not run.

### 6. Pace and projected time to v0.4.0
| Milestone | Span | Slices | ADR-0057's estimate | Ratio |
|---|---|---|---|---|
| M0 | 2026-10-24 to 10-26 | S0001-S0034 | rest of M0: 12-16 | — |
| M1a | 2026-10-26 to 11-13 (18 days) | 79 | 12-18 | 4.4-6.6× |
| M1b | 2026-11-13 to 11-28 (15 days) | 49 | 15-25 | 2.0-3.3× |
| G1 (with M2's first) | 2026-11-28 | 4 | — | — |

- **Throughput:** 3.3-4.4 slices a day; 15-18 days per L-sized milestone.
- **Projection by duration:** M2-M12 are 6 L (M2, M3, M4, M5, M9, M11), 3 XL (M6, M7, M8; an XL about 2-3 L) and 2 M (M10, M12; about half an L): 13-16 L-equivalents, 195-288 days, plus the 4-week dogfood window: about 7.5-10.5 months, so **v0.4.0 between mid-July and mid-October 2027**.
- **Projection by slices:** ADR-0057 estimates 143-213 slices for M2-M12; M1 ran 3.0-4.7× its estimate; at that ratio and today's throughput, 430-1,000 slices in about 100-300 days. The two methods overlap.
- **Why the range is wide:** the write core carried the heaviest review (red teams, formal models, Windows and OneDrive edge cases: S0144-S0152); read-only screens use the light lane. Against that, each write vertical waits for its promotions and clearance periods (roadmap §5), and the parity work in M6-M8 is the largest.
- **Owner time:** not measured (as in M1a and M1b); the owner's manual steps at G1: two ledger copies' runs (refusals, oracle, one rerun), the package install, the acceptance run, and eight decisions.

### 7. Lens reviews and the spot audit (frozen d3022ac4)
No High in any. Triage in S0165 (`design/journal/S0165-g1-review-findings.md`, every finding ID with its disposition):
- **L1, simplicity:** 3 Medium, 6 Low, all class (e). The create branch, the engine's test-copy confinement and the app folder's five identity checks kept as they are (owner, 2026-11-28; `design/inbox/answered/20261128-g1-review-proposals.md`); the rest fixed, rows BL-0321, or dropped with reasons.
- **L2, truthful outcomes across processes:** 13 paths traced; 2 Medium, 7 Low. L2-F1 (after an engine death an unreadable file was called "changed by another program"; "not saved" was true) fixed with tests; a §11 spec row that would have built a false "saved" corrected; the sidecar now checks `committed` against the final reply.
- **L3, do the tests prove what they claim:** "unusually strong"; 1 Medium (the fake folds names only as APFS does: a planted bug in the confined save passed 11,347 tests; owner: "Keep; fix fake at M3 (Recommended)", BL-0322), 4 Low fixed.
- **Spot audit (S0119, S0149):** both trustworthy on facts (every number and base failure reproduced; quotes verbatim). Its process finding SA-F1 ("lands" commits before the verifier) changed PROCESS.md §3 step 7 (owner: "Hand-off, then lands (Recommended)"); SA-F2 led to the owner's reading of the write criterion above.

## What G1 found that the earlier gates did not
- One real over-refusal in the write core (BL-0319: a plain-text `.asc` include blocks inserts into the root).
- One misleading cause text after an engine death (L2-F1, fixed).
- Four test gaps that let a planted bug through (fixed), and a fake that cannot model Linux or Windows name rules (BL-0322, M3).
- A Windows-only test placement slip, caught by the full CI run (fixed).

## The options (ADR-0046)
- **Go:** M2 continues on the product path; the first mutators (M3, M4) are promoted under ADR-0014 as planned.
- **Go with named changes:** for example, any of the simplifications still open in `design/inbox/20261128-m1b-simplifications.md` (five proposals from the M1b area review, unanswered), or a change of pace or process.
- **No-go:** fall back to incremental work on the v0.3.0 code, moving the contract and engine in first; the gates, specs, corpora and harness carry over.

## Copies of the owner's ledger
The owner's copy in the T-local folder (with the oracle's work folders and reports) is no longer needed once the decision is recorded; the owner deletes them.
