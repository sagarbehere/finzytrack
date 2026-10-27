# Specs

One folder or file per area: intent, decisions (linking ADRs, not restating them) and open questions. Behaviour lives in tests. Each spec opens with an **Orientation** block of 10–20 lines: what exists, entry points, the invariants that bind it, known traps, the main tests.

Specs are written just in time (review:F §3.12): M0 writes only M1's specs (the D1 write corpus, the per-mutator intent table, the mirror surface and DDL, the INV-21 goldens, the Beancount vectors). Every other area's spec is written in the first slice of its vertical.

Planned areas, in vertical order: `write-path/`, `mirror/`, `workspaces/`, `shell/`, `accounts/`, `transactions/`, `importing/`, `dashboards/`, `investments/`, `assistant/`, `settings/`, `statecharts.md` (the target machine catalogue, ARCHITECTURE.md §7).

Written so far: [`roadmap.md`](roadmap.md) (the milestone order and where we are, S0007); [`write-path/`](write-path/README.md) (the per-mutator intended-change table, S0004; the D1 write corpus, S0020); [`mirror/`](mirror/README.md) (the mirror surface: tables, columns, `r_*` views and the v0.2.2 compatibility names, S0021; the INV-21 goldens, [`mirror/inv21-goldens.md`](mirror/inv21-goldens.md), S0022); `kernel/` (the Beancount domain vectors, [`kernel/vectors.md`](kernel/vectors.md), S0024); [`failure-modes.md`](failure-modes.md) (what each failure of the shell, the webview, the main sidecar and the ledger engine does to the others, to data and to the user, with each row's test, S0041); [`engine-jobs.md`](engine-jobs.md), "Engine jobs and saves" (one engine doing one job at a time: the queue, Cancel, the save's durable order, R4, recovery and the switch; draft 5, ADR-0064; the owner ended its review loop early and its TLA+ model, `formal/tla/engine-jobs/`, is under review, BL-0110).

**Renamed:** `engine-scheduler.md` (the engine-scheduler spec) is `engine-jobs.md` since S0047 (owner, 2026-10-27); journals, answered inbox files and ADR prose still use the old name; the model's folder followed in BL-0111 (`formal/tla/engine-jobs/`).
