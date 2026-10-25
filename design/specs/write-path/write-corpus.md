# Write path: the D1 write corpus

## Orientation
- **What exists:** the hand-written nasty cases of review:D1 §6, as exact bytes in `corpora/ledgers/write/<case>/` (each rooted at `main.beancount`), described by `corpora/ledgers/write/cases.toml` (S0020). Not here yet: the generated 5k and 54k ledgers (the demo generator) and Beancount's and Fava's example ledgers (BL-0003's parity corpus); copies of the owner's real ledgers never come here (T-local only, PROCESS.md §6).
- **Entry points:** `cases.toml` (edit this when adding a case); `scripts/check_write_corpus.py` (stdlib; the fixed list is `check_write_corpus.FEATURES`); `scripts/oracle/tests/test_write_corpus.py` (Beancount 3.2.0). Both run in the T0 pre-push hook.
- **Decisions it applies:** review:D1 §6 (the case list), §8 (a plugin that changes user entries; `@@`), §9 guardrail 1 (the cases the overlay-equivalence suite needs); ADR-0059 (a ledger set with an encrypted file is refused, never loaded); ADR-0010 (the overlay load these cases will exercise).
- **Invariants:** INV-01 and INV-03 (the corpus is the input to their checks, BL-0001 and BL-0003), INV-23 (synthetic data only).
- **Traps:** an editor or git's line-ending conversion silently "fixes" a case (CRLF, a missing final newline): `.gitattributes` sets `-text` and the check refuses any file without it. Loading a case in place writes Python bytecode and may write Beancount's pickle cache: the load test copies each case to a temp folder. Never load a `refuse` case: Beancount would start gpg.

## What the corpus promises

1. **Coverage:** together the cases cover every feature in `FEATURES`, each tied to its spec line.
2. **Honest labels:** each case declares exactly the features the stdlib detectors find in its include tree, no fewer and no more. A detector reads only files Beancount would read from `main.beancount`, and never inside comments or strings where that matters.
3. **Beancount agrees:** each `load` case loads in Beancount 3.2.0 with exactly the declared errors (type plus a message fragment, matched one to one); the include tree the stdlib check computes equals the files Beancount read; load-level features (prices added by `implicit_prices`, documents found by the folder scan, a plugin changing a user entry, elided amounts and `{}` reductions filled in by booking) show in Beancount's output, and in no case that does not declare them.
4. **Encrypted files are refused** (ADR-0059): `expect = "refuse"` exactly when an included file is one Beancount's `is_encrypted_file` accepts (`.gpg`, or `.asc` with a PGP message header). A `load` case holds no file Beancount might decrypt, judged conservatively (any `.gpg` name in any letter case; a PGP header anywhere in an `.asc` file). The `asc-plain` case is the must-not-pass: an `.asc` file without the header is plain text and loads. The M1a overlay loader (BL-0003) must give the refusal on both refused cases and load `asc-plain`.

## Adding a case

Write the files with exact bytes (a script, not an editor, when line ends matter), synthetic data only. Add its table to `cases.toml`: the features it shows, `expect`, and the errors Beancount 3.2.0 reports, taken from Beancount itself (the oracle, EP-29), never from Finzytrack code. A new feature needs a spec line, an entry in `FEATURES`, a detector with a must-not-pass test in `scripts/tests/test_check_write_corpus.py`, and, if it has a load-level effect, a probe in the load test.
