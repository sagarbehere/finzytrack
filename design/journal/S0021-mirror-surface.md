# S0021: The mirror surface spec

## Brief
- Goal: write M1's mirror surface spec (review:27 §M0; review:backend-survival §7 row "Mirror writer/reader", form (c)): every table and column the v0.4 mirror has at M1a, with type, sign convention, as-of meaning, id provenance (fallback ids included), metadata-JSON key quoting, `is_currency`, pad pairing and which columns are documented for user SQL; the ADR-0048 compatibility list (every v0.2.2 table name keeps working through 0.4.x as a view over the `r_*` surface; `lots` from booked inventory; `budget_for_range` with a located deprecation message). The DDL snapshot and per-table checksums come from code with the M1a mirror build (recorded as a backlog row, not faked).
- Implements: ADR-0015, ADR-0016, ADR-0023, ADR-0024, ADR-0025, ADR-0026, ADR-0030, ADR-0031, ADR-0048, ADR-0055; INV-05 (the surface its view golden will check), INV-06, INV-21.
- Capabilities: CAP-QUERY-SURFACE, CAP-DATA-MIRROR, CAP-MIRROR-EXTERNAL, CAP-FN-BUDGET-FOR-RANGE (spec only; all stay `planned`).
- Invariants touched: none enforced yet (INV-05 and INV-21 stay pending: BL-0005, BL-0021).
- Risk class / lane: Medium (spec plus stdlib tooling; no ledger or user data). One verifier round; another only after a High fix (ADR-0058).
- Rules the check enforces (`scripts/check_mirror_surface.py`, run by `scripts/tests/test_mirror_surface.py`):
  1. Every table and column has the required fields, from closed vocabularies (type, format, as-of, origin, status); money is TEXT with a sign convention; names are unique.
  2. Every table and column the v0.2.2 exporter creates is in the spec under its v0.2.2 name, served by a compatibility view; none is dropped.
  3. Every v0.3.0 (`legacy-final`) table and column is kept or dropped with a citation; nothing claims an origin it does not have.
  4. A changed column or dropped item cites an existing ADR, capability row, backlog ID or open inbox file; `decided`/`draft` rows carry no open owner question.
  5. The pinned legacy inventories equal what the exporters' `CREATE TABLE` statements produce when the tags are present (derived by running the DDL in SQLite, not by reading names).
- Acceptance tests: `scripts/tests/test_mirror_surface.py` (the real spec passes; one must-not-pass per rule and per loosening listed in the Hand-off).
- Expected doc impact: new `design/specs/mirror/` (README, `surface.toml`, generated `surface.md`); `scripts/check_generated.py` registers the generator; `design/specs/README.md`; roadmap §1; backlog (WS-MIRROR stale facts corrected, new rows for the DDL snapshot and checksums and for the user-doc corrections); user docs unchanged now (light lane: at M7, BL row).
- Open questions: one batched inbox file, `design/inbox/20261025-mirror-as-of-date.md` (the date `account_balances` and `lots` are computed at). Those rows are marked `owner:` and the rest proceeds.
- Base SHA: 48899c4.

## Decisions
- Three layers, as the ADRs require: internal base tables `m_<name>`, the public `r_<name>` views (documented columns only), and the v0.2.2 names as compatibility views over `r_*` with exactly the v0.2.2 columns in v0.2.2 order (ADR-0015, ADR-0048) (agent).
- `meta` is on no ADR list (ADR-0048 enumerates 17 tables; the v0.2.2 exporter creates 18), but it is a v0.2.2 mirror table, so its name is served too; its keys change (ADR-0015) (agent; the conservative reading).
- `latest_prices` (v0.3.0 only) is not rebuilt: CAP-QUERY-SURFACE's note replaces it with `price_asof` (ADR-0016); the WS-MIRROR backlog text saying "materialized `latest_prices`" is corrected (agent).
- v0.2.2 output shapes are kept where nothing decided otherwise: metadata JSON keeps Beancount's `filename`, `lineno`, `__tolerances__`, `__automatic__` keys and `json.dumps` defaults; `values_json` keeps its `[value, type-tag]` pairs (the v0.2.2 budget resolver depends on them); `ledger_options` keeps every option (agent; measured on Beancount 3.2.0 with a synthetic ledger).
- Money TEXT is plain positional notation with the written scale (`format(d, "f")`), never an exponent; tags and links JSON arrays are sorted (v0.2.2's order was undefined) (agent).
- `account_type` keeps v0.2.2's meaning (first segment); the canonical kind is a new column `account_kind` (ADR-0024), so custom-root ledgers do not silently change existing SQL (agent).
- Pad pairing comes from Beancount's own padding transactions (matched by the balance's source location), not a re-implementation: v0.2.2's rule ignored currencies and parent accounts (ADR-0024, measured against `beancount/ops/pad.py`) (agent).
- `transaction_content_hash` is computed with v0.2.2's content-hash formula, the same one the fallback id uses (ADR-0055 leaves the canonical form to the spec) (agent).
- `source_account`: when several legs carry an `import-key`, the first in posting order (ADR-0055 says "the leg"; the tie is not decided there) (agent).
- `is_currency` follows ADR-0025 literally: declared `asset-class` first, else ISO-4217 membership; `operating_currency` is not a signal; status `draft` until M8's first slice confirms it (agent).
- `commodities` covers every commodity a posting uses as units, cost or price, and both sides of `price` directives (v0.2.2: units only); Beancount 3.2.0's `options["commodities"]` is empty, so it cannot serve as the list (agent).
- Planning columns the write contract needs (entry source location, `entry_hash`) are added by M1b's slice through this spec; base-table additions do not bump the surface version (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Tests first: on the base commit `scripts/tests/test_mirror_surface.py` fails (its module, spec and checker do not exist there).
- Checks that refuse, each with its must-not-pass test (`scripts/tests/test_mirror_surface.py`; every assertion names the specific rule's message, so a second rule firing cannot hide a dead one):
  - fields closed and non-empty → `Fields.test_a_missing_or_extra_field`, `test_empty_text`; vocabularies (type, format, origin, status, change, user_sql, as_of, table origin and status) → `test_closed_vocabularies`; exact numbers TEXT with a sign, no sign elsewhere → `test_money_is_text_with_a_sign`; format fixes the type → `test_format_fixes_the_type`; names (duplicates, `m_<name>` base, no `m_`/`r_` public names) → `test_names`; surface version, empty table, missing inventories, function fields → `Shape.*`.
  - v0.2.2 kept (table, column, user_sql under a public view, function entry with message, located notice, citation), never dropped → `V022Accounting.*`.
  - v0.3.0 kept or dropped, not both; drops cited, real, with the right origin → `V030Accounting.*`.
  - origins confirmed by the inventories; `new` only and always on v0.4 columns → `Origins.*`.
  - changes need a reason and a citation; citations resolve (tables, columns, functions, drops; non-text, unknown ADR/CAP/BL/INV, missing or answered inbox file) → `Citations.*`.
  - `owner:` needs an open inbox file (not `answered/`, not README); decided/draft items carry no open question; columns of an `owner:` table are `owner:` too → `Status.*`.
  - inventories by behaviour: `derive_tables` runs the DDL (comment and non-statement strings create nothing), `derive_functions` reads registrations (unregistered and commented-out classes do not count), `inventory_problems` catches a hidden column, table, reordering or function → `Derivation.*`; pinned counts → `RealSpec.test_pinned_inventories`; re-derived from the tags when present → `test_pinned_inventories_match_the_legacy_code`.
- Ways to loosen or bypass, listed before coding and each tested: extra fields; whitespace-only text; lowercase or REAL types; money as INTEGER or with `n/a` or empty sign; relabelling a v0.2.2 column as `new` (with a matching `change`, so only the origin rule can catch it); dropping a v0.2.2 item after deleting it from the table; keeping and dropping one item; citing an ADR with a trailing space, a short ADR number, an unknown invariant, an answered or nonexistent inbox file, or a non-list; an `owner:` question hidden in a decided row's text; editing the pinned inventory to match a trimmed spec (caught by the counts and the re-derivation).
- Self-mutation: 59 loosening mutants over every guard in `scripts/check_mirror_surface.py` (plan in the session scratchpad); 59 tried / 58 killed in the first run. The survivor, a loosened citation regex, was equivalent (every string the regex refused also failed the reference lookup) and the regex was stricter than the lookup (it refused real backlog IDs such as `L1.M5`), so it was removed; its replacement mutant (non-text citations) was killed. Final run: 59 tried / 59 killed.
- Population: the inventory is the code that creates mirror tables at each tag, found by behaviour: every `CREATE TABLE` string in `backend/app/services/sqlite_exporter.py` executed in SQLite, plus a search of the whole `backend/` at both tags for other `CREATE TABLE`/`VIEW`/`create_function`/`create_aggregate` (none; no SQL UDFs), and the compute registry's `registry.register(...)` calls.
  - v0.2.2: in 18 tables, 121 columns, 1 compute function → out: 18 tables kept (17 with a public view, `meta` internal), 121 columns kept (111 same, 10 changed with citations), 0 dropped; `budget_for_range` with a compatibility entry.
  - v0.3.0 (`legacy-final`): in 19 tables, 126 columns, 4 functions → out: 18 tables kept, `latest_prices` (4 columns) dropped with citations, `postings.cost_date` kept; 3 functions dropped with citations.
  - v0.4 adds 4 columns (`postings.account_kind`, `postings.cost_label`, `accounts.account_kind`, `ledger_errors.error_type`). Spec total: 18 tables, 126 columns; 104 decided, 8 draft, 14 waiting for the owner (`account_balances`, `lots`).
  - User docs: `reference/querying-data.md` on the docs `main` (released) and `v0.4` branches, checked column by column against the v0.2.2 DDL; 9 differences recorded in the spec README (BL-0058).
- Claims measured on Beancount 3.2.0 (oracle venv, synthetic ledgers in the scratchpad): the metadata JSON keys Beancount adds, the `values_json` pair shape, option values, error `entry_json`, padding transactions carrying the assertion's location, one `pad` serving two currencies, the balance difference's sign, narration `""` and payee `None` when absent, `options["commodities"]` empty. SQLite 3.53.4: `'0.00' > 0` is true on a TEXT column.

## Verifier

## Retro
