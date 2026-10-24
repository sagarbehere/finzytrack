# ADR-0022: The demo is a generated, disposable workspace, replaced on release

This record decides how the demo ledger is produced, where it lives, and how it is refreshed.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** content, lifecycle

## Context

Every release should ship a demo that is recent and shows today's features. The v0.3 demo is one hand-written script on a fixed calendar; it drifts from features, two builds of one tag differ, and the demo lives inside the user's own workspace.

## Decision

(owner, 2026-10-24)
- **One deterministic generator composed of scenario fragments:** a background household layer plus one fragment per capability that has something to show (interest attribution, CDs, DRIPs and splits, every booking method, budgets including income budgets, reconciliation assertions, multi-currency, documents, stale prices, and so on). Fragments are anchored at a date and seeded by name.
- **Tied to the capability map:** each capability row has a `demo:` field; a user-visible slice is done only with its fragment; CI checks that each demo-relevant capability has a fragment and that an assertion query per fragment passes.
- **Validity gates:** 0 Beancount errors; every seed dashboard returns non-empty results; the finance red team's oracle checks hold.
- **Reproducible:** the anchor is the release tag's date (`SOURCE_DATE_EPOCH`), so rebuilding a tag gives identical bytes.
- **One generator, many uses:** scaled by a size parameter, it also produces the 5k and 54k test and performance corpora and the acceptance fixtures.
- **A separate demo workspace** at `<app_local_data_dir>/demo/`, manifest `kind: demo`, never inside a user's workspace, with a "Demo" badge while active.
- **Replaced on each release without asking**, with a one-line notice. Replacement is allowed only when the manifest says `kind: demo` **and** the folder's `realpath` is exactly the app's demo path with no symlink (INV-25). If the demo was edited, **one previous copy is kept** in a hidden folder inside the app data dir, and the notice says where.
- The v0.4 demo writes only the v0.4 metadata contract (ADR-0026).

## Alternatives considered and rejected

- **Hand-maintained demo script.** Rejected: drifts from features.
- **Consent and backup before refreshing the demo.** Rejected by the owner: demo data is disposable. Keeping one edited copy costs one rename and avoids silent loss.
- **Identifying the demo by file name.** Rejected: a real workspace could be "refreshed".

## Consequences

- A stale browser tab on the old demo key is redirected to the new demo.
- Each milestone adds the fragments for its capabilities.

## References

review:22 §1; review:23 §4.4; review:F §2 item 11; ADR-0019, ADR-0026; INV-25; CAP-DATA-DEMO-GEN, CAP-WS-DEMO.
