# ADR-0009: Fava-style source splicing; mutators are edit producers

This record decides how new ledger text is produced: by editing only the target spans of the source files.

- **Status:** Accepted. **Amended on 2026-11-28 (S0167):** the two metadata-line primitives are dropped; a metadata line added, removed or changed is a whole-entry replace spliced into the entry's own text, never re-printed (the amendment of that date).
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** write path

## Context

The loader returns processed entries, not source: plugins have run, booking has filled lots and amounts, `pushtag` has been expanded, and comments are gone. Re-printing that on every write damaged ledgers (glob-included entries copied into the root, plugin entries persisted, `{}` reductions pinned, comments lost; review:10 I2, I3, I10). Fava edits the source slice of one entry and has years of real use.

## Decision

(owner, 2026-10-24)
- **Port Fava's slicing algorithms** (`fava/core/file.py`, MIT) as pure functions over text, with an MIT attribution header: `locate`, `apply_edits`, `route_insert`, plus our `replace_token`. Do not depend on the `fava` package.
- **Locate from the parser's own positions** of the *current* file text (never loader output), so plugin-generated entries can never be targeted. Confirm that the span's first line has the entry's date and keyword.
- **Many edits per file** are planned from one snapshot and applied bottom-up; overlapping spans abort.
- **`replace_token`** for move-postings and rename: replace exactly the account token on the posting's own line; never a regex over the file.
- **Whole-entry replace** prints the modified entry and replaces its span. Inserts follow Fava's `insert-entry` options, else append to the default file; never inside a `pushtag`/`pushmeta` region.
- **Text hygiene:** CRLF/LF per file, missing final newline, tabs allowed, UTF-8 or refuse. Include, option and plugin lines are never re-emitted. The editable set is the glob-expanded include list. **Encrypted (`.gpg`) files are not editable.**
- **`@@` is preserved as written** (an own posting-line renderer or a `replace_token` on the price span); step 2 treats `@@ T` and `@ T/u` as the same intent. Resolved before replace (stage 4).
- **Typed metadata is merged** against the parsed source entry. The transaction-edit use case never takes a router-built `Transaction`; it merges the request against the source entry, and that merged entry is the intent the contract checks.
- Two more primitives for metadata lines: insert a posting meta line, and delete a meta line (verified by the semantic diff).
- **Every mutator is an edit producer:** `(snapshot, request) → Edit[] + IntendedChange`. Stages: 1 contract + overlay load; 2 engine port; 3 inserts; 4 single-entry replace/delete; 5 multi-entry operations; 6 every mutator promoted (ADR-0014). There is no full-rewrite path.

## Alternatives considered and rejected

- **Full re-render of loader output** (legacy). Rejected: lossy on every byte of every write.
- **Full rewrite from unbooked parsed source.** Rejected: it still drops comments and blank lines, flattens `pushtag` blocks and rewrites every touched file. Comments are user data.
- **Depending on the `fava` package.** Rejected: it pulls in Flask, Jinja, Babel and more; these functions are not a public API; its I/O is non-atomic.

## Consequences

- Bytes outside the edited spans are never rewritten, so glob, plugin and `{}` damage cannot arise.
- Comments *inside* one whole-entry-replaced entry are lost; posting-level token edits can refine this later.
- Statement import and its undo are not built (ADR-0030).

## References

review:D1 §0–§8, §12.7, §12.8; review:11 L3-02, L3-03, L3-15; ADR-0008, ADR-0014; INV-01, INV-03; CAP-DATA-WRITE.


## Amendment (2026-11-28): a metadata line is a spliced whole-entry replace (S0167)

Owner, 2026-11-28, in session, answering proposal 5 of `design/inbox/answered/20261128-m1b-simplifications.md` (from the M1b area review, F1), choosing "Accept, splice only (Recommended)". The option as put: accept, with the rule that the producer splices the line into the entry's own text, so comments and spacing are kept (I10), never re-printed; intent-table rows and ADR-0009 amended; BL-0313 closes without code.
- **Superseded:** "Two more primitives for metadata lines: insert a posting meta line, and delete a meta line" (the intent table's `insert_meta` and `delete_meta`, never built).
- **Instead:** adding, removing or changing one metadata line (of an entry or of a posting) is a `replace` of that entry by the same entry with that line added, removed or changed, **produced by splicing the line into the entry's own bytes, never by re-printing the entry from its parse**. Every other byte of the span stays as written: comments, spacing, elided amounts, the order of keys (I10). The decision's "whole-entry replace prints the modified entry" does not apply to such a replace, and the consequence "comments inside one whole-entry-replaced entry are lost" does not hold for it. Example: setting `bank-id: "1234"` on an account's `open` replaces that `open` and its metadata block with the same text plus one line `  bank-id: "1234"`; a comment on the line after it is kept. Every line changed on one entry, and a `replace_token` on that same entry (a rename whose `open` also carries a metadata value naming the account), go into one `replace`: two edits on one entry overlap and the plan is refused (`overlapping_edits`).
- **What the contract checks (ADR-0008):** step 2 checks the replaced entry is exactly the intended one (the parsed entry with that line added, removed or changed; every other key keeps its value and type), and step 2b checks every byte outside the span. **Limit:** neither sees a comment or spacing inside the span, how a metadata value is quoted (`k: Assets:Old` against `k: "Assets:Old"`), or the order of tags and links (the entry key sorts them), so the splice is the producer's rule, pinned by each producer's tests (a comment, odd spacing, a value's quoting and the tags' order inside the entry survive). Whether step 2b also checks such a span (only whole metadata lines differ from the old span) is decided in the brief of the first producer, `account.edit` (M4).
- **Rows changed** (`design/specs/write-path/intent-table.toml`): `account.edit`, `account.rename`, `doc.txn-attach`, `import.link-element`, `recon.proceed`, `import.bank-id-migration`, `inv.commodity-metadata`, `meta.cleanup` now list `replace` where they listed the two primitives. BL-0313 is closed without code.
- **Unchanged:** the built primitives (`Insert`, `Replace`, `Delete`, `ReplaceToken`; a spliced replace is a `Replace` whose text the producer built), locating from the parser's positions, typed metadata merged against the parsed source entry, every step of the contract.
