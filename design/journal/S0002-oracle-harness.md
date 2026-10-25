# S0002: Oracle harness for three mutators (spike S9)

## Brief
- Goal: run three legacy v0.3 mutators (update transaction, rename account, import append) end to end against the legacy process on ledger copies, compare each result with the mutator's intended change through a parser-level semantic diff (D1 step 2), and tag every residual divergence with an ADR-0014 class. Spike S9's criterion (review:C §5): end to end in ≤ 1 agent-day per mutator adapter, ≤ 10 s per comparison at demo size; fallback if it fails: oracle comparisons become T2-only and sampled.
- Implements: ADR-0014 (old app as differential oracle; expected divergence classes I2, I3, I10, M28, FIX-<id>; non-comparable intents); review:D1 §3 step 2, §12.5, §12.6, §12.8; review:27 §1 (the harness); backlog WS-QA item 16.
- Capabilities: none (process tooling; no capability row, as for the other `scripts/`).
- Invariants touched: INV-24 (the harness only ever runs on copies; refuses the live data folder, the legacy worktree and this repo; never writes into the source ledger folder; real-ledger runs print counts and case IDs only), INV-23 (corpus fixtures are synthetic), INV-02 (Beancount 3.2.0 parses; nothing is reimplemented).
- Risk class / lane: Medium / full (test tooling, but it produces the evidence used to promote ledger writes and it drives a process on ledger copies).
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
- Head SHA verified:
- Attacks tried:
- Findings:

## Retro
-
