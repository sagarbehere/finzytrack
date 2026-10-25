# Specs

One folder or file per area: intent, decisions (linking ADRs, not restating them) and open questions. Behaviour lives in tests. Each spec opens with an **Orientation** block of 10–20 lines: what exists, entry points, the invariants that bind it, known traps, the main tests.

Specs are written just in time (review:F §3.12): M0 writes only M1's specs (the D1 write corpus, the per-mutator intent table, the mirror surface and DDL, the INV-21 goldens, the Beancount vectors). Every other area's spec is written in the first slice of its vertical.

Planned areas, in vertical order: `write-path/`, `mirror/`, `workspaces/`, `shell/`, `accounts/`, `transactions/`, `importing/`, `dashboards/`, `investments/`, `assistant/`, `settings/`, `statecharts.md` (the target machine catalogue, ARCHITECTURE.md §7).

Written so far: [`roadmap.md`](roadmap.md) (the milestone order and where we are, S0007); [`write-path/`](write-path/README.md) (the per-mutator intended-change table, S0004).
