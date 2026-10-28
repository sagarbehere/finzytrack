# Mirror: the SQL surface

## Orientation
- **What exists:** this spec (S0021): every table and column the v0.4 mirror has at M1a (`surface.toml` → generated `surface.md`), the `r_*` views, the v0.2.2 compatibility names, and a stdlib check. The build (S0050, in the engine): `ledger_engine.mirror.build.build` writes every table and view of this spec into a fresh SQLite file from one overlay load, with the rows of every table but `commodities` (BL-0120, waiting for `design/inbox/20261028-iso-currency-list.md`) and the ledger's prices only (the sidecar's, BL-0121). The reads (S0052, in the main sidecar, `app.mirror`): a read-only open of a generation file given by its path, checked to be a finished mirror (rollback mode, no hot journal, a well-formed `meta`), the typed reads `Reader.info()` (`meta`) and `Reader.ledger_errors(limit)`, and the hardened `QueryRunner` of ADR-0016 (an authorizer allowlist, no `ATTACH`, a timeout that stops the query, a row cap, and memory bounds that hold while it runs: values, columns, the rows' memory and SQLite's heap, with temporary storage in memory so no query writes a temp file), which reads every table and view of this spec. Not built: the generation publish (BL-0113), so the reads take a path, not a pinned generation; the freshness check (BL-0125); `dsum`, `price_asof`, the describe views and the `steps` schema (M7); user SQL reaches the app in M7.
- **Entry points:** `design/specs/mirror/surface.toml` (edit this); `scripts/gen_mirror_surface.py`; `scripts/check_mirror_surface.py` (`--legacy` also re-derives the v0.2.2 and v0.3.0 inventories from the legacy tags).
- **Decisions it applies:** ADR-0015 (rebuildable cache, `r_*` is the public surface), ADR-0016 (SQLite only, one hardened QueryRunner), ADR-0023 (money is TEXT), ADR-0024 (Beancount decides; canonical account kind), ADR-0025 (`is_currency`), ADR-0026 (TransactionId), ADR-0030 (price sidecar), ADR-0031 (label-aware lots), ADR-0048 (v0.2.2 names kept through 0.4.x), ADR-0055 (`transaction_content_hash`, `source_account`).
- **Invariants:** INV-05 (its view golden, BL-0005, will check the DDL against this spec), INV-06, INV-21 (fallback ids and metadata keys: [`inv21-goldens.md`](inv21-goldens.md); BL-0021).
- **Traps:** money columns are TEXT, so `amount > 0` compares *text* (`'0.00' > 0` is true); cast first. Metadata keys with `-` need a quoted JSON path. `posting_id` and every `id` are positions, not identities. Do not re-implement a Beancount rule in the mirror (pad pairing, booking, tolerances): read Beancount's result. A row marked `owner:` waits for the owner: do not build it before the answer (none open since 2026-10-25). `account_balances` and `lots` stop at today: the build date is part of the mirror's freshness.
- **Main tests:** `scripts/tests/test_mirror_surface.py` (the real spec passes; a must-not-pass per rule; the legacy inventories pinned and re-derived). `scripts/oracle/tests/test_inv21_goldens.py` (the INV-21 goldens, S0022). `engine/tests/mirror/` (S0050: the built DDL against `surface.toml` with planted defects, the INV-21 goldens and the `balance` vectors through the built file, one test per row rule, the fresh-file and rounding refusals). `backend/tests/mirror/` (S0052: the runner's confinement and limits, the read-only open, the typed reads, over a file made from `contracts/mirror-ddl.sql`). Later: the per-table checksums (BL-0057), the view golden (BL-0005).

## Three layers

| Layer | Names | Who reads it | May change |
|---|---|---|---|
| Base tables | `m_<name>` | the app (`mirror.reader`) | any release; internal |
| Public views | `r_<name>`, one per table with documented columns | user SQL, dashboards, the AI, outside tools | only as rule 2 below allows |
| Compatibility views | the 18 v0.2.2 table names | users' existing SQL | never in 0.4.x (ADR-0048); dropped later with a warning |

A compatibility view has exactly the v0.2.2 columns, in v0.2.2 order, read from `r_<name>` (`meta` has no public view and is read from `m_meta`). The public view has the columns marked `user_sql = true`: those are the columns documented for user SQL. Columns marked `false` live only in the base table.

Every column of an `r_*` or compatibility view is a plain reference to a base-table column (no expression, cast or function), so it keeps the column's TEXT or INTEGER affinity and a v0.2.2 query such as `WHERE amount > 0` behaves exactly as on v0.2.2; BL-0057's DDL golden checks this. Views use only SQLite's built-in functions, so an outside tool (DB Browser, Python's `sqlite3`) can open the mirror and read every view. Helpers that need our code (`dsum`, `price_asof`), the describe views (`r_columns`, `r_tables`) that replace `PRAGMA table_info`, the in-memory `steps` schema and an as-of lots helper arrive in M7/M8 (ADR-0016, ADR-0029, CAP-QUERY-SURFACE). The mirror spike's deduplicated price view will need a name other than `r_prices`, which this spec gives to the plain table.

## Conventions

**Exact numbers** (`format = "decimal"`): TEXT, plain positional notation (`format(d, "f")`, never `1E+2`), with the scale the ledger wrote; a number Beancount computed keeps the scale its computation produced. v0.2.2 wrote `str(Decimal)`, which gives an exponent for some computed values (`1E-8`): the number is the same, the text differs, so those columns are marked `changed` (ADR-0023, ADR-0038). Sums and products the mirror computes itself (`account_balances.balance`, `lots.book_value`, `commodity_usage.total_volume`) use a local decimal context of 64 significant digits with the `Inexact` trap set: a result that would need rounding fails the build instead of being rounded (fail closed, INV-22). The same context holds every other Decimal operation the build makes that could round: the merge and reduction of lots in Beancount's inventory (`lots.units_number`), and a magnitude (taken with `copy_abs()`, since `abs()` rounds to Python's default 28 digits). So the build itself never rounds, but it copies the numbers as Beancount loaded them, and Beancount's load uses Python's default 28 significant digits. Today, that rounds two kinds of numbers before the build sees them, silently and with no load error: a written **negative** number with more than 28 significant digits (the parser treats the minus as an operation: `-12345678901.123456789012345678 SHIB` loads as `-12345678901.12345678901234568`), and a number Beancount computed (an interpolated amount, a balance difference). `postings.amount`, `account_balances` and `lots` then carry the rounded value. Whether the load keeps such numbers exact, follows Beancount or refuses them is open (BL-0123, owner question `design/inbox/20261028-numbers-beyond-28-digits.md`). Every such column states its **sign convention**. The rule for ledger amounts: positive is a debit, negative a credit. Example: buying groceries for 10.50 INR from a bank account gives `Expenses:Food` `10.50` and `Assets:Bank` `-10.50`; a salary of 1,000 gives `Income:Salary` `-1000`. Until `dsum` arrives (M7), aggregate with `SUM(CAST(amount AS REAL))`; comparisons need a cast too, because SQLite compares a TEXT column with a number as text.

**Dates** are TEXT `YYYY-MM-DD`, civil dates as written in the ledger.

**As of** (per table):
- `entry`: one row per directive or posting, carrying its own date; nothing is summed over time.
- `ledger-end`: a summary over every entry, future-dated ones included.
- `load`: facts about the load itself (options, errors, the mirror's own metadata).
- `today`: a summary over the entries dated up to the build date in the user's time zone, future-dated ones excluded; the mirror records that date and is rebuilt when the date changes (ADR-0025 amendment, 2026-10-25).
- `owner:<file>`: waiting for the owner (none open).

**Ids.**
- `id` and `posting_id` are positions in the load, counting from 1. They change when entries are added or removed earlier in the ledger.
- `postings.transaction_id` is the TransactionId (ADR-0026, INV-21), first rule that applies:
  1. the legacy `id` meta, as its Python text (`str()`): `id: 42` gives `42`, a key with no value gives `None`, `id: ""` gives the empty text. The value is used as is, so an empty or copied legacy id, or a legacy id that happens to equal another entry's content fallback (before v0.1, `transaction_id` was the content hash), gives two entries the same TransactionId (BL-0059);
  2. the legacy `transaction_id` meta, the same way;
  3. `k:` plus the first element of the first posting `import-key` that has one (posting order; elements are separated by whitespace). A posting whose `import-key` is not text or has no element is skipped; an `import-key` on the transaction itself is not an identity (ADR-0026 puts it on postings);
  4. the **content fallback**: the hash below, and the n-th repeat of a hash among the entries that reach this step (n ≥ 1) gets `-n` appended: two identical coffees on one day give `<hash>` and `<hash>-1`. Entries settled by rules 1 to 3 take no ordinal.

  Entries are counted in the order Beancount's loader returns them: by date, then directive type, then line number. The file does not count, so moving an entry to another file can change which twin gets which ordinal; on equal line numbers in two files, the order in which Beancount read the files decides. Padding transactions (flag `P`) are transactions like any other.
- The **content hash** (also `transaction_content_hash`) is v0.2.2's formula (`BeancountEngine.compute_hash_from_transaction` and `compute_content_hash` at the `v0.2.2` tag), kept byte for byte so the ids of ledgers v0.2.2 read do not change: SHA-256 (lowercase hex) of the UTF-8 text `date|payee|narration|amount|account`, where:
  - the account is chosen as v0.2.2 did, by literal text: if the transaction has a `source_account` meta key, its value; else the first posting whose account name starts with the text `Assets:` or `Liabilities:`; if that gives nothing (no such posting, or a meta value Python treats as false: empty text, `0`, `FALSE`), the first posting's account. This is a legacy formula, so the literal prefixes win over ADR-0024's account-kind rule here: on a ledger with custom root names the fallback is the first posting, exactly as in v0.2.2;
  - `amount` is `<number> <currency>` of the first posting whose account equals the chosen value as written (untrimmed) and has units; `0` when none. The number is Python's `str(Decimal)` of the number after Beancount filled in a missing amount, **not** the mirror's plain notation (`format(d, "f")`, "Exact numbers" above): `0.00000001 BTC` hashes as `1E-8 BTC`. Reusing the mirror's number formatter here would change these ids;
  - the comparison is text against text: a value that is not text (a number, `TRUE`, a date, an Amount) matches no posting. v0.4 difference: v0.2.2 compared the raw value, which for an Amount (`source_account: 10.00 USD`) raised an error and failed the whole mirror export, so there is no released id to keep; v0.4 hashes such an entry with amount `0` and the value's text (`10.00 USD`) as the account;
  - `account` in the text is the chosen value as Python text (`str()`) with surrounding whitespace removed; `date` is ISO; payee and narration are empty strings when absent and otherwise exactly as Beancount read them: no whitespace is trimmed or collapsed (payee `Cafe ` and `Cafe` give different texts, and so do a tab, a no-break space or a line break inside the narration). The fields are joined without escaping, so payee `A|B` with narration `C` and payee `A` with narration `B|C` give the same text, and the second gets an ordinal.

  The INV-21 goldens (`inv21-goldens.md`, S0022) pin all of this against v0.2.2's output.

**Metadata JSON** (`json-meta`, and the values inside `json-custom`, `json-entry`, `json-value`), converted as v0.2.2 did:
- keys are kept as written, including the keys Beancount adds: `filename` (absolute path), `lineno`, `__tolerances__` on transactions, `__automatic__` on postings whose amount Beancount filled in;
- a Decimal becomes a string (`"10.50"`), a date an ISO string, an Amount a two-element array `["1.25", "USD"]`, `TRUE` a JSON `true`, sets and tuples arrays; anything else its Python text;
- serialized with Python's `json.dumps` defaults (non-ASCII escaped as `\u....`; `json_extract` decodes it);
- a kebab-case key needs a quoted path: `json_extract(posting_metadata_json, '$."import-key"')`; a plain key does not: `'$.memo'`;
- `custom_directives.values_json` is an array of `[value, type-tag]` pairs, the type tag being Beancount's Python type as text (`"<AccountDummy>"`, `"<class 'str'>"`); v0.2.2's budget resolver reads `value` by position, so the shape stays;
- `ledger_errors.entry_json` is the entry's fields as an array, in Beancount's field order.

**`is_currency`** (cash or holding; ADR-0025 and its 2026-10-25 amendment). Rule: a declared `asset-class` decides first; else a code is cash when it is an ISO 4217 code or listed in `operating_currency`; else it is a holding. Example: an INR ledger with `option "operating_currency" "INR"` and a USD bank account. v0.2.2 marked USD as a holding (0), because only operating currencies counted as cash; v0.4 marks it cash (1), because USD is an ISO 4217 currency code. A non-ISO operating currency such as `RMB` stays cash. A declared `asset-class` still decides first: `asset-class: "cash"` makes any code cash, `asset-class: "etf"` makes it a holding.

**Pad pairing** (`balance_assertions.pad_date`, `pad_source_account`). A `pad` asks Beancount to insert a transaction that makes the next `balance` assertion come out right. Beancount's own rule (`beancount/ops/pad.py`): a pad on an account stays active until the next pad on it; each currency can be padded once per active pad; an assertion that already matches gets no padding. The mirror does not re-implement this. It reads Beancount's result: each padding transaction (flag `P`) carries the pad's metadata on the transaction and the assertion's metadata (`filename`, `lineno`) on its postings, so the assertion at that file and line is paired with that pad. An assertion with no padding transaction has NULL in both columns. Example: `2020-01-04 pad Assets:Bank:Usd Equity:Opening`, then `2020-01-05 balance Assets:Bank:Usd 50 USD` on an account that holds nothing: Beancount inserts a padding transaction of `50 USD` from `Equity:Opening`, dated 2020-01-04, and the assertion row gets `pad_date = 2020-01-04`, `pad_source_account = Equity:Opening`.

**`source_account`** (ADR-0055 and its 2026-10-25 amendment) is computed per row, and gives every existing entry the value v0.2.2 gave it: the legacy `source_account` meta whenever the key is present (every row); else the first leg carrying an `import-key` (every row; v0.4 imports mark the leg v0.2.2 imports named in the key); else, in a two-leg entry, the other leg; else the first other leg whose account starts with `Assets:` or `Liabilities:`; else NULL. Example: in a hand-written `Expenses:Food 50` / `Assets:Bank -50`, the food row shows `Assets:Bank` and the bank row shows `Expenses:Food`.

## Compatibility (ADR-0048)

- Every v0.2.2 table name is served (the list, with columns, is in `surface.md`). The v0.2.2 exporter creates 18 tables; ADR-0048 lists 17 because it leaves out `meta`, the exporter's freshness table. Its name is served too; its keys change (ADR-0015).
- `lots` comes from Beancount's booked inventory, label-aware; v0.2.2's end-of-ledger computation is not rebuilt. It is taken as of today (ADR-0025 amendment, 2026-10-25).
- `budget_for_range`, the only v0.2.2 compute function, keeps working through 0.4.x over the `ledger` step's budget measures, with a located deprecation notice naming the replacement (CAP-FN-BUDGET-FOR-RANGE, M7).
- Things only v0.3.0 had (`latest_prices`, `portfolio_series`, `portfolio_returns`, `cash_instruments`) were never released and are not rebuilt; `postings.cost_date` (v0.3.0) is kept.

## Not here yet

- The **per-table checksums** over the corpus ledgers, generated from the mirror build once every table has rows (BL-0057, BL-0120). The **DDL snapshot** exists since S0050: `contracts/mirror-ddl.sql`, generated from the build's table list (`ledger_engine.mirror.core.schema`), which `engine/tests/mirror/test_surface.py` checks against this spec; a separate `mirror-views.md` is not generated, since `surface.md` already documents every view column.
- The **planning columns** the write contract needs (each entry's source file and line, `entry_hash`; review:D1 §10, review:28 §4.1) are added by M1b's slice through this spec. Adding base or public columns does not change the surface version.

## Where the v0.2.2 user docs differ from the code

Checked in S0021: `src/content/docs/reference/querying-data.md` in the docs repo (`finzytrack-docs`), on its `main` branch (the released docs) against the `v0.2.2` exporter. The docs' `v0.4` branch adds a `latest_prices` section, which v0.4 does not have. The fixes go into the `v0.4` docs with the M7 surface (BL-0058).
- `transaction_id` is documented as a UUID; it can also be a legacy `transaction_id` value or a content hash with a `-n` suffix (and in v0.4 a `k:` id).
- `transaction_flag` is documented as `*` or `!`; padding transactions have `P`, and other flag letters occur.
- `source_account` is documented as the originating Assets or Liabilities account; v0.2.2's code gave each posting of a two-leg entry the other posting's account, whatever its kind (v0.4 follows the docs' meaning, ADR-0055).
- "SQLite implicit cast works for plain comparisons" (`WHERE amount > 0`) is wrong for TEXT columns: `'0.00' > 0` is true.
- `account_balances.transaction_count` counts postings, not transactions; zero balances have no row (undocumented).
- `ledger_errors` holds every load error, not only parsing errors.
- The cross-table example selects `c.type`, a column `commodities` does not have.
- Undocumented: `balance_assertions.pad_date` and `pad_source_account`; the `meta` table; the Beancount keys inside the metadata JSON; the `[value, type-tag]` shape of `values_json`; `notes`/`documents` tags and links are NULL (not `[]`) when empty.
- v0.2.2's `lots` kept only one of two lots that differ only in their label (its unique key leaves the label out).

## Rules

1. `surface.toml` is the contract. The M1a build's DDL must match it (BL-0057); a column is built only when its row is `decided` or `draft`.
2. A change to a public column's meaning, type or name, or its removal, needs an ADR, bumps `surface_version`, and is the owner's when users' SQL would see it (rubric items 1, 2, 5). Adding a column needs neither.
3. v0.2.2 names and columns are never dropped in 0.4.x. A v0.3.0 item is kept or dropped with a citation.
4. A row marked `owner:<file>` is built only after the answer; the answer turns it into `decided` in the same change that moves the inbox file to `answered/`.
