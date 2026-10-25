# Mirror: the INV-21 goldens

## Orientation
- **What exists:** the expected values the M1a code must reproduce for ledgers released versions (v0.2.0 to v0.2.2) wrote (S0022): each transaction's TransactionId, content-hash input and hash, and metadata JSON, on 11 synthetic ledgers; and the inventory of every metadata key a released version wrote, classified per INV-21. No product code yet: the M1a mirror build (BL-0021) must pass them.
- **Entry points:** `corpora/ledgers/inv21/` (one folder per case, rooted at `main.beancount`); `cases.toml` (features, variants, v0.4 overrides); `goldens.json` (the values; written by `scripts/oracle/gen_inv21_goldens.py`, never by hand); `keys.toml` (the key inventory); `scripts/oracle/inv21.py` (the second derivation and the checks); `scripts/oracle/tests/test_inv21_goldens.py`.
- **Decisions it applies:** ADR-0026 (TransactionId; read-forever keys; cleanup only with consent), ADR-0048 (v0.2.2 ids unchanged through 0.4.x), ADR-0055 (`transaction_content_hash` computed from the entry), ADR-0024 (Beancount is the oracle: entry order, interpolated amounts). The formulas are in [`README.md`](README.md) "Ids" and "Metadata JSON"; this file does not restate them.
- **Invariants:** INV-21 (these are its goldens; it stays `pending` until the M1a code passes them), INV-23 (synthetic data only).
- **Traps:** a TransactionId is not unique: a copied entry keeps its legacy `id` (BL-0059). Ordinals follow position, not content: reordering twins or moving one to another file changes which twin gets `-1`. The hash sees one leg, so twins can differ elsewhere (another expense account). A legacy `id: 42` is the text `42`; `id:` with no value is the text `None`; `id: ""` is the empty text. The hash's amount is `str(Decimal)` (`1E-8`), never the mirror's plain notation: do not reuse the mirror's number formatter for it. `source_account: ""` skips the Assets search and uses the first posting.
- **Main tests:** `scripts/oracle/tests/test_inv21_goldens.py` (runs in the T0 pre-push hook and T1's `oracle-unit`).

## Where the values come from

Two derivations, which must agree (EP-29):
1. **The released code.** `goldens.json` was produced by running v0.2.2's own functions on the case ledgers in Beancount 3.2.0 (the oracle environment): `SQLiteExporter._compute_fallback_id_map`, `_get_transaction_id` and `_metadata_to_json` with its helpers; `BeancountEngine.compute_hash_from_transaction`; `libs/content_hash.compute_content_hash` (whose input text is captured as it is hashed). Its header names the files and line ranges. These functions are the same at v0.2.0, v0.2.1 and v0.2.2 (compared by syntax tree), except the metadata-JSON helpers, which v0.2.2 made more tolerant. The generator, `scripts/oracle/gen_inv21_goldens.py`, reads those functions from the `v0.2.2` tag of this repository (`git show`); nothing in CI needs it, and the test reruns it with the committed goldens as the expected output only when the tag is in the clone.
2. **The spec.** `scripts/oracle/inv21.py` derives every value again from the README's text alone. When the two disagree, the spec or the golden is wrong, and the slice that finds it says which and fixes it.

`transaction_id` is v0.4's expected TransactionId. It equals v0.2.2's (`v022_transaction_id`) for every entry except those listed in a case's `v04_overrides`: entries with a posting `import-key` (v0.4's `k:` rule; no released version wrote `import-key`) and the ordinals they free (an entry with an import key no longer counts as a twin). The overrides were derived by hand from the README and are checked against the second derivation.

One case, `source-account-amount`, is a ledger v0.2.2 could not read: an Amount as the `source_account` value made v0.2.2 raise an error and fail the whole mirror export. There is no released value to keep, so v0.4's (README "Ids": text against text) is written by hand in `cases.toml` (`v022_fails` with the reason, `v04_expected` with the hash input); the generator confirms that v0.2.2 still fails on it, and its `v022_transaction_id` is null.

Each transaction's row holds `at` (file and line), `flag`, `transaction_id`, `v022_transaction_id`, `sha256` (the content hash; named so because the privacy scan accepts a 64-hex value only as a `sha256` pin), `hash_input`, `metadata_json` and `posting_metadata_json` (one per posting). The `metadata-keys` case also pins `directives` (every other directive's metadata JSON). The only normalization: `filename` inside the metadata JSON is the path relative to the case folder, with `/` (the mirror stores the absolute path).

## What the test checks

- Beancount loads every case without errors; the goldens list exactly Beancount's transactions, in Beancount's order.
- Every pinned value equals the spec derivation; each `sha256` (the content hash, `transaction_content_hash`) is the SHA-256 of its `hash_input`.
- A ledger v0.2.2 could not read claims no v0.2.2 id, and its hash inputs equal the hand-written `v04_expected`.
- A v0.4 id differs from v0.2.2's only through a listed override, only in a case with a posting `import-key`, and each override is a `k:` id or a content hash of that case with an ordinal.
- Each case's declared features are found by their detectors (from Beancount's output, never from the labels), and together the cases declare every feature in `inv21.FEATURES`: legacy ids (both keys, non-text, empty text, shared, before an import key, hash-shaped and equal to another entry's fallback id), twins (three of a kind, differing outside the hash, made by `|`, with an identified entry between them, after a reorder, after a move to another file, on equal line numbers in two files), padding transactions, no payee, non-ASCII text, every `source_account` case (wins, empty, padded, unmatched, false-like, true-like non-text values, an Amount, absent with `Liabilities:` or with no balance-sheet posting, repeated, interpolated amount, thousands separator), amounts written with an exponent by `str(Decimal)`, custom root names, and every `import-key` case (on the first or a later posting, on several postings, several elements, on the transaction, empty).
- The key inventory (below).

## The key inventory

`keys.toml` lists every metadata key a released version wrote, with where, and the keys INV-21 names that no released version wrote (`transaction_id`, `ofx_id`, transaction-level `ofx_memo`, written before v0.1; `import-key`, v0.4's). Each is `read` (INV-21 (a): a v0.4 reader needs it; the reader is named with its ID) or `pass` (INV-21 (b): kept byte for byte, read by nothing). The test checks that every key INV-21 names is `read`, that INV-21's rule names no key the check does not know, that the `metadata-keys` ledger carries every inventory key on its directive and no other key except its declared user keys, and that its metadata JSON for every directive equals the spec derivation.

The inventory was built from what the v0.2.x code does, not from a list of names: every place that puts a key into an entry's metadata (the engine's create functions, the import commit, the transaction edit, the account form, the documents scheme, the frontend's import converters, whose metadata the commit writes as is) and the keys in the released seed ledgers, at each of the three tags. It found one key review:28 §2 did not list: v0.2.0's commodity `type`.

The released snake_case keys that stay (ADR-0026): `account_number`, `ifsc_code`, `swift_bic`, and `source_account`, `external_id`, `external_id_type`, `content_hash` and `source_rule`, which v0.4 reads or passes through but never writes.

## Adding a case

Write the ledger (synthetic data, rooted at `main.beancount`), add its table to `cases.toml` with the features it shows, run `uv run --project scripts/oracle --locked python scripts/oracle/gen_inv21_goldens.py`, review the new values, and run the test. A new feature needs an entry in `inv21.FEATURES` with a detector and a near-miss ledger in the test. A change to an existing golden needs a journal line saying why the old value was wrong (PROCESS.md §8).
