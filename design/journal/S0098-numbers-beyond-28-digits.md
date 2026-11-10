# S0098: numbers beyond 28 digits, and a load that never raises (BL-0123)

## Brief
- Goal: apply the owner's answer of 2026-10-29 to BL-0123 (`design/inbox/answered/20261028-numbers-beyond-28-digits.md`, option 1: "Yes, keep numbers beyond 28 digits as exact, with a warning and amend adr-0015 to content hashes"). Every stage that computes with a ledger's numbers runs at 64 digits, the load warns on each line where Finzytrack differs from bean-check and Fava, and the load returns and never raises: neither for such numbers nor for a plugin that adds a `Document` whose file name is not a path (S0049's contract). The strict xfail `test_a_written_negative_number_keeps_its_digits` becomes a passing test.
- Implements: the owner's answer above; ADR-0010 (the overlay load is Beancount's own); mirror README "Exact numbers"; ADR-0015's amendment of 2026-10-29 (the fingerprint is a content hash): **already applied** (ADR-0015 lines 46-48 quote the owner), so this slice adds no amendment.
- Capabilities: CAP-DATA-MIRROR (partial, M2).
- Invariants touched: INV-02 (Beancount's own booking, only the precision changes), INV-21 (content fallback ids), INV-22 (fail closed), INV-06 (exact money).
- Risk class / lane: High / specialist (finance semantics: how existing ledgers are interpreted).
- Acceptance tests: none written; the slice is parked before coding (below).
- Expected doc impact: this journal, the inbox question, the BL-0123 row's note.
- Open questions: `design/inbox/20261110-numbers-beyond-28-digits-divisions.md` (rubric 1, 2, 4). **S0098 is parked until the answer.**

### Where the load runs (inventory by behaviour, 8a7dbe11)
Every call into Beancount's load is in `ledger_engine.ledger.beancount_port` (INV-02; `tests/architecture/test_load_rule.py`). The callers are: `overlay.load` (the open `ledger/opening.py`, the copy `ledger/copy.py`, the copy's fresh-process check `ledger/copy_check.py`), and `port.real_load` (the rebuild `ledger/rebuild.py`, the create's check `ledger/create.py`). The engine's own decimal context is set only in `mirror/core/rows.py` (`EXACT`: 64 digits, `Inexact` trapped). Every other stage runs in Python's default context, 28 digits. Beancount divides in `parser/grammar.py:896` (`@@`), `parser/booking.py:218` and `parser/booking_full.py:720, 750` (`{{ }}`), `booking_full.py:911, 917, 945, 984` (a filled-in quantity, cost or price), and `core/prices.py` (inverse rates). The sidecar does its own arithmetic in the default context too (not a load stage; noted for the owner's answer).

### Evidence (spikes, scratchpad only; synthetic ledgers)
- The corpus mirror checksums with the overlay load wrapped in `decimal.localcontext(prec=64)`: two tables change, `write/total-price` `postings` and `oracle/nasty` `postings`, `prices`. In every changed row only the per-unit price of a `@@` line changes, `1.428571428571428571428571429` → `1.428571428571428571428571428571428571428571428571428571428571429`. At 28 digits the snapshot is unchanged (control).
- `7 VTI {{100 USD}}` and `3 VTI {{100.00 USD}}` (no sale): at 28 digits `lots` builds (`book_value` `100.00000000000000000000000003`); at 64 digits the build is `BuildFailed("inexact", "account_balances.balance")`, so the workspace would not open.
- `Assets:Broker  VTI @ 3 USD` / `Assets:Bank  -10 USD`: the content hash and TransactionId change, `07eb28e8…` at 28 digits, `10178aba…` at 64 (units `3.333…` with 28 and with 64 digits).
- The crash in the row: `12345678901.123456789012345678 ETH` with the other leg left blank raises `decimal.InvalidOperation` in `interpolate.quantize_with_tolerance` from `overlay.load` and from `port.real_load`. `bean-check` on the same file crashes with the same exception.
- A plugin that appends `Document(..., filename=42)`: `overlay.load` and `port.real_load` both raise `TypeError` in `validation.validate_documents_paths` (`posixpath.isabs`). Today no engine process dies of it: the open answers `engine.internal` (`protocol/server.py`'s request boundary), the copy `Stop("source_failed")`, the copy check catches it; the rebuild answers `engine.internal`.

### Why this is escalated rather than built
The owner's answer assumed that 64 digits changes only numbers longer than 28 digits. The evidence shows it also changes every number Beancount divides, in ordinary ledgers. That has three effects: every `@@` per-unit price changes, a `{{total}}` ledger stops opening unless the mirror's exactness rule widens, and a rare released TransactionId changes (INV-21). The literal warning ("each line where Finzytrack differs") would then name all of those lines. Each of these is a rubric item beyond what was decided (1, 2, 4), and the brief says to escalate rather than guess. Nothing is built on the old premise.

## Decisions
- Park S0098 before coding; the question is in the inbox with options and a recommendation (agent, under `.claude/agents/implementer.md`: "If an open question hits the rubric, write the inbox file and park that slice before coding").
- The `Document`-name half is not an owner question. The decided design, to build with the answer: the overlay load returns `LoadRefused` with a new reason when one of Beancount's own load stages raises an `Exception` on the ledger (booking, a plugin's import, validation); the real load does the same; the open and the rebuild answer that refusal instead of `engine.internal`. It is built with the precision half, not alone, because both change the same port stages and the same refusal. Under the recommended option 3, the interpolation crash takes this refusal too (agent).
- ADR-0015's "content hashes" amendment: found applied (ADR-0015, "Amendment (2026-10-29): the fingerprint is a content hash"); nothing to do (agent).
