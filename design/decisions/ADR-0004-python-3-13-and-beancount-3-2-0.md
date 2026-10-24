# ADR-0004: Python 3.13 and Beancount 3.2.0 are pinned for v0.4.0

This record pins the Python runtime and the Beancount version, and states when they move.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** agent (proposal), owner (accepted with the final-review corrections)
- **Area:** stack

## Context

- The overlay loader (ADR-0010) is a replica of Beancount 3.2.0's `_parse_recursive`. Its equivalence with `load_file` was shown on 3.2.0.
- Beancount 3.2.0 ships wheels only for CPython 3.9–3.13. Beancount 3.2.3 adds 3.14.
- The legacy oracle runs Beancount 3.2.0 on Python 3.13. Comparing old and new apps on different Beancount versions would add noise to every divergence.
- Python 3.15.0 was released on 2026-10-09, too new for the 14-day rule (ADR-0050).

## Decision

- **Python 3.13.16**, managed by uv 0.13.0 with `exclude-newer = "14 days"`.
- **`beancount==3.2.0` exactly**, and `beanquery` 0.2.0. Only `ledger.beancount_port` imports Beancount.
- **After v0.4.0**, the first slice moves to **Python 3.14 + Beancount 3.2.3**. It re-mirrors the overlay replica if the loader changed, and it lands only when the D2 equivalence suite is green (ADR-0010, guardrail 2).
- While the oracle comparison runs, the new and the legacy app stay on the same Beancount version.

## Alternatives considered and rejected

- **Python 3.14 + Beancount 3.2.3 from the start.** Rejected: version noise in every oracle comparison, and a re-mirror of the overlay replica in the middle of the riskiest milestone.
- **Python 3.15.** Rejected: released less than 14 days before the pins were set.

## Consequences

- Any Beancount upgrade, including security fixes, is its own slice with the full T1 suite plus the D2 equivalence suite.
- The overlay module carries a header naming Beancount 3.2.0 and the function it mirrors.

## References

review:25 §10 and §14 item 2; review:F §3.10; ADR-0010, ADR-0050; INV-02.
