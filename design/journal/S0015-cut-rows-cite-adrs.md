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
- CAP-DATA-ENCRYPTED: no ADR cuts *reading* encrypted ledgers (ADR-0010 refuses only writes) and INV-03 asks for parity, so the cut is the owner's question: `design/inbox/20261025-encrypted-ledgers.md` (recommendation: read yes, write no). Until answered the row cites ADR-0010 with a PROVISIONAL note naming the inbox file (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: a cut or deferred row without an ADR, without the field, or citing a missing ADR.
- Self-mutation: 2 tried, 2 killed.
- Population: every row with status `cut` (15) or `deferred` (12) in the map, 27 of 340, found by status.

## Verifier
- Round 1, head d9f3303: PASS WITH FINDINGS. Attacks: 6 (every citation read against its ADR; other ADRs searched for the same cuts; tests on the base; odd `adrs` values; the tagged-test lookup; the full check).
- F1 Medium: CAP-DATA-ENCRYPTED's citation does not decide the cut (see Decisions); escalated to the owner instead of being parked in BL-0003's text.
- F2 Low: CAP-INV-CAS-IMPORT now also cites ADR-0030, the deciding record.
- F3 Low: CAP-IMP-OFX-CREATE-ACCT now also cites ADR-0027 ("OFX account auto-create is removed").
- F4 Low: the test used a real capability ID, which counted as that row's tagged test; now a fictional `CAP-X-Y`.
- One Medium, three Low on a docs/tooling slice: no further round (ADR-0058).

## Retro
- A keyword hit is not a decision: two of three wrong citations were ADRs that mention the topic while another ADR decides it. Reading each candidate ADR's decision section, not grep context, is the cheaper check.
- Process cost: about 25 min building; verifier 2 min; fixes and inbox 10 min.
