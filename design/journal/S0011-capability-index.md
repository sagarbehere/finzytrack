# S0011: Capability map as an index; workstreams retire into specs

## Brief
- Goal: answer the owner's question (2026-10-25) whether `capabilities.md` and `backlog.md` are overlapping and essential: `capabilities.md` duplicated the `.toml` in full; it becomes an index (ID, short label, v0.4 kind, status, milestone, release-blocking) and the detail stays in the source. The backlog's workstream sections retire one by one into their area specs.
- Implements: PROCESS.md §7 (generated docs), INV-19 (generated, never hand-edited).
- Capabilities: none. Invariants touched: INV-19 (the index stays generated and checked).
- Risk class / lane: Low / light; docs and a generator: one verifier round (ADR-0058).
- Acceptance tests: `scripts/tests/test_gen_capabilities.py` (labels cut at the first clause; long labels cut on a word with an ellipsis; a decimal point is not a sentence end; pipes cannot break the table; an index row has the index columns and not the detail; the header points to the `.toml`); `check_generated.py --all` and `check_capabilities.py` pass.
- Expected doc impact: `design/capabilities.md` regenerated (about 18,200 → 8,200 words); backlog §2 header (the retirement rule). No user docs.
- Open questions: none.
- Base SHA: ff81bd6.

## Verifier
- Round 1, head b73eb9a: PASS WITH FINDINGS. Attacks: 5 (gates; all 340 labels scanned; a mid-word-cut mutant; every consumer of the old columns; information loss).
- F1 Low: labels were cut at a `; ` inside parentheses (7 ended with an unclosed "("). Fixed: the cut happens only outside parentheses and backticks; test.
- F2 Low (EP-29): the word-cut test let a mid-word cut pass. Fixed: an input whose 100-character mark falls inside a word, and a check that no word is split.
- F3 Low: the glossary described the old columns. Fixed.
- F4 question: some labels drop a qualifier; accepted, the full text is one search away in the `.toml`.
- Docs-only slice: one round (ADR-0058).

## Retro
- The label rule was tested on hand-picked strings, not on the 340 real rows; a scan of the real data (as the verifier did) is the cheaper test for a text transform. Process cost: about 15 min building, 1.5 min verifier, 10 min fixes.
