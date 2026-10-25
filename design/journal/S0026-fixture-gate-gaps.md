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

## Verifier
- Round 1, head 7188a88: PASS WITH FINDINGS. Attacks: 14 (every tracked file through the guard; corpora/fixtures/extensionless statements; UTF-16; renamed extensions and binaries; split, padded and reformatted statements; an overwritten synthetic asset; 18 MANIFEST row shapes; manifest spellings, `..` and symlinks; unparsed shell writers; regressions; base-commit failure; mutants).
- F1 Medium: a UTF-16 (BOM) statement or OFX file passed anywhere. Fixed: UTF-16/32 text with a BOM is re-encoded before sniffing.
- F2 Medium: `corpora/manifest.tsv`, `Corpora/...`, `..` and symlinked spellings wrote the manifest. Fixed: the guard compares the resolved, lower-cased path (as its other rules do), for `.git` too.
- F3 Low: older heuristic gaps of the sniff (extensions, magic bytes, split files) → BL-0061 (M6, before the first real export).
- F4 Low: the review-day boundary and "a bad committed row blocks every commit" were untested. Tests added (`today=` injected).
- F5 Low: a synthetic-asset path vouched for any content. Fixed: it must parse as JSON.
- F6 Low: the stated limit named only interpreters. Fixed wording (git checkout/restore/apply, patch, curl -o, awk -i, ed too).
- Docs: PROCESS.md §8 now describes the sniff, the row validation and the manifest guard. INV-23's "Enforced by" does not list the guard files: owner text, reported in the morning summary.
- Fix-round mutants: 6 tried, 6 killed. Two Medium fixes, no High: no further round (ADR-0058).

## Retro
- Both Medium findings were "the same thing spelled differently" (an encoding, a path spelling); the guard already had `_norm` for paths and I did not reuse it. Rule: a new path rule goes through the module's normalizer.
- Process cost: about 45 min building; verifier 4 min; fixes about 25 min.
