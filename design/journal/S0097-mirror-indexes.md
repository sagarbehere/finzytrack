# S0097: indexes on the mirror's base tables (BL-0122)

## Brief
- Goal: decide, by the reads that exist on `next` at ca9d6661, whether the mirror's base tables need an index at M1a (BL-0122), and build only the indexes a named read needs, each with a measured gain against its build-time and file-size cost (owner's rule: tie complexity to the requirement). Never a UNIQUE index on `transaction_id` (BL-0059).
- Implements: ADR-0015 (rebuildable cache), ADR-0016 (one hardened QueryRunner), ADR-0011 (no resident ledger); spec `design/specs/mirror/README.md`, `surface.toml`.
- Capabilities: CAP-DATA-MIRROR (partial, M2), CAP-ENGINE-SQLITE (planned, M7).
- Invariants touched: none if no index is built (INV-10 bounds, INV-21 rows unchanged by an index).
- Risk class / lane: Low (no index built: a brief with evidence only). Would be Medium, full lane for the build's DDL if an index were needed.
- Acceptance tests: none written; the slice stops after the brief (below).
- Expected doc impact: this journal only. The row's milestone is the owner's call (pre-push deferral check), so `design/backlog.md` is not edited here.
- Open questions: moving BL-0122 from M1a to M1b with the TransactionId lookup (BL-0059) needs the owner's words; escalated by the orchestrator in `design/inbox/20261110-mirror-indexes-to-m1b.md`. S0097 is parked until the answer. The broken `measure_mirror_build.py` the slice found is BL-0235.

### Inventory of mirror reads (by behaviour, ca9d6661)
Built from every place product code opens a mirror file (`sqlite3.connect` in `backend/src`, `engine/src`) and every SQL text run on it, not from names:

| Read | Code | SQL | Table size | Plan |
|---|---|---|---|---|
| `Reader.info` (envelope's `ledger_version`) | `app.mirror.files.read_info` | `SELECT key, value FROM m_meta LIMIT 65`, `sqlite_schema` lookups | 9 rows | scan of 9 rows |
| `Reader.ledger_errors(limit)` | `app.mirror.reader._errors` | `COUNT(*)` and `... FROM m_ledger_errors ORDER BY id LIMIT ?` | the load's errors (0 on the demo ledgers) | scan plus top-N sort |
| `QueryRunner.run` | `app.mirror.query` | user SQL; `sqlite_schema` for the object list | any | n/a |
| engine freshness / publish | `ledger_engine.mirror.store` | `SELECT key, value FROM m_meta LIMIT 65` | 9 rows | scan |
| build | `ledger_engine.mirror.build` | DDL and inserts only | | |

Callers: `Reader` and `QueryRunner` have no caller in product code (`grep` over `backend/src`: only the mirror package itself; the tests and `backend/scripts/measure_mirror_reads.py` use them). Their first route is BL-0126 (M2); user SQL is CAP-ENGINE-SQLITE (M7). No product code reads `m_postings` or any view over it (`r_postings`, `postings`). Counts: 5 read sites, 0 of them on a table larger than the load's error list.

### Measurements (54k, current head)
`backend/scripts/gen_demo.py --size 54k --anchor 2026-10-28` (54,018 entries, 108,241 postings); built with `engine/scripts/measure_mirror_build.py` (load 1.4 s, build 1.1 s, file 77.4 MB; was 82.8 MB in S0052); `backend/scripts/measure_mirror_reads.py <mirror> --runs 10 --indexes`; macOS, warm cache, medians, SQLite 3.53.1.

| Query | No index | `(account, transaction_date)` + `(transaction_id)` |
|---|---|---|
| `Reader.info` | 0.21 ms | n/a |
| `Reader.ledger_errors(200)` | 0.21 ms | n/a |
| one account's year (user SQL example) | 15.3 ms | 2.6 ms |
| one transaction by `transaction_id` (user SQL example; the shape of M1b's resolver) | 11.9 ms | 0.30 ms |
| monthly expenses | 30.0 ms | 30.1 ms |
| every posting, capped at 10,000 rows | 31.7 ms | 31.7 ms |
| cost | | +91 ms build, +12.7 MB file (77.4 → 90.1 MB) |

The worst case for the only real table read, `ledger_errors`: a copy of the mirror with 54,018 error rows (one per entry) answers `COUNT(*)` plus the first 200 in 2.5 ms without an index, 0.09 ms with one on `id` (5 ms to build). Not a requirement at any plausible error count.

### Verdict
No read that exists at M1a needs an index: both measured gains are on queries no product code runs today (user SQL with no route until M2/M7). The one named consumer is the TransactionId lookup of M1b's write planner (BL-0059, ARCHITECTURE.md §9.2 step 2), which would gain about 11.6 ms a lookup for +91 ms of build and +12.7 MB of file (both indexes; the `transaction_id` index alone is smaller, to be measured then). Building them now is speculative; no index is built and no test is written. Proposal for the owner: move BL-0122 to M1b and choose its index with the resolver (BL-0059), measured with the resolver's own query (non-unique `(transaction_id)`, or `(transaction_id, transaction_content_hash)` if the resolver filters on the hash in SQL).

### Found on the way
- `engine/scripts/measure_mirror_build.py` no longer runs: `Stamp` gained the required `published_by` in S0088 (c600d538) and the script's `STAMP` was not updated (`TypeError: Stamp.__init__() missing 1 required positional argument: 'published_by'`). The numbers above used a scratch copy with `published_by="open"`. No test runs the measurement scripts, so nothing caught it. Proposed: a one-line fix and a smoke test that imports and runs it on a tiny ledger (tooling; needs a backlog ID or a slice from the orchestrator).
- The DDL snapshot has no `CREATE INDEX` (`contracts/mirror-ddl.sql`: 0), consistent with S0096's checksums; nothing changes there.

## Decisions
- Stop after the brief, build no index (agent, under the orchestrator's instruction for this case): no M1a read needs one, measured above.
- The milestone move is not made here (orchestrator: "do not edit the row's milestone yourself").
- **Owner's answer** (Owner, 2026-11-10, in session (answer to the orchestrator's question): "Move to M1b (Recommended)"): option 1. BL-0122 moved to M1b with the TransactionId lookup (BL-0059); nothing built in this slice. Inbox file moved to `design/inbox/answered/`.

## Verifier
- None: no code, check or test changed (a docs-only park and close; the commits are the brief, the evidence and the row move).

## Retro
- Build about 3 min (inventory and measurement), fix 0 min, 0 verifier rounds, findings 0H/0M/0L. Cheaper gate: a backlog row whose own text says "chosen when the first read that needs it is built" should carry that read's milestone from the start; the M1a/M1b mismatch was visible in the row.
