# When Copy brings in a v0.2.2 ledger, should it also rewrite the `document` links on transactions so they point at the copied files?
- Asked: 2026-11-10 by the implementer of S0095-inv21-through-the-build
- blocks: nothing now (Copy's screens and adoption arrive in M3, BL-0026; the transaction documents reader in M5, CAP-DOC-TXN)
- Rubric item: 1 (what is written to the ledger: a copy would change the text of a metadata value)
- ADRs checked: ADR-0026 and INV-21 (keys a released version wrote are read forever and pass through untouched; only the consented cleanup removes or renames a key: none says whether a copy may change a key's value); ADR-0017 (a copied workspace writes `option "documents" "documents"` and is self-contained); ADR-0062 (Copy is the only way a legacy ledger comes in); your decision 3 of 2026-11-07 in S0081 ("Rewrite only the include lines and the documents option that point outside"; S0081 round 1 added `document` directives, so the copy is self-contained). None covers a `document` value written as metadata on a transaction.

## Context (plain language, a concrete example first)
Example: in v0.2.2 you attached a receipt to a lunch. v0.2.2 stored the file at `data/documents/one-0a1b2c3d/2026/receipt.pdf` and wrote two things into the ledger `data/ledgers/one.beancount`:

```
option "documents" "../documents/one-0a1b2c3d"
2026-01-05 * "Lunch"
  document: "../documents/one-0a1b2c3d/2026/receipt.pdf"
  ...
```

The `document:` value is a path relative to the ledger file's folder (v0.2.2's `document_store.py`; Fava reads it the same way).

Today's Copy (S0081) copies the receipt into the new workspace as `documents/2026/receipt.pdf` and rewrites the option to `option "documents" "documents"`. It leaves the `document:` line as it is. In the copy, `../documents/one-0a1b2c3d/2026/receipt.pdf` now points outside the workspace, at a file that does not exist there (or, if the copy is placed next to the old `data/` folder, at the old install's file). I checked this with a test on disk: the copy succeeds, "loads the same" passes (the text is the same on both sides), and the link leads nowhere. No byte is lost: the receipt is copied, the line is kept.

## Options
1. **Copy rewrites the `document*` values that point at a moved file**, as it already does for `document` directives: the line becomes `document: "documents/2026/receipt.pdf"`, nothing else changes, and "loads the same" maps the new value back to the old file. The copy is self-contained, as ADR-0017 wants; Fava and v0.4 open the receipt. The cost: the copy's text differs from the source on those lines (the source is never touched).
2. **Keep the value; v0.4's documents reader maps old roots** when it reads a `document` value (it would remember that `../documents/one-0a1b2c3d` became `documents`). The ledger text stays identical. The cost: Fava and other tools see a broken link in the copy, and v0.4 keeps a mapping outside the ledger, which ADR-0017's state placement discourages.
3. **Refuse such a copy** until the user moves the files. Simple, but it blocks the main adoption path for anyone who used attachments.

## Recommendation
Option 1. It treats a `document` value like the `document` directive Copy already rewrites, keeps the copy self-contained, and changes only the path text of a key INV-21 reads (the key itself is kept, so INV-21's "remove or rename" does not apply). It would be built with Copy's adoption screens (BL-0026, M3), with a must-not-pass test on the v0.2.2 install fixture.

## Answer (owner, verbatim, with date)
