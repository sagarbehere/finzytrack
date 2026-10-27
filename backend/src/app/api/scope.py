"""The scope declaration every operation carries (ADR-0038: `x-ft-scope`, `x-ft-writes`; ADR-0018).

`x-ft-scope` says what the operation reads: `app` (app-level state; never under `/api/w/{ws}/`), or, for a workspace
route, `config` (the workspace's tool state) or `ledger` (the ledger, through the mirror or the engine). `x-ft-writes`
says what a successful call changes: `ledger`, `config` or `none`; the frontend's query keys and invalidation are
generated from both (ARCHITECTURE.md §4.1, BL-0107).
"""

from __future__ import annotations

from typing import Literal

type Scope = Literal["app", "config", "ledger"]
type Writes = Literal["ledger", "config", "none"]

SCOPES = frozenset({"app", "config", "ledger"})  # the values of `Scope` (a test holds them equal)
WRITES = frozenset({"ledger", "config", "none"})  # the values of `Writes`


class LedgerWriteFromAppScope(ValueError):
    """Only a workspace route can write a ledger: the ledger belongs to the workspace named in the path."""


def declare(scope: Scope, writes: Writes = "none") -> dict[str, str]:
    """The `openapi_extra` of a route: `@router.get(path, openapi_extra=declare("ledger"))`."""
    if scope == "app" and writes == "ledger":
        raise LedgerWriteFromAppScope(writes)
    return {"x-ft-scope": scope, "x-ft-writes": writes}
