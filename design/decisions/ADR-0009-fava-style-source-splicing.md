# ADR-0009: Fava-style source splicing; mutators are edit producers

This record decides how new ledger text is produced: by editing only the target spans of the source files.

- **Status:** Accepted
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
