# S0020: The D1 write corpus (hand-written nasty cases)

## Brief
- Goal: commit the hand-written part of the D1 write corpus as exact bytes in `corpora/ledgers/write/`, one folder per case, described by `cases.toml` (features, expected outcome, expected Beancount errors); a stdlib check that every case shows the features it declares and nothing else it could be mistaken for, and that the corpus covers the fixed feature list; a load test in the oracle environment that runs Beancount 3.2.0 on every case and compares its errors with `cases.toml`; an encrypted-file case that must be refused, never loaded (ADR-0059).
- Implements: review:D1 §6 (the nasty-case list), §9 guardrail 1 (pythonpath plugin, duplicate include, glob and nested includes, `implicit_prices`, documents folders), §8 (a plugin that changes user entries; `@@` total prices); ADR-0059 and BL-0003's refusal ("never partly loaded"); the corpus part of BL-0001. The generated 5k/54k ledgers and the Beancount/Fava example ledgers are not in this slice (the demo generator and BL-0003's parity corpus bring them).
- Capabilities: none (test asset). Invariants touched: INV-01 and INV-03 (still pending: the corpus is their input, not their check), INV-23 (synthetic data only).
- Risk class / lane: Medium / full lane asset; tooling that touches no ledger or user data, so one verifier round and a fresh one only after a High fix (ADR-0058).
- Rules the checks enforce:
  1. Every case folder has a `cases.toml` entry and the reverse; every declared feature is in the fixed list.
  2. Per case, the declared features equal the features the stdlib detectors find (a case cannot claim a feature it lacks, nor carry an undeclared one that would blur what it tests).
  3. The union of declared features covers the fixed list (D1 §6, guardrail 1, §8, ADR-0059).
  4. `expect = "refuse"` exactly when a case holds an encrypted file; a `load` case holds no file Beancount might decrypt (conservatively: any `.gpg` name in any letter case, or a PGP message header anywhere in an `.asc` file), so the load test never starts gpg.
  5. Every file in the corpus has git's `text` attribute unset, so checkouts keep the exact bytes (CRLF, missing final newline).
  6. Load test (oracle env): each `load` case loads in Beancount 3.2.0 with exactly the declared errors (type and message fragment, as a multiset); each `refuse` case's encrypted file is one Beancount's own `is_encrypted_file` accepts, and no file of a `load` case is; features with a load-level effect (implicit prices, documents found by the folder scan, a plugin changing a user entry, elided amounts and `{}` reductions filled in by booking, glob and nested includes) are confirmed on Beancount's output.
- Acceptance tests: `scripts/tests/test_check_write_corpus.py` (detectors with a must-not-pass case each, the rules above, the fixed list pinned to the spec sources); `scripts/oracle/tests/test_write_corpus.py` (the load test and the probes, each probe with its must-not-pass).
- Expected doc impact: new `design/specs/write-path/write-corpus.md`; `design/specs/write-path/README.md` orientation; `corpora/README.md`; `.gitattributes`; the pre-push hook runs the check and the load test; roadmap §1; this journal.
- Open questions: none (accepted ADRs cover it).
- Base SHA: 6444596.

## Decisions
- One folder per case, exact bytes committed; the generator stays out of the repo (the bytes are the artifact, and the check pins their properties) (agent).
- Declared features must equal detected features, not merely be found: honest labels make each probe's must-not-pass ("every case that does not declare it") sound (agent).
- The encrypted cases hold placeholders, not OpenPGP data: Beancount decides by the name or the armour header, and a real ciphertext would add nothing but a binary blob (agent).
- `total-price` added beyond D1 §6's list, from D1 §8 (`@@` must survive a replace) (agent).
- The pre-push hook runs the check and the Beancount load (under a second); PROCESS.md §6's T0 row names it (agent; tightening).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Ways to loosen or bypass, each tested: a feature dropped from `FEATURES` (pinned to the spec lists); a lax detector (positive + must-not-pass per detector: comments, strings, header vs posting, unpaired push, unresolved or un-included files, folder-crossing globs, `{}` augmentation, `.GPG`); a false or missing label; orphan folder or entry; unknown feature; malformed entry (7 shapes); refuse without an encrypted file; errors on a refused case; a load case holding `.GPG` or a late PGP header; symlinks and stray files; `text` set, unspecified, `auto` or missing; a coverage gap; the error comparator (undeclared, missing, one error for two declarations, type mismatch); each load probe against every non-declaring case.
- Self-mutation: 25 tried, 22 killed; survivors `nested-unresolved` and `glob-crosses-folders` got tests (then killed); `probe-reduction-any-sign` is equivalent on this corpus (no case has a positive `{}` posting; the stdlib detector refuses one, mutant `empty-cost-any-sign` killed). The runner's copy is not a git repo, so the plan's command starts with `git init -q` (the check fails closed without git).
- Population: the fixed list is D1 §6 (18 features, comments split three ways), §9 guardrail 1 (+2), §8 (+2), ADR-0059 (+3) = 25 features over 19 cases (17 load, 2 refuse).

## Verifier
- Round 1, head 5325c60: PASS WITH FINDINGS. Attacks: 10 (raw-byte labels, finance meaning of each case, errors against Beancount, encrypted-file paths, Fava 1.30 insert-entry, include-tree divergence, detector mutants, the feature list against D1's text, pre-push and CI wiring, an encrypted root).
- F1 Medium: Fava inserts before the `insert-entry` line in the file that holds it, so the case never routed into an included file and its comment was wrong. Fixed: the line moved into `food.beancount` between two entries; comment corrected.
- F2 Low: the string guards in `total-price` and `implicit-prices` had no must-not-pass. Fixed: lot-label tests.
- F3 Low: a self-include or a cycle to the root went undetected; a glob reached dotfiles; `**` was modelled as one level. Fixed: the root counts as a target; wildcards skip dotfiles; a `**` include is refused (conservative rule, S0016 retro).
- F4 Low: an encrypted root file cannot be a case here (every case is rooted at `main.beancount`). Recorded in the spec and in BL-0003.
- F5 Low: PROCESS.md's T0 row implied the Stop hook runs the corpus load. Fixed: "pre-push only".
- Fix-round mutants: 5 tried, 5 killed. No High, so no further round (ADR-0058, tooling).

## Retro
- What the verifier found that I did not: I wrote the `insert-entry` comment from memory of Fava instead of running Fava; running the real tool once (as the verifier did) is the cheaper gate for any case whose meaning depends on another tool.
- Self-mutation before hand-off caught two test gaps; the verifier's own mutants caught two more (string guards). Mutating every guard clause, not one per check, would have found them.
- Process cost: about 1 h 15 min building (incl. orientation); verifier 5 min wall; fixes about 15 min.
