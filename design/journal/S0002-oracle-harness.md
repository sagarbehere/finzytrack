# S0002: Oracle harness for three mutators (spike S9)

## Brief
- Goal: run three legacy v0.3 mutators (update transaction, rename account, import append) end to end against the legacy process on ledger copies, compare each result with the mutator's intended change through a parser-level semantic diff (D1 step 2), and tag every residual divergence with an ADR-0014 class. Spike S9's criterion (review:C §5): end to end in ≤ 1 agent-day per mutator adapter, ≤ 10 s per comparison at demo size; fallback if it fails: oracle comparisons become T2-only and sampled.
- Implements: ADR-0014 (old app as differential oracle; expected divergence classes I2, I3, I10, M28, FIX-<id>; non-comparable intents); review:D1 §3 step 2, §12.5, §12.6, §12.8; review:27 §1 (the harness); backlog WS-QA item 16.
- Capabilities: none (process tooling; no capability row, as for the other `scripts/`).
- Invariants touched: INV-24 (the harness only ever runs on copies; refuses the live data folder, the legacy worktree and this repo; never writes into the source ledger folder; real-ledger runs print counts and case IDs only), INV-23 (corpus fixtures are synthetic), INV-02 (Beancount 3.2.0 parses; nothing is reimplemented).
- Risk class / lane: Medium / full (test tooling, but it produces the evidence used to promote ledger writes and it drives a process on ledger copies). **Raised to High by the verifier** (its classifiers decide whether a wrong number counts as expected, and F1 reached outside the copy); not a write slice, so no data-safety red team.
- Acceptance tests (`scripts/oracle/tests/`, run with the harness's pinned env):
  - semantic diff: location-only meta ignored; per-file multisets (a duplicate entry counts twice); entry moved between files shows as remove + add; options/plugins/includes compared; glob includes followed; parser only, no plugins or booking.
  - each classifier tags a planted divergence of its class and nothing else: I2 (glob-included entries copied into the root), I3 (plugin or pad-generated entries persisted), I10 (elided amounts or `{}` baked in; comments lost), M28 (metadata-contract keys), FIX-L3-02 (`@@` turned into `@`), FIX-L3-15 (typed metadata turned into strings); anything else is `UNEXPLAINED`.
  - expected-change models for the three mutators on hand-written fixtures.
  - harness guards: refuses the live data folder, the legacy worktree and the repo as `--ledger-dir`; the source folder is byte-identical after a run; a real-ledger run (`--ledger-dir` outside `corpora/` and not generated) prints no entry text.
  - end to end (local tier, needs the legacy worktree): each mutator on the demo-size fake ledger and on the nasty fixtures, with the per-comparison time.
- Expected doc impact: `design/ARCHITECTURE.md` scripts list (oracle/ contents); `design/backlog.md` WS-QA item 16 status; this journal; the CI workflow gains the oracle unit-test job. No user docs.
- Open questions: none for the owner yet. The intended change for rename on accounts with subaccounts and on metadata values that name the account is not decided by ADR-0049; the harness avoids those cases and the pilot slice (the intent table) settles them.
- Base SHA: 2c2a6dc.

## Decisions
- The harness has its own pinned environment (`scripts/oracle/pyproject.toml` + `uv.lock`, Python 3.13, Beancount 3.2.0) instead of borrowing the legacy venv, so its comparison does not depend on the oracle's own install and CI can run its unit tests (agent).
- Dev tooling, not bundled: the harness importing Beancount does not touch ADR-0054's process split or INV-02's "only `ledger.beancount_port` imports Beancount" rule, which apply to the app's packages (agent).
- One fresh legacy process per case, on its own copied root and port: no shared state between cases, and a crashed case cannot taint the next (agent; measured against the ≤ 10 s budget).
- The expected-change models here are provisional and minimal; the per-mutator intent table (pilot slice) is the authority and replaces them (agent).

## Verifier
- Round 1, head aff2f22: **FAIL**. Attacks tried (11): eraser over-matching per class; parse errors in the legacy output; the refusal path with an unbalanced priced intent; includes pointing outside the staged copy, end to end; `--report` and case-id paths; Beancount cache writes into the source (none: cache disabled); redaction and CLI coverage with a mutant (`redact = lambda r: r` survived); end to end on nasty and demo with a full load of the legacy output; uv `exclude-newer` with `--locked` in CI (fine); unit suites; doc and ADR coverage.
- Findings and resolution (fix commit below):
  - F1 High (INV-24): an absolute or `../` include let the legacy app rewrite a file outside the copy. Fixed: `parse_set` lists include targets that resolve outside the folder; the harness refuses such a set before staging and on every staged copy (`check_contained`), and stops the run if the source folder's bytes change (exit 3). The verifier's repro now prints "refused" and the outside file is untouched.
  - F2 High (ADR-0014, ADR-0026, INV-21): M28 erased keys on every entry. Fixed: M28 applies only to entries the mutator creates (`Expected.created`); it drops a v0.4 key from the intended side only when the legacy side lacks it, and a legacy machinery key from the legacy side only when the intended side lacks it, so a key both carry is still compared. Untargeted entries may differ only by I10.
  - F3 High: I10 accepted any baked number. Fixed: the baked units or lot must equal Beancount's own booking of that entry (`LoadFacts.booked_at`); an intended entry without a booked form may only have elided units filled, and a full load of the legacy output turns any new Beancount error into a blocking divergence.
  - F4 High (L3-02, INV-02): the price eraser ran on every priced posting with a fixed cent. Fixed: it runs only on postings the source wrote with `@@` (read from the text) and recognises only the legacy's own rounding rule (|units × unit price − total| < 0.005, cited from the legacy commit endpoint); a lot may lose only its label (and date), or the whole cost only when it had no date.
  - F5 High: parse errors were never compared. Fixed by the load-error comparison (error keys per review:D1 §12.1).
  - F6 Medium: a refused priced edit was FIX-L3-02 without checking it balances. Fixed: only when Beancount itself accepts the intent on its own (`classify.balances`).
  - F7 Medium: `--report` was unguarded. Fixed (`refuse_report`; also `--work` inside the ledger folder is refused).
  - F8 Low: case ids were paths. Fixed (`check_cases`: a short name of `[A-Za-z0-9._-]`, unique, known mutator).
  - F9 Medium: `harness.py` had no tests. Fixed: `tests/test_harness.py` (CLI refusals before any process starts, `redact`, `stray_files`, containment of a copy).
  - F10 Low (INV-24 enforced-by list names only the launcher): the list is in the owner's file; proposed in `design/inbox/20261025-inv-24-enforced-by.md` rather than edited here.
  - The verifier's egress question (the legacy process had network access at aff2f22): S0003 starts every legacy process offline (`launch_legacy.offline_env`).
- Re-run after the fixes, end to end: demo 15 cases, 13 EXPECTED_DIVERGENCE + 2 NON_COMPARABLE, p95 4.6 s per case (a second full load per case; budget 10 s). Nasty 9 cases: 6 EXPECTED_DIVERGENCE, 1 NON_COMPARABLE, and **2 UNEXPLAINED** (ren-coffee, ren-rent): the legacy rename renames the `open` but never rewrites the glob-included files (I2), leaving "Invalid reference to unknown account" errors. Correctly blocking: a rename on a glob ledger is not comparable with the old app until a class for I2's follow-on errors exists (BL-0047).

## Retro
- The verifier found five High defects the gates could not: every one was an eraser or guard that was too permissive, and every unit test written so far asked "does the class fire", never "does it fire on something it must not". Prevention: each classifier now has a negative test beside its positive one (`OverMatchTest`), and this pattern is noted for the reflection pass as a candidate verifier-checklist line.
- A cheaper gate for F1: a containment check in the copy guard from the start; INV-24 thinking stopped at "the root folder" and did not follow the includes.
- Where time went: harness design and the legacy's quirks (refusals, glob literal files) more than code; the S9 criterion itself (≤ 10 s per comparison) was never close.
- Process cost: implementation ≈ 1 h 20 min; brief + docs ≈ 15 min; verifier ≈ 8 min wall; fixes ≈ 45 min. Ceremony share ≈ 0.3 before fixes.
