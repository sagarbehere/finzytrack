# Mirror: the SQL surface

## Orientation
- **What exists:** this spec only (S0021): every table and column the v0.4 mirror has at M1a (`surface.toml` → generated `surface.md`), the `r_*` views, the v0.2.2 compatibility names, and a stdlib check. No mirror code yet: the engine builds and publishes the mirror in M1a (ADR-0054, ADR-0015); user SQL reaches it in M7.
- **Entry points:** `design/specs/mirror/surface.toml` (edit this); `scripts/gen_mirror_surface.py`; `scripts/check_mirror_surface.py` (`--legacy` also re-derives the v0.2.2 and v0.3.0 inventories from the legacy tags).
- **Decisions it applies:** ADR-0015 (rebuildable cache, `r_*` is the public surface), ADR-0016 (SQLite only, one hardened QueryRunner), ADR-0023 (money is TEXT), ADR-0024 (Beancount decides; canonical account kind), ADR-0025 (`is_currency`), ADR-0026 (TransactionId), ADR-0030 (price sidecar), ADR-0031 (label-aware lots), ADR-0048 (v0.2.2 names kept through 0.4.x), ADR-0055 (`transaction_content_hash`, `source_account`).
- **Invariants:** INV-05 (its view golden, BL-0005, will check the DDL against this spec), INV-06, INV-21 (fallback ids; BL-0021).
- **Traps:** money columns are TEXT, so `amount > 0` compares *text* (`'0.00' > 0` is true); cast first. Metadata keys with `-` need a quoted JSON path. `posting_id` and every `id` are positions, not identities. Do not re-implement a Beancount rule in the mirror (pad pairing, booking, tolerances): read Beancount's result. Two tables wait for the owner (`owner:` rows): do not build them before the answer.
- **Main tests:** `scripts/tests/test_mirror_surface.py` (the real spec passes; a must-not-pass per rule; the legacy inventories pinned and re-derived). Later: the M1a mirror-build tests, the DDL snapshot and per-table checksums (BL-0057), the view golden (BL-0005), the INV-21 goldens (BL-0021).

## Three layers

| Layer | Names | Who reads it | May change |
|---|---|---|---|
| Base tables | `m_<name>` | the app (`mirror.reader`) | any release; internal |
| Public views | `r_<name>`, one per table with documented columns | user SQL, dashboards, the AI, outside tools | only as rule 2 below allows |
| Compatibility views | the 18 v0.2.2 table names | users' existing SQL | never in 0.4.x (ADR-0048); dropped later with a warning |

A compatibility view has exactly the v0.2.2 columns, in v0.2.2 order, read from `r_<name>` (`meta` has no public view and is read from `m_meta`). The public view has the columns marked `user_sql = true`: those are the columns documented for user SQL. Columns marked `false` live only in the base table.

Views use only SQLite's built-in functions, so an outside tool (DB Browser, Python's `sqlite3`) can open the mirror and read every view. Helpers that need our code (`dsum`, `price_asof`), the describe views (`r_columns`, `r_tables`) that replace `PRAGMA table_info`, the in-memory `steps` schema and an as-of lots helper arrive in M7/M8 (ADR-0016, ADR-0029, CAP-QUERY-SURFACE). The mirror spike's deduplicated price view will need a name other than `r_prices`, which this spec gives to the plain table.

## Conventions

**Exact numbers** (`format = "decimal"`): TEXT, plain positional notation (`format(d, "f")`, never `1E+2`), with the scale the ledger wrote; a number Beancount computed keeps the scale its computation produced; nothing is rounded. Every such column states its **sign convention**. The rule for ledger amounts: positive is a debit, negative a credit. Example: buying groceries for 10.50 INR from a bank account gives `Expenses:Food` `10.50` and `Assets:Bank` `-10.50`; a salary of 1,000 gives `Income:Salary` `-1000`. Until `dsum` arrives (M7), aggregate with `SUM(CAST(amount AS REAL))`; comparisons need a cast too, because SQLite compares a TEXT column with a number as text.

**Dates** are TEXT `YYYY-MM-DD`, civil dates as written in the ledger.

**As of** (per table):
- `entry`: one row per directive or posting, carrying its own date; nothing is summed over time.
- `ledger-end`: a summary over every entry, future-dated ones included.
- `load`: facts about the load itself (options, errors, the mirror's own metadata).
- `owner:<file>`: waiting for the owner (`account_balances`, `lots`: today or the end of the ledger).

**Ids.**
- `id` and `posting_id` are positions in the load, counting from 1. They change when entries are added or removed earlier in the ledger.
- `postings.transaction_id` is the TransactionId (ADR-0026, INV-21), first rule that applies: the legacy `id` meta; the legacy `transaction_id` meta; `k:` plus the first element of the first `import-key` on the entry's postings (posting order); the **content fallback**. The fallback is the hash below, and the n-th repeat of a hash among the entries that reach this step (n ≥ 1, in entry order) gets `-n` appended: two identical coffees on one day give `<hash>` and `<hash>-1`.
- The **content hash** (also `transaction_content_hash`) is v0.2.2's formula, kept byte for byte so ids of ledgers v0.2.2 read do not change: SHA-256 (lowercase hex) of the UTF-8 text `date|payee|narration|amount|account`, where `account` is the legacy `source_account` meta if present, else the first posting whose account is an Assets or Liabilities account, else the first posting's account; `amount` is `<number> <currency>` of the first posting on that account (the number as `str(Decimal)`), or `0`; payee and narration are empty strings when absent. The INV-21 goldens (BL-0021) pin it against v0.2.2's output.

**Metadata JSON** (`json-meta`, and the values inside `json-custom`, `json-entry`, `json-value`), converted as v0.2.2 did:
- keys are kept as written, including the keys Beancount adds: `filename` (absolute path), `lineno`, `__tolerances__` on transactions, `__automatic__` on postings whose amount Beancount filled in;
- a Decimal becomes a string (`"10.50"`), a date an ISO string, an Amount a two-element array `["1.25", "USD"]`, `TRUE` a JSON `true`, sets and tuples arrays; anything else its Python text;
- serialized with Python's `json.dumps` defaults (non-ASCII escaped as `\u....`; `json_extract` decodes it);
- a kebab-case key needs a quoted path: `json_extract(posting_metadata_json, '$."import-key"')`; a plain key does not: `'$.memo'`;
- `custom_directives.values_json` is an array of `[value, type-tag]` pairs, the type tag being Beancount's Python type as text (`"<AccountDummy>"`, `"<class 'str'>"`); v0.2.2's budget resolver reads `value` by position, so the shape stays;
- `ledger_errors.entry_json` is the entry's fields as an array, in Beancount's field order.

**`is_currency`** (cash or holding; ADR-0025, status draft until M8's first slice). Example: an INR ledger with `option "operating_currency" "INR"` and a USD bank account. v0.2.2 marked USD as a holding (0), because only operating currencies counted as cash; v0.4 marks it cash (1), because USD is an ISO 4217 currency code. A declared `asset-class` still decides first: `asset-class: "cash"` makes any code cash, `asset-class: "etf"` makes it a holding.

**Pad pairing** (`balance_assertions.pad_date`, `pad_source_account`). A `pad` asks Beancount to insert a transaction that makes the next `balance` assertion come out right. Beancount's own rule (`beancount/ops/pad.py`): a pad on an account stays active until the next pad on it; each currency can be padded once per active pad; an assertion that already matches gets no padding. The mirror does not re-implement this. It reads Beancount's result: each padding transaction (flag `P`) carries the pad's metadata on the transaction and the assertion's metadata (`filename`, `lineno`) on its postings, so the assertion at that file and line is paired with that pad. An assertion with no padding transaction has NULL in both columns. Example: `2020-01-04 pad Assets:Bank:Usd Equity:Opening`, then `2020-01-05 balance Assets:Bank:Usd 50 USD` on an account that holds nothing: Beancount inserts a padding transaction of `50 USD` from `Equity:Opening`, dated 2020-01-04, and the assertion row gets `pad_date = 2020-01-04`, `pad_source_account = Equity:Opening`.

**`source_account`** (ADR-0055) is computed per entry, the same for all its postings: the legacy `source_account` meta; else the first leg carrying an `import-key`; else the single Assets or Liabilities leg; else NULL.

## Compatibility (ADR-0048)

- Every v0.2.2 table name is served (the list, with columns, is in `surface.md`). The v0.2.2 exporter creates 18 tables; ADR-0048 lists 17 because it leaves out `meta`, the exporter's freshness table. Its name is served too; its keys change (ADR-0015).
- `lots` comes from Beancount's booked inventory, label-aware; v0.2.2's end-of-ledger computation is not rebuilt. Its date waits for the owner (`design/inbox/20261025-mirror-as-of-date.md`).
- `budget_for_range`, the only v0.2.2 compute function, keeps working through 0.4.x over the `ledger` step's budget measures, with a located deprecation notice naming the replacement (CAP-FN-BUDGET-FOR-RANGE, M7).
- Things only v0.3.0 had (`latest_prices`, `portfolio_series`, `portfolio_returns`, `cash_instruments`) were never released and are not rebuilt; `postings.cost_date` (v0.3.0) is kept.

## Not here yet

- The **DDL snapshot** (`contracts/mirror-ddl.sql`, `mirror-views.md`) and **per-table checksums** over the corpus ledgers are generated from the M1a mirror build's code, which is checked against this spec (BL-0057). They are not written by hand.
- The **planning columns** the write contract needs (each entry's source file and line, `entry_hash`; review:D1 §10, review:28 §4.1) are added by M1b's slice through this spec. Adding base or public columns does not change the surface version.

## Where the v0.2.2 user docs differ from the code

Checked in S0021: `reference/querying-data.md` on the docs repo's `main` (the released docs) against the `v0.2.2` exporter. The docs' `v0.4` branch adds a `latest_prices` section, which v0.4 does not have. The fixes go into the `v0.4` docs with the M7 surface (BL-0058).
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
