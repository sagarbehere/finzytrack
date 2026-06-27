"""Server-side compute registry — a general financial-computation engine.

`/api/compute` is the peer of `/api/ledger/query`: name-addressable functions
that may read the ledger (read-only) and return arbitrary JSON. The first
function is `budget_for_range` (the Fava-style budget resolver). See
dev-docs/refactored-dashboard-recipes.md §3.2/§4.6 and dev-docs/budget.md §6.
"""
