# ADR-0016: SQLite is the only query engine, behind one hardened QueryRunner

This record decides the engine for user SQL over the mirror and how user queries are confined.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** mirror, recipes, security

## Context

Users write SQL in dashboards and in Analyze (INV-08). v0.3 had five SQL runners and float sums of money. The mirror spike compared SQLite with DuckDB-over-SQLite on 53 seed queries over a ~109k-posting mirror.

## Decision

(owner, 2026-10-24)
- **SQLite only.** Spike results: latency 21.6 ms vs 32–45 ms; process memory 24–28 MB vs 134–167 MB (about 2 MB vs 55 MB per query); 53/53 seed queries unchanged vs 25/53, with 5 silently wrong under DuckDB; DuckDB adds about 66 MB and a per-platform extension. Users' existing SQL needs no migration.
- **One `QueryRunner`** (in `mirror/query.py`) for every user query: `query_only`, `SQLITE_LIMIT_ATTACHED=0`, an authorizer allowlist (no `PRAGMA` or `pragma_*()`), a single statement, a timeout, a string-length limit and a row cap. A read-only open mode alone is not enough. Internal SQL is `LiteralString`; user SQL is the `UserSql` type.
- **SQL helpers expose kernel semantics:** `dsum` (an exact decimal sum returned as REAL, so comparisons and ordering keep working) and `price_asof`. The AI and users describe tables through a schema-describe helper, not `PRAGMA`.
- **SQL over earlier steps:** our code attaches an in-memory `steps` schema and loads the step outputs before lockdown, then runs the user's SQL (ADR-0029).
- **BQL** (beanquery) is allowed only for `SELECT`, `BALANCES`, `JOURNAL` and `PRINT`. BQL steps share *entries*, never a connection.

## Alternatives considered and rejected

- **DuckDB over the SQLite mirror.** Rejected on memory (decisive for hosting), compatibility (silently wrong results: 4-byte `REAL`, quoted `json_extract`), footprint and confinement (its SQLite extension could reach other files, accepts several statements and has no timeout hook).
- **A TEXT-returning `dsum`.** Rejected: it breaks `HAVING amount != 0`, `>` and `ORDER BY` in existing recipes.
- **`dmul`/`dquant` helpers.** Dropped as unnecessary.

## Consequences

- Float appears only in chart math and in the documented REAL result of `dsum` (INV-06).
- Seed recipes and AI guides use `dsum` and `price_asof`.

## References

review:14 §0a item 2, L5-07; review:11 L3-07, Q9; review:18 §2 inv. 5, L11-07; ADR-0015, ADR-0029, ADR-0040; INV-06, INV-08, INV-09; CAP-ENGINE-SQLITE, CAP-QUERY-SQL, CAP-QUERY-BQL.
