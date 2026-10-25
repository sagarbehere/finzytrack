# S0015: Cut and deferred capability rows cite their ADR (BL-0039)

## Brief
- Goal: every `cut` and `deferred` row of `design/capabilities.toml` names the ADR that decided it, and `scripts/check_capabilities.py` fails on a row that does not (or cites one that does not exist).
- Implements: BL-0039 (review:G F-11); PROCESS.md §7 ("cut and deferred rows stay, with their ADR").
- Capabilities: the 27 cut and deferred rows (data only; no status changes). Invariants touched: none.
- Risk class / lane: Low / light (a map field and a check; no code path of the product).
- Acceptance tests: `scripts/tests/test_check_capabilities.py` (passing rows beside rows without an ADR, without the field, citing a missing ADR; the committed map passes).
- Expected doc impact: the 27 rows; backlog BL-0039 done; roadmap §1. `capabilities.md` does not show the field (unchanged).
- Open questions: none.
- Base SHA: cbd5389.

## Decisions
- Each citation was checked against the ADR's text, not only a keyword hit: ADR-0019 ("Never an auto-created ledger"), ADR-0036 (separate phone card views rejected; phone principle), ADR-0035 (auto-created accounts rejected, INV-15), ADR-0027 (`ofx_mappings.yaml` retired), ADR-0030 (XIRR, TWR, windowed returns, tax modelling, CAS replace and undo cut), ADR-0048 (v0.3-only functions need no compatibility), ADR-0029 (the `ledger` step's value and cost measures; goals and GUI builder), ADR-0032 (one-upload sync replaces CAS import), ADR-0015 (manual mirror export endpoints not rebuilt), ADR-0010 (writes to encrypted files refused), ADR-0020, ADR-0018, ADR-0021, ADR-0041, ADR-0025, ADR-0011 for the deferred rows (agent).
- Observation, not changed here: CAP-DATA-ENCRYPTED's note says the overlay loader refuses encrypted files, while ADR-0010 only refuses *writes* to them, and INV-03 says a ledger Beancount loads must be handled. Reading `.gpg` ledgers is a question for the M1a overlay-load slice; noted on that slice's path (BL-0003, the parity corpus) rather than decided here (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: a cut or deferred row without an ADR, without the field, or citing a missing ADR.
- Self-mutation: 2 tried, 2 killed.
- Population: every row with status `cut` (15) or `deferred` (12) in the map, 27 of 340, found by status.
