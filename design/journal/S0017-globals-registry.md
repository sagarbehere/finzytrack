# S0017: INV-11's globals test and the state registry (BL-0011)

## Brief
- Goal: enforce INV-11 (and EP-04's "no module-level mutable state elsewhere") in the main sidecar: `app.platform.state_registry` lists every stateful module-level name with owner, bound and eviction; an architecture test scans every module's source and fails on unregistered module-level or class-level mutable state, `global`/`nonlocal`, or memoizing decorators, and on stale or incomplete registry entries.
- Implements: INV-11, EP-04, ARCHITECTURE.md §2.5 ("the globals-allowlist test reads this list from `platform/state_registry.py`, where each item declares its bound and its eviction"); BL-0011. The isolation matrix stays planned (L11-08, M2).
- Capabilities: none. Invariants touched: INV-11 (pending → enforced).
- Risk class / lane: Medium / full (an architecture gate; no ledger or user data).
- How the check could be loosened or bypassed (written before coding): 1. a mutable value built by a call (`defaultdict(list)`, `make_client()`) instead of a literal → only calls on a short immutable allowlist pass; 2. state in a class attribute; 3. state inside a module-level `if`/`try`/`with`; 4. `global`/`nonlocal` writes from functions; 5. `functools.lru_cache`/`cache` (unbounded hidden state); 6. augmented assignment at module level; 7. a tuple holding a list; 8. registering by module only (a whole module exempted) → entries are (module, name); 9. stale registry entries kept after the state is gone; 10. an entry without bound or eviction. Stated limits: an alias of stdlib state (`_env = os.environ`) passes, as names and subscripts are read as aliases or type expressions; state created inside functions and stored on objects is the containers' business (INV-11's isolation matrix, M2).
- Acceptance tests: `backend/tests/architecture/test_globals.py` (tree clean; 20 planted states found; 12 immutable shapes pass; registered vs unregistered; four bad registry entries).
- Expected doc impact: INVARIANTS.md INV-11 (enforced, files listed); backlog BL-0011 done; roadmap §1; this journal. Also `pyproject.toml` registers the `inv`/`cap` pytest markers (PROCESS.md §7 tags) and `ruff.toml` names `app` first-party so the hook and CI sort imports alike.
- Open questions: none.
- Base SHA: 0a27345.

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: every item of the loosening list above has a planted case.
- Self-mutation: 8 tried, 8 killed.
