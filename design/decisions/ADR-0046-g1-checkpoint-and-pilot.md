# ADR-0046: The G1 go/no-go checkpoint, the pilot, and the M1a/M1b split

This record decides when and how the owner judges whether the rebuild continues, and what the first slices are.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner (G1 criteria, pilot); agent (M1 split, from the final review)
- **Area:** process, planning

## Context

The rebuild needs a kill/continue point after the write core. The first G1 criteria were mostly "no failures", which cannot fail by then, and nothing measured pace. The write contract needs workspace concepts (the full extended fingerprint and the layout), so M1 had a hidden dependency.

## Decision

(owner, 2026-10-24)
- **G1 sits after M1b**, before any write capability ships. Measurable go criteria:
  - ~~14 green nightly runs in a row~~ replaced (owner, 2026-10-25, with ADR-0060): a manual full GitHub run (`ci.yml` + `nightly.yml`) green at each milestone exit (M1a, M1b) and at G1;
  - at least 200 old-vs-new comparisons, none unexplained;
  - under 5% unjustified refusals on copies of the owner's books;
  - a write takes **≤ 1 s at demo size and ≤ 5 s at 54k entries**; these are also the app's user-facing performance targets (INV-20);
  - at least 80% of planted bugs caught (a sample of ≥ 20 plants across ≥ 4 classes);
  - **no fixed time-to-finish ceiling:** the G1 evidence pack shows the measured pace and the projected time to a releasable v0.4.0, and the owner judges it then.
- D2 equivalence exact on every corpus ledger and real-ledger copy, and one load per write, stay M1b exit criteria.
- **Options at G1:** Go; Go with named changes; No-go (fall back to incremental work on the v0.3.0 code, moving the contract and engine in first; nothing built in M0–M1 is wasted).
- **G2** is a confidence checkpoint (not a kill point) **after M4**, when the first replace/delete and multi-entry mutators are promoted.
- **Pilot:** the per-feature **"intended change" table** (the per-mutator intent table M1 needs anyway), run under the full process. After 3–5 slices the rubric and gates are adjusted and the result is recorded as an ADR; then a rough slice count for M1–M12 is estimated.
- **M1 is split:** **M1a** "read core + workspace-format core" (mirror, overlay load and `beancount_port`, platform ports, the first domain types, the workspace-format core with the extended fingerprint and persisted `ledger_version`, the API skeleton) and **M1b** "write contract" (`ledger/core`, the contract, writer, journal, promotion machinery, the TLA+ and Hypothesis models, the oracle classifiers). M2 (read-only shell) may start after M1a, in parallel with M1b.

## Alternatives considered and rejected

- **Keep the original "no failures" criteria.** Rejected: they cannot fail and do not measure pace.
- **A fixed time ceiling set now.** Rejected by the owner in favour of judging the measured pace at G1.
- **Piloting on the write path itself.** Rejected: an uncalibrated process should not meet the riskiest code first; WS-WRITE stage 1 is the calibrated second run.
- **Re-cutting M2/M3 for earlier visibility.** Deferred: early visibility is not a goal.

## Consequences

- The milestone plan (review:27) carries the binding milestone exits.

## References

review:F §2 item 5, §3.12, §4 (F44); review:27 §0.1, §2, G1; review:20 §10; review:21 §5 item 8; ADR-0001, ADR-0002, ADR-0014; INV-20.
