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

## Verifier
- Round 1, head c232423: PASS WITH FINDINGS. Attacks: 11 (class attributes from unknown calls; loops, match, `except*`; walrus and expression statements; memoizer aliases; `tuple([[]])`; a container-owned registry entry; mutants; marker coverage; gates).
- F1 Medium: class bodies used a denylist. Fixed: the same immutable allowlist as module level, plus `field(...)`.
- F2 Medium: `for`/`while`/`match`/`except*` blocks and module-level expressions were not visited. Fixed: every nested block; any non-constant expression statement and loop variable counts.
- F3 Low: memoizers matched by spelling. Fixed: resolved through import aliases; any `lru_cache`/`cache`/`cached`/`memoize` name or `cachetools` decorator.
- F4 Low: `tuple(...)` passed whatever it held. Fixed: elements must be immutable.
- F5 Medium: container-owned parts (§2.5) could never be registered. Fixed: `process` entries are module-level names validated by the scan (at most one, ADR-0041); container entries name the class or factory and are validated as defined names. The docstring and INV-11's line say so.
- F6 Low: markers sat on a helper. Fixed; every test carries INV-11. F7 Low: `with` and `except` branches had no planted case. Added.
- Also: duplicate entries and unknown owners are refused; the bad-entry test asserts the specific problem.
- Three Medium on an architecture gate with no ledger or user data: no further round (ADR-0058).

## Retro
- The brief's loosening list was right in spirit and too narrow in reach (it named literals; the verifier used calls and other blocks). Writing the list per AST node kind, not per example, is the cheaper way to make it complete.
- Process cost: about 40 min building; verifier 2.5 min; fixes about 25 min.
