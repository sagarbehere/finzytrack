# S0026: The fixture gate's gaps (BL-0023)

## Brief
- Goal: close BL-0023's open parts found by the S0019 verifier: F1, the staged-file guard skipped its "looks like a bank statement" sniff for data-like files inside `corpora/` and `fixtures/`, so a renamed real export there passed; F2, a MANIFEST row's owner review date was only length-checked, a malformed row was silently ignored, and nothing stopped an agent writing a row. F4 (the anonymization script) has no consumer until the first real export arrives (M6, import); it moves to its own item, BL-0060.
- Implements: INV-23 (unchanged text; it is already enforced), PROCESS.md §8 (privacy gate), ADR-0058 (data guards stay), EP-30 (refuse the shape: name the synthetic assets instead of modelling what a statement is).
- Capabilities: none. Invariants touched: INV-23 (its checks tightened).
- Risk class / lane: Medium (privacy guard; no ledger data): one verifier round, a fresh one only after a High.
- Rules: (1) the statement sniff runs everywhere; only the paths in `check_staged.SYNTHETIC_ASSETS` (two synthetic JSON assets our own tools write, the only tracked files that trip it) are exempt by exact path; (2) when MANIFEST.tsv is staged, every row needs a 64-hex sha256, a non-empty path and a real calendar date not in the future, else refused; a bad row never vouches for a file; (3) the guard hooks refuse agent writes to `corpora/MANIFEST.tsv` (file tools, and shell writes the Bash guard parses: redirects, cp, tee, sed -i). Stated limit: an interpreter writing the file (`python3 -c ...`) is not parsed (PROCESS.md §11).
- Acceptance tests: `scripts/tests/test_check_staged.py` class `Bl0023`; `scripts/tests/test_guards.py` `test_only_the_owner_writes_the_export_manifest`.
- Expected doc impact: `check_staged.py` docstring; backlog BL-0023 done, BL-0060 new, totals; roadmap §1; this journal.
- Open questions: none (moving F4 to M6 is backlog planning; reported to the owner in the morning summary). Base SHA: e8982a69.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Ways around it, each tested: keep the old corpora/fixtures exemption; exempt by prefix instead of exact path (`goldens.json.txt`); accept a future date, an impossible date (2026-02-30), a reordered date, a non-hex hash, an empty path, a short row; drop the manifest problems; let agents write the manifest; block every MANIFEST.tsv anywhere.
- Self-mutation: 8 tried, 7 killed; the survivor (prefix exemption) got the `goldens.json.txt` case, then killed.
- Population: every tracked file in an allowed place with a sniffed extension was run through the new sniff; two trip it (both synthetic, both named).
