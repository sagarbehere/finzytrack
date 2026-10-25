# Engineering principles (EP-nn)

> **Frontend framework (2026-10-25): Vue 3, per ADR-0053** (spike S13). Where this document describes the React target, read the Vue equivalents: hooks → composables, `.tsx` → `.vue`, TanStack Vue Query/Table/Virtual, shadcn-vue on Reka UI, `@xstate/vue`, `@ai-sdk/vue`, `eslint-plugin-vue` + `eslint-plugin-vuejs-accessibility`, `vue-tsc`; pins in ARCHITECTURE.md §10 (Vue fallback rows).

This file holds the engineering principles that shape Finzytrack v0.4's code, how each is enforced, and the verifier checklist.

- **Status:** current. Migrated on 2026-10-24 from the review's engineering-principles document (review:26), with the final review's corrections applied (review:F §3.11): size thresholds are report-only (EP-02); the account grammar and tolerance live in `ledger` behind the port (EP-03); concept counters are bans with one allowed home (EP-23); a test-design principle is added (EP-28); findings cite one of the listed ID forms (§3).
- **Owner:** the process. Agents propose changes; the owner approves (§4). EP IDs are stable and never renumbered.
- **Read with:** `design/INVARIANTS.md` (the "musts"), `design/ARCHITECTURE.md` (the system these principles shape), `design/process/PROCESS.md` (how slices, gates and reviews work) and `design/decisions/` (ADRs).
- **Frontend examples** use React, the target stack (ADR-0006, pending S13). If S13 keeps Vue, the Vue equivalents named next to each check apply.

## 0. Invariants versus principles

| | Invariants (`INV-nn`) | Principles (`EP-nn`) |
|---|---|---|
| Strength | **Must.** A violation is a defect, and the change does not merge. | **Should.** The default, and the verifier holds code to it. |
| Owner | The owner (`design/process/PROCESS.md`: adding or tightening is batched for approval; weakening always needs the owner) | The process. Agents propose changes and the owner approves (§4) |
| Lives in | `design/INVARIANTS.md` | `design/ENGINEERING.md` (this file) |
| Deviation | Not allowed. Changing one needs an ADR + owner approval | **Allowed with a recorded reason:** a `Deviates: EP-nn — <reason>` line in the slice record, or an ADR when the deviation is lasting or crosses slices. The verifier judges whether the reason holds. An unrecorded deviation is a finding |
| Enforcement | Every INV has at least one check (`scripts/check_invariants.py`) | Automated where possible (listed per EP); otherwise a verifier checklist item `[EP-nn]` |

Several principles are the *engineering means* of an invariant. When that holds, the EP names the INV, and a violation that breaks the invariant is reported against the INV, not the EP. (Invariant IDs are those of `design/INVARIANTS.md`: INV-01–31, with the workspace invariants numbered INV-28–31 per ADR-0003.)

**Automated checks referenced below.** Tiers are those of `PROCESS.md` (gates):

| Check | Tool / file | Tier |
|---|---|---|
| Type gates | pyright strict (`backend/pyrightconfig.json`); `tsc --noEmit` with the strict `tsconfig.json` (`vue-tsc` if spike S13 keeps Vue) | T0 pre-push (`PROCESS.md` (gates)) |
| Lint / bans | ruff (`backend/ruff.toml`), ESLint (`frontend/eslint.config.ts`, with eslint-plugin-react-hooks strict rules incl. the React Compiler rules, as errors) | T0 |
| Licences | licence check of the Python, JS and Rust dependency trees against an allowlist; `THIRD_PARTY_NOTICES` regenerated (owner, 2026-10-24; ADR-0042) | T1 |
| Layering | import-linter (`backend/.importlinter`); eslint-plugin-boundaries | T1 (T0 for changed files) |
| Architecture tests | `backend/tests/architecture/` (AST bans, globals/state registry, route scope, TLA mapping, error codes, events) | T1 |
| Generation diff | `.githooks/pre-commit` + CI regeneration, then `git diff --exit-code` | T0 + T1 |
| Concept counters, duplication | `scripts/concept_counters.py`, jscpd | T1 |
| Models | Hypothesis state machine, XState model-based paths, TLC small config | T1; large configs T2 |
| Mutation | mutmut, Stryker | T2, weekly and before each clearance (`PROCESS.md` (gates)) |

---

## 1. Structure

### EP-01 Functional core, imperative shell
**Rule.** Logic that decides is pure: functions and frozen values over plain data, in a package's `core/` (or `domain/`). The shell around it (use cases, HTTP, adapters) does I/O and calls the core; it decides as little as possible.
**Why.** Pure code is testable with no setup, safe to property-test and easy to reason about for money semantics. The 2026-10 review found most defects where logic and I/O were mixed (routers holding business rules, L1-06; god-components, L8).

```python
# bad: the decision hides inside I/O
def commit_import(ws, rows):
    for r in rows:
        if not db.exists(r.key):            # I/O
            ledger.append(make_txn(r))      # I/O mid-decision
# good: pure planning, then one shell step
def plan_commit(rows: Sequence[DraftRow], existing: KeySet) -> CommitPlan: ...   # importing/core
def commit_import(c: WorkspaceContainer, session_id: SessionId) -> Committed | Refused:
    plan = plan_commit(load_rows(c, session_id), c.mirror.import_keys())
    return c.ledger.commit(plan.edit_plan)
```

**Enforcement.**
- import-linter "purity" contract: `*.core` and `domain` cannot import fastapi, pydantic, sqlite3, httpx, portalocker, os, pathlib, subprocess, socket, time, random, secrets, uuid, beancount or `platform.adapters`.
- ESLint boundaries: `src/domain` imports only `domain`, decimal.js and temporal-polyfill.
- AST test: no builtin `open`/`print` in core.
- Checklist `[EP-01]`: is any branching on business state left in a use case or a view?

### EP-02 Deep modules
**Rule.** Prefer a few modules, each with a **small public interface** over a cohesive implementation (Ousterhout). Don't split a concept into many tiny public functions or one-method classes. A module's public names are in `__all__` / `index.ts`, and everything else is private.
**Why.** Small interfaces are what make "one concept, one place" hold (EP-23) and keep the slice verifier's neighbourhood small. A mass of tiny functions spreads one decision over many call sites.

```python
# bad: callers must compose six steps correctly every time
locate(...); check_hash(...); splice(...); fix_newlines(...); route(...); reparse(...)
# good: one entry point; the steps are private
def apply_edits(text: str, edits: Sequence[Edit], newline: Newline) -> str: ...
```

**Enforcement.**
- Lint: `__all__` is required in every `core` module (ruff `PLE0604` + an AST check); ESLint `import/no-internal-modules` for `features/*/` (only `index.ts`).
- Size thresholds (`PROCESS.md` (structural checks)) are **report-only smells** (CI job summary), never a CI failure.
- Metric: the public surface of each module, i.e. the count of names in `__all__` / `index.ts`, reported per area and watched in area reviews.
- Checklist `[EP-02]`: does a new public function have more than one caller-meaningful reason to exist? Could it be private?

### EP-03 Beancount is the ledger-semantics core, wrapped, never re-implemented
**Rule.** Booking, interpolation, tolerance, validation, account grammar, the price map and the printer come from Beancount, called through `app.ledger.beancount_port`. Only that module imports `beancount`. **The account grammar (`parse_account`) and `Tolerance` construction live in `ledger` behind the port;** `domain.AccountName` and `domain.Tolerance` are brands/value types whose only constructors are in `ledger`, so `domain` stays Beancount-free and INV-02 holds as written. The frontend uses vector-pinned ports for live UX only. *(The engineering means of INV-02 and INV-03.)*
**Why.** The review found six balance checks with three tolerance models and an account-name validator that contradicted Beancount (L3-04, L3-09). Every re-implementation drifts.

```python
# bad
if abs(sum(p.units.number for p in txn.postings)) > Decimal("0.005"): ...
# good
residual = beancount_port.compute_residual(txn.postings)
if not beancount_port.is_within_tolerance(residual, tolerances): ...
```

**Enforcement.**
- import-linter: only `ledger.beancount_port` imports `beancount`.
- AST test: the load rule of ARCHITECTURE §2.4 item 4 (full loads only from `ledger/overlay.py` and the reconciliation job; parser-only calls only via `beancount_port`).
- Concept counters for tolerance, balance-check and account-grammar implementations (count = 1).
- Beancount-generated vectors on both sides (`gen_domain_vectors.py`).

### EP-04 Stateful parts are explicit and small
**Rule.** State that outlives a request lives only in the declared places (ARCHITECTURE §2.5). Each one is registered with its owner, bound and eviction. Workflows with real states are statecharts (EP-17). There is no module-level mutable state anywhere else, and no ledger stays resident. *(The engineering means of INV-10, INV-11 and INV-31.)*
**Why.** Hidden state caused I6, L11-04 (a global "last previewed recipe"), the stale-widget bugs and the hosted-tenancy breaks.

```python
# bad
_last_preview: dict[str, Recipe] = {}          # module global, unbounded, cross-tenant
# good
@register_state(owner="UserContainer", bound=Bound(ttl=timedelta(hours=1), max_items=200))
class PlanStore: ...
```

**Enforcement.**
- Globals test: AST scan for module-level mutable containers and `global` writes, compared against `platform/state_registry.py`.
- Cache-registry test (every cache declares a bound and an eviction).
- 54k memory regression test (T2).
- ESLint `no-restricted-syntax` for module-level `ref`/`reactive` outside `catalog/` and the stores that are explicitly listed.

### EP-05 Extensibility only where variation is real
**Rule.** Plug-in points exist only for: importers and statement parsers, price sources, recipe catalogs (functions, transforms, viz, `ledger` measures), AI capabilities, LLM provider profiles, migrations and log events. Each is a **self-describing registry**: every entry declares an id, a version, an args schema, its input and output shapes and a description, so the docs, the AI guide and a future GUI builder are generated from it (ADR-0029). Everywhere else, no speculative interfaces, factories or strategy objects.
**Why.** Real variation needs a stable seam; imagined variation is indirection that someone pays for later. The self-description is what makes the "users author dashboards" invariant (INV-08) work for people and for the AI.

```python
# bad: an abstract base class for something with one implementation
class AbstractTransactionRepositoryFactory(ABC): ...
# good: a registry where users and agents really add entries
@price_sources.register(id="amfi", version=1, args=AmfiArgs, description="Indian mutual fund NAVs from AMFI")
def amfi(args: AmfiArgs) -> PriceSourceSpec: ...
```

**Enforcement.**
- Registry test: every entry has a complete descriptor, its args schema rejects unknown keys (L5-08), and the generated catalog docs match (generation diff).
- Checklist `[EP-05]`: a new Protocol/ABC with one implementation needs a named second variation, or it is a finding.

### EP-06 Parse, don't validate, at every edge
**Rule.** Data enters through one parser per edge that returns domain types or a located error: HTTP (Pydantic schemas → domain DTOs), files (ledger via Beancount; settings, rules, manifests and recipes via their models), bank exports (L4 adapters), LLM output (structured outputs), localStorage, route params and shell messages (zod on the frontend), API responses (generated decoders). Inside, functions take only parsed types and never re-check. Pydantic stays at the edges and never appears in a `core` signature.
**Why.** "Valid" checks scattered inside lose to the one path that forgets them. The import parsers silently mis-signed amounts and accepted `2026-02-31` (L3-11).

```python
# bad
def book(amount: str, account: str): if not ACCOUNT_RE.match(account): raise ...
# good
def book(amount: Amount, account: AccountName) -> Edit: ...   # cannot be called with raw text
```

**Enforcement.**
- import-linter: `core` and `domain` cannot import pydantic.
- pyright: `NewType`/brand constructors are private to their parser modules (ESLint `no-restricted-syntax` on `as Money` / `as AccountName` outside `domain/` and `api/decode.gen.ts`; ruff `TID251` bans direct `AccountName(...)` outside `domain.accounts`).
- Checklist `[EP-06]`: does any core function accept `str`/`dict` where a domain type exists?

### EP-07 Ports and adapters for the shell
**Rule.** Every outside effect goes through an injected port (ARCHITECTURE §2.3): file system, locks, ledger store, mirror, clock, ids, randomness, network, IMAP, LLM, secrets and the Tauri shell (`ShellPort`). Adapters live in `platform/adapters` (frontend: `platform/shell/*`) and are wired only in the composition root.
**Why.** Injection is what makes crash injection, recorded providers, the frozen clock and the isolation matrix possible. It is also the seam that keeps hosted mode open (L11).

```python
# bad
def fetch_nav(isin): return requests.get(f"https://amfi…/{isin}").text
# good
def refresh_prices(c: WorkspaceContainer, http: Http, clock: Clock) -> RefreshReport: ...
```

**Enforcement.**
- import-linter: only `platform.adapters` imports httpx, portalocker, imaplib, ssl, pydantic_ai; only `app.main` imports `platform.adapters`.
- ESLint: only `platform/shell/tauri.ts` imports `@tauri-apps/*`; only `src/api` calls `fetch`.

### EP-08 Plans as data
**Rule.** The core returns *what to do*: `EditPlan`s, `WritePlan`s, drafts, lifecycle plans, export/import plans, report-bundle plans, HTTP request specs for price sources. The shell executes them. A plan can be previewed, hashed, stored, confirmed and tested without executing it. (This is D1 generalized.)
**Why.** Previews, confirmations (INV-16), AI proposals (INV-13), dry-run migrations (INV-17) and "show every file the setup will write" (L10-02) all fall out of one shape, and tests can assert on the plan instead of on disk.

```python
# bad: setup writes files as it goes
def setup_fresh(root): write(root/"main.beancount", TEMPLATE); write(...)
# good
def plan_create_workspace(req: CreateRequest, probe: Probe) -> LifecyclePlan | Refused: ...  # pure; lists every file
execute(plan, consent, fs, journal)                                                          # shell
```

**Enforcement.**
- Architecture test: every mutating HTTP route has a matching `:preview` or is declared `x-ft-plan: inline` (with a reason).
- Use cases that write take a plan type (pyright).
- Checklist `[EP-08]`: does any shell function compute a decision that a plan should have carried?

### EP-09 Command-query separation: reads from the mirror, writes through one path
**Rule.** Queries read the mirror (or `QueryRunner`) and never load the ledger, except BQL, which is a declared transient load (ADR-0011). Commands produce plans and commit only through `ledger.writer` under the contract. A query never writes, and a command returns ids, versions and results, not view models. *(The engineering means of INV-01 and INV-05.)*
**Why.** One write path is what makes the write-safety contract (ADR-0008) total. One read path is what keeps memory frugal and freshness consistent (five raw SQL runners existed, L1-10).

**Enforcement.**
- AST test: `atomic_replace` is reachable only from `ledger.writer`, `boot` execute and settings/rule stores (each through `FileSystem`).
- Concept counter: SQL runners = 1.
- Route-scope test: GET routes declare `x-ft-writes: none`.

### EP-10 Determinism
**Rule.** Time, ids and randomness are injected (`Clock`, `Ids`, `Rng`). "Today" is always `today(tz, clock)` (INV-07). Output order is defined (sorted keys, stable sort keys). The demo generator is seeded per fragment name and anchored at `SOURCE_DATE_EPOCH` (ADR-0022).
**Why.** Golden tests, model-based tests, the oracle comparison and reproducible builds all need it. Twelve UTC "today" bugs came from ad-hoc clocks (L3-10).

```python
# bad
created = datetime.now(); tid = str(uuid4())
# good
created = clock.now(); pid = ids.new_plan_id()   # TransactionIds are derived, not generated (ADR-0026)
```

**Enforcement.**
- ruff `TID251` banned APIs outside `platform/adapters`: `datetime.datetime.now`, `datetime.date.today`, `time.time`, `uuid.uuid4`, `random.*`, `secrets.*`; plus ruff `DTZ`.
- ESLint `no-restricted-syntax` / `no-restricted-properties`: `new Date()`, `Date.now`, `Math.random` and `crypto.randomUUID` outside `platform/`.
- Acceptance runs with `TZ` west of UTC and a frozen clock on both sides.

### EP-11 Vertical slices over a shared kernel, dependency direction enforced
**Rule.** Each capability is one slice (ARCHITECTURE §2.2, §3.1) with its own core/usecases/http/capabilities (backend) or queries/machines/hooks/views (frontend). Slices depend on the kernel, never on each other except through declared, ADR-referenced edges. The kernel never depends on slices.
**Why.** The rebuild grows one vertical at a time (ADR-0002). Slices keep area reviews bounded (`PROCESS.md` (structural checks)) and stop the package-level cycles the old backend had (`core ↔ services ↔ schemas ↔ ai`).

**Enforcement.**
- import-linter layers, per-slice layers and independence contracts (ARCHITECTURE §2.4).
- eslint-plugin-boundaries element rules.
- Generated package diagram, diffed in review.

### EP-12 Idiomatic code; no heavy FP libraries
**Rule.** Write plain, idiomatic Python and TypeScript: dataclasses, `match`, comprehensions, `Protocol`s; in TS, discriminated unions, `switch` with `never` checks, hooks and plain function components (no class components). Do not add `returns`, `toolz`, fp-ts, Effect or similar. Functional style means pure functions and immutable values, not a library.
**Why.** The code must stay readable by any contributor and by agents with a typical prior. A second programming model inside the language costs more than it returns here.

**Enforcement.**
- Dependency denylist checked in CI (`deptry` / a pnpm `overrides` check): `returns`, `toolz`, `fp-ts`, `effect`, `purify-ts`, `neverthrow`.
- Checklist `[EP-12]`.

### EP-13 Errors: typed, translated once, never swallowed; expected outcomes are tagged unions
**Rule.**
- **Taxonomy.** `Invalid` (with located issues), `NotFound`, `Conflict`, `ContractRefused`, `Forbidden`, `Unavailable`, `Internal` (ARCHITECTURE §6.1). Every error carries a stable code from the one registry. Expected outcomes the caller must handle are returned as tagged unions, with exhaustive `match` / `switch`.
- **Translation once, at the edge,** into the one envelope: code, plain-language message, located issues, retryable flag, correlation id. Codes, presentation and client messages are generated into TS.
- **No string matching on messages anywhere.**
- **No swallowing.** `except Exception` only at the shell boundary (API handler, job runner, sidecar loop), where the error is logged with the correlation id and becomes `INTERNAL`. Safety checks fail closed (INV-22).
- **One frontend path:** `AppError` from the client → `presentError` (D11 toast/modal, `useFieldErrors` inline, Retry) → `QueryState` for query errors → explicit error states in statecharts.

**Why.** L1-05 found three backend channels, routers string-matching messages and domain errors swallowed into 500s. F3 found seven frontend patterns.

```python
# bad
except ValueError as e:
    if "not found" in str(e): raise HTTPException(404)
# good
match accounts.close(c, req):
    case Closed(version=v): return ok(v)
    case NotOpenAt(date=d): raise Invalid(Code.ACCOUNT_NOT_OPEN_AT, issues=[Issue("/date", ...)])
```

```ts
// bad
catch (e: any) { console.error(e); toast(e.body?.detail ?? 'Error') }
// good
const m = useMutation({ mutationFn: closeAccount })          // errors reach presentError via MutationCache
```

**Enforcement.**
- ruff `BLE001`, `S110`, `TRY`, plus an AST allowlist for `except Exception`.
- AST ban on `in str(e)` / `e.args[0] ==` comparisons.
- pyright `reportMatchNotExhaustive`; TS `switch-exhaustiveness-check`.
- Error-code coverage test (every registry code triggered by a test tagged with it; unused codes fail).
- ESLint: `no-console` outside the logger, no access to `.body`/`.json()` on errors outside `src/api`, `no-empty`, `@typescript-eslint/only-throw-error`, `no-floating-promises`.
- Acceptance flows per error class (ARCHITECTURE §6.7).

### EP-14 Versioned contracts
**Rule.** Every contract that outlives one release carries a version and a compatibility rule:
- the HTTP API (`contracts/openapi.json`, `oasdiff` breaking-change check);
- the mirror's `r_*` views (`meta.views_version`, golden);
- the recipe schema (`schemaVersion`, migrations);
- the workspace and app manifests (`format`, `min_reader`, INV-17);
- rule files;
- the chat stream (`sdk_version`);
- the log event schema;
- the ledger metadata keys and id scheme (INV-21: keys a released version wrote and a reader needs are read forever; other keys pass through untouched; only the consented cleanup removes or renames a key (per 28, owner 2026-10-24)).

**Why.** Users' recipes, outside tools on the mirror and folders opened on other machines all depend on these, and the review's own changes force the first format bumps (L10-08).

**Enforcement.** OpenAPI break check, view-contract golden, schema and manifest JSON-Schema snapshots in `contracts/` with a diff check, old-format fixtures in `corpora/` read by every reader (T1).

### EP-15 Observability
**Rule.**
- Structured JSON logs through registered events (ARCHITECTURE §6A). Each line carries the session id, correlation id, workspace id, feature slice and use case.
- Every port call in an adapter is timed (`@timed(port, op)` → `duration_ms`, warning over its slow threshold).
- The frontend logs into the same stream, and global error handlers route uncaught errors to the log and to the user.

**Why.** A user's report must be analysable from the log alone. v0.3 had cwd-relative text logs, f-strings with no correlation ids, and frontend errors that never left the console.

**Enforcement.**
- AST test: every public method of a `platform.adapters` class is `@timed`.
- ruff `T20` (no print), `G004` (no f-string log calls); only the registered-event logger is importable outside `platform.observability`.
- ESLint `no-console` outside `platform/logger.ts`.
- Playwright: an uncaught UI error appears in the log with its correlation id.

### EP-16 Schemas are the single source; everything else is generated, never hand-edited
**Rule.** Each shared shape has one source, and every other form is generated:
- backend Pydantic edge models → OpenAPI (exported **offline** by importing the app, no running server) → TS types, decoders and query keys;
- the error and event registries → TS;
- the Draft contract → TS;
- the mirror DDL with column comments → mirror docs;
- the manifest models → JSON Schema;
- `contracts/recipe.schema.json` → TS types, enum consts, the Python validator's enum sets and the docs.

Generated files carry a header and are never hand-edited. Generated code keeps domain types (EP-21) through schema annotations (`x-ft-type`). *(The engineering means of INV-19 and S2.)*
**Why.** Hand-copied mirrors drifted every time (three recipe validators, L5-03; FE regex copies, L3-17). The old "restart the backend, then regenerate" step let stale clients ship.

**Enforcement.** The pre-commit hook regenerates when a source is staged and blocks on a diff; CI regenerates from a clean checkout and fails on `git diff`; a header check refuses edits to generated files (a T0 check comparing each generated file's content hash with its header).

### EP-17 Statecharts for stateful workflows
**Rule.** A workflow with real states is a statechart:
- **frontend:** an XState v5 machine (used via `useMachine` from `@xstate/react`), covered by model-based tests (`@xstate/graph` paths) that hit 100% of states and transitions;
- **backend:** a pure `transition(state, event) -> (state, effects)` over an explicit table, with an exhaustive (state × event) test and Hypothesis random walks.

Required for: boot, workspace open/switch, the import session, the edit buffer and save, the AI proposal, the updater, the sidecar and lifecycle runs (ARCHITECTURE §7). Where the backend is authoritative, the frontend machine models only UI states over the backend state.
**Why.** The worst UI integrity bugs were implicit state machines: edits lost on re-query (L8-01), edits during save never sent (L8-02), undo resurrecting changes (M-1), the wizard-vs-boot divergence (L10).

```ts
// bad: booleans that can contradict each other
const saving = ref(false), dirty = ref(false), conflict = ref(false)
// good
const editBuffer = setup({...}).createMachine({ initial: 'clean', states: { clean: {...}, dirty: {...}, saving: {...}, conflict: {...} } })
```

**Enforcement.**
- T1 model-coverage report per machine (must be 100%).
- Exhaustive-table tests for backend machines.
- Checklist `[EP-17]`: are there two or more booleans describing one workflow's phase?

### EP-18 A property-based stateful model of the write core
**Rule.** `ledger.contract`, `ledger.writer`, `mirror.publish` and recovery are tested by a Hypothesis `RuleBasedStateMachine` against a reference model, with edits, external edits, legacy writes, injected crashes at every file-system call, and restarts (ARCHITECTURE §8.2). Any change to these modules keeps the model green and extends it when it adds a behaviour.
**Why.** Priority #1 is no corruption and no loss (ADR-0008). Example tests cannot cover the interleavings and crash points that matter.

**Enforcement.** The T1 bounded run (fixed example budget and seed database committed), the T2 long run, and a mutation score floor on `app/ledger` (ADR-0043, gate item 7).

### EP-19 A small TLA+ spec of the concurrency and crash protocol
**Rule.** `formal/tla/WriteProtocol.tla` models the locks, the atomic write sequence, the write-intent journal, mirror generation publishing, the legacy-app lock and the external-edit fingerprint (ARCHITECTURE §8.3). Each spec action maps to exactly one code function, marked `@tla_action`. A change to the protocol changes the spec in the same slice.
**Why.** Lock ordering, crash windows and cross-process interleavings are where reasoning fails and tests rarely reach. The spec work already surfaced a gap (multi-file commit atomicity), closed by the write-intent journal (ADR-0012).

**Enforcement.** TLC on the small config in T1 and the large config in T2, plus the mapping test (spec actions ↔ markers, both directions).

### EP-20 Diagrams are generated from models and code
**Rule.** Package diagrams come from the import-linter contracts plus the real import graph; statechart diagrams from XState machines and backend tables; the API reference from OpenAPI; the mirror docs from the DDL; the TLA+ mapping from the markers. Nobody hand-draws an architecture diagram that claims to describe the code.
**Why.** Hand-drawn diagrams drift silently, as the 2026-05 dev-docs did.

**Enforcement.** `scripts/gen_diagrams.py` in T1 with a generation diff on `design/generated/`; the doc auditor flags hand-written diagrams that describe code structure.

### EP-21 Domain types; brand where confusion is costly
**Rule.**
- Use `NewType` / branded types and frozen value objects for the concepts in ARCHITECTURE §2.6: `Amount`, `Money`, `Commodity`, `Currency` (a Profile-assigned role), `AccountName`, `CivilDate` (`date` / `Temporal.PlainDate`), `WorkspaceKey` vs `LineageId`, `TransactionId`, `CanonicalEntryKey`, `ErrorKey`, `LedgerVersion`, `PlanId`, `PreviewHash`, `WorkspacePath`, `AppPath`, `UntrustedText` vs `SafeHtml`, `UserSql`/`UserBql` (internal SQL is `LiteralString`).
- Tagged unions make illegal states unrepresentable.
- Shared types are generated from schemas (EP-16).
- **Do not brand every string:** add a brand only when mixing two values up would be costly (wrong money, wrong account, wrong workspace, markup from data, a path escape, user SQL), and say which confusion it prevents.

**Why.** Each type above corresponds to a review defect or a decided trap: L3-01/05 (money scale), L3-04 (account grammar), ADR-0017 (registry key vs lineage), INV-09 (markup from data), L5-07 (user SQL).

```python
# bad
def run(sql: str) -> Frame: ...                       # internal and user SQL look the same
# good
def run_internal(sql: LiteralString, params: Params) -> Frame: ...
def run_user_sql(sql: UserSql, params: Params, limits: QueryLimits) -> Frame: ...
```

```ts
// bad
<div dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
// good: SafeHtml can only come from the sanitizing renderer; <SafeMarkdown> is the one raw-HTML site
<SafeMarkdown source={msg.text /* UntrustedText */} />
```

**Enforcement.**
- pyright strict (`LiteralString`, `NewType`).
- `react/no-danger` (via `@eslint-react` or a `no-restricted-syntax` rule on `dangerouslySetInnerHTML`) everywhere except `components/app/SafeMarkdown.tsx` (`vue/no-v-html` except `SafeMarkdown.vue` if spike S13 keeps Vue).
- ESLint ban on `as <Brand>` casts outside the parser modules.
- Schema check: every field named `*amount*`, `*account*`, `*date*`, `*commodity*` in `contracts/openapi.json` carries an `x-ft-type` (T1). The name match is a **heuristic**: an allowlist file (`contracts/x-ft-type-allowlist.txt`) lists fields such as `amount_count` or `date_format` that match but are not domain values, each with a reason.
- Checklist `[EP-21]`: is a new brand justified by a named confusion? Is a costly confusion left unbranded?

### EP-22 The strictest type checking
**Rule.**
- **Python:** pyright `strict` is the single gate, with `reportMissingTypeStubs` on (local stubs in `backend/typings/beancount/`), `reportUnnecessaryTypeIgnoreComment`, `reportMatchNotExhaustive` and `reportImplicitOverride`. Every `# type: ignore` / `# pyright: ignore` carries a rule code and a reason.
- **TypeScript:** `strict` (checked by `tsc --noEmit`), `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, `noPropertyAccessFromIndexSignature`, `noFallthroughCasesInSwitch`, `verbatimModuleSyntax`. No `any`.

**Why.** Types are the cheapest verifier, and they carry the domain types of EP-21.

**Enforcement.**
- `pyrightconfig.json` / `tsconfig.json` settings checked by a test, so they cannot be loosened silently (loosening needs the owner, `PROCESS.md`0a).
- ruff `PGH003`; ESLint `no-explicit-any`, `ban-ts-comment` (with a description required).
- T0 gates.

## 2. Added principles (well justified by the review)

### EP-23 One concept, one implementation
**Rule.** Each kernel concept has exactly one implementation per side: amount parse/format, "today", account roots/kinds, SQL runners, balance and tolerance checks, metadata-key filters, LLM prompt loaders, tree builders, draft converters. Before adding a helper, search for an existing one.
**Why.** This is the review's central "patchwork" finding (review:00 §3: 15 formatters, 7 root lists, 6 balance checks, 5 SQL runners).

**Enforcement.** Concept counters are **bans with one allowed home**: each concept's one implementation carries an `@concept <name>` marker, and `scripts/concept_counters.py` fails CI when a second implementation of a concept appears outside its marked home (count = 1). Plus jscpd on both languages, and checklist `[EP-23]` ("does this already exist elsewhere?", `PROCESS.md` (slice verifier)).

### EP-24 Dependencies: exact pins, at least 14 days old, upgrades as slices
**Rule.**
- Every dependency is pinned exactly in a lockfile (`uv.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `formal/tla/tools.lock`). A version is adopted only once it is at least 14 days old. Each upgrade is its own slice, with the full T1 suite.
- **Security exception** (owner, 2026-10-25): a release that fixes a published security advisory affecting us may be adopted before it is 14 days old, with a one-line reason in the journal naming the advisory. Only that package (and what it strictly requires) moves early.
- Beancount upgrades also re-run the D2 equivalence suite (ADR-0010, guardrail 2).
- portalocker stays on the legacy apps' lock semantics (ADR-0005, ADR-0020).

**Why.** Owner rule (review:13 §4; recorded in ADR-0050, item 12). Fresh releases carry the most regressions, and a wrapped Beancount loader replica must move only deliberately.

**Enforcement.** `[tool.uv] exclude-newer = "14 days"`, pnpm `minimumReleaseAge: 20160`; Cargo has no such setting, so `Cargo.lock` is resolved to versions published at least 14 days earlier and a T1 check of its entries' publish dates is BL-0052; a CI check that the lockfiles are unchanged by a fresh resolve, and a Renovate-style dependency report in T2 (report only, never auto-merge).

### EP-25 The async edge only awaits
**Rule.** An `async def` handler only awaits. Blocking work (Beancount loads, file writes, SQLite, scikit-learn) runs in worker threads behind the workspace limiter or the heavy-load gate.
**Why.** L1-02: one write froze the whole server. This is the engineering means of INV-20.

**Enforcement.** The AST test from review:L1 §1.5 (no sync call into `usecases` from an `async def` without `to_thread`), plus the `/health` latency test during a 54k write (backend-core acceptance).

### EP-26 Privacy-safe logging by construction
**Rule.** Logs contain no financial content outside opt-in, time-limited debug mode. The logger's redaction is **default-deny**: only declared safe field kinds (ids, counts, durations, enums, codes, constant text) are written. Money, amounts, plain strings (including erased `NewType`s such as `AccountName`), `UntrustedText`, secrets and paths outside the app dirs are redacted automatically. AI diagnostics are opt-in under the same policy. "Report a problem" shows its contents before saving and scrubs free text (ARCHITECTURE §6A). *(Supports INV-23.)*
**Why.** v0.3 logged financial details at info level. Users must be able to attach a log without reviewing it line by line.

```python
# bad
log.info(f"duplicate found: {payee} {amount}")
# good
log.event(Ev.IMPORT_DUPLICATE_FOUND, session=session_id, row_count=1)   # payee/amount cannot be passed
```

**Enforcement.**
- The redaction corpus test (every sensitive type, every registered event emitted with hostile values).
- The report-scrub fixture test.
- ruff `G004`.
- Event-registry schema test (field kinds limited to the safe set unless the field is marked `debug_detail`).

### EP-27 Messages tell the user what happened, whether the data is safe, and what to do
**Rule.** Every user-facing error or warning message (from the registry, ARCHITECTURE §6.5) has those three parts. It is concise and plain for non-native English readers: no idioms, no jargon, codes only in the details.
**Why.** Owner rules (concise, clear helpText; no triple-negation rhythm). A message that doesn't say "your ledger is unchanged" makes a refused write look like data loss.

**Enforcement.** A registry test checks that every code defines `message`, `data_safe` and `next_step`, plus length limits. Checklist `[EP-27]` covers the wording; the doc auditor reviews new registry entries.

---

### EP-28 Test design (tightening, agent-approved)
**Rule.**
- Test through public interfaces (the module's `__all__` / `index.ts`, the HTTP API, the UI), never private helpers.
- Fakes for ports only; never mock inside the core.
- No sleeps and no real time: the injected `Clock` (EP-10) and explicit waits on conditions.
- One behaviour per test, tagged with its IDs (`INV`, `CAP`, finding).
- A property test names its property in its name or docstring.
- Goldens stay small: one behaviour per golden, reviewed side by side when changed (`PROCESS.md` (golden change rule)).

**Why.** Tests coupled to internals block the refactors the rebuild relies on; sleeps and real time cause flakes, the main threat to speed (`PROCESS.md` (limits)).

**Enforcement.** Lint bans on `time.sleep` / `setTimeout`-based waits in tests and on importing private names in tests; checklist `[EP-28]`.

### EP-29 Tests come from the spec, not from the code (owner, 2026-10-25)
**Rule.**
- Every expected value in a test comes from the spec: the ADR, intent-table row, invariant or capability the test cites, Beancount itself, the old app's observed behaviour, or a hand-checked fixture. It never comes from running the code under test and copying its output.
- Each test names the spec line it checks (its tag or docstring). A test with no spec behind it is either a characterization test (tagged as such) or a gap in the spec.
- For every check that accepts, tags or refuses (classifiers, guards, validators, table checks), a negative test sits beside each positive one: the case the spec says must **not** pass.
- When a test's expected value changes in the same slice as the code it tests, the journal says which spec line justifies the new value (PROCESS.md §8 already requires this for goldens).

**Why.** Tests written next to the code drift towards asserting what the code does, and then they protect its bugs. In S0002 and S0004 two such tests passed against wrong code: an asserted price that the legacy rule never produces, and a "dropped lot date is expected" case that the spec forbids. Owner: "The reviewers should also review the tests and carefully check that the tests are testing the spec and not bending themselves to the code as it is written" (owner, 2026-10-25, in session).

**Enforcement.** Verifier checklist `[EP-29]`: the verifier reviews every new and changed test against the spec (see `.claude/agents/verifier.md`, "Review the tests"); mutation testing (T2) shows tests that pass whatever the code does.

### EP-30 Remove a risk class before defending against it (owner, 2026-10-25)
**Rule.** When a design choice can make a class of defect impossible (refuse the input, leave the feature out, pick the simpler structure), prefer it to a check that defends against each case. Defend case by case only when the class must be supported, and say so in the brief.

**Why.** S0005 spent four verifier rounds defending a copy against tracked symlinks, one escape route at a time, in a repo that has no symlinks; refusing every symlink would have closed the class in the first round (ADR-0058).

**Enforcement.** Verifier checklist `[EP-30]`.

## 3. Verifier checklist (generated from this file)

The verifier prompt includes this list. **Every finding cites at least one ID**: an `INV-nn` (must), an `EP-nn` (should), an `ADR-nnnn`, a `CAP-…` row, a `SPEC:<file>#<anchor>`, or `BUG` with a repro. It gives a repro or the rule it breaks (`PROCESS.md` (agents)).

| ID | Question the verifier answers | Automated? |
|---|---|---|
| EP-01 | Is any decision left in the shell or a view? | partly (purity contracts) |
| EP-02 | Does a new public name earn its place? | partly (`__all__`, boundaries) |
| EP-03 | Is any Beancount semantic re-derived? | yes (gateway, counters, vectors) |
| EP-04 | Any new cross-request state not registered and bounded? | yes |
| EP-05 | New interface without a real second variation? Registry entry fully self-described? | partly |
| EP-06 | Raw `str`/`dict` past an edge? Pydantic in core? | partly |
| EP-07 | I/O not through a port? | yes |
| EP-08 | Effect executed without a plan where a preview is due? | partly |
| EP-09 | A read that loads the ledger, or a write outside `ledger.writer`? | yes |
| EP-10 | Ambient clock, id or randomness? | yes |
| EP-11 | Undeclared cross-slice import? | yes |
| EP-12 | FP library or a foreign idiom? | yes (denylist) |
| EP-13 | Swallowed error, string match, ad-hoc presentation, missing code test? | yes |
| EP-14 | Contract changed without a version or a migration? | yes |
| EP-15 | Untimed port call, unregistered event, raw console/print? | yes |
| EP-16 | Generated file edited, or a stale artifact? | yes |
| EP-17 | Implicit workflow state; model coverage below 100%? | yes |
| EP-18 | Write-core change without a model update? | partly (mutation score) |
| EP-19 | Protocol change without a spec change; mapping broken? | yes |
| EP-20 | Hand-drawn structural diagram? | partly |
| EP-21 | Costly confusion left unbranded, or a brand without a reason? | partly |
| EP-22 | Loosened type setting, or an unexplained ignore? | yes |
| EP-23 | Second implementation of a concept? | yes (counters, jscpd) |
| EP-24 | Unpinned or too-new dependency; upgrade mixed into a feature slice? | yes |
| EP-25 | Blocking call on the event loop? | yes |
| EP-26 | Financial content or free text reachable in a log line? | yes |
| EP-27 | Message missing "what happened / data safe / next step", or hard to read? | partly |
| EP-28 | Test through internals, a mock inside the core, a sleep, or several behaviours in one test? | partly (lint bans) |
| EP-29 | Does any test take its expected value from the code instead of the spec, lack the negative case, or change its expectation without a spec reason? | partly (mutation testing) |
| EP-30 | Does a check defend case by case against a risk class that a simpler design would remove? | no |

---

## 4. How the agents use this, and how it is governed

**Where it lives.** This file, **`design/ENGINEERING.md`**, sits beside `design/INVARIANTS.md` and `design/process/PROCESS.md`. It is referenced from `PROCESS.md` and from every agent definition in `.claude/agents/*.md`. Agent prompts link to its sections and do not restate them, the same rule `PROCESS.md` follows. The §3 checklist and the automated-check table in §0 are generated sections: the checklist from the EP headers, the check table from the CI and hook configuration, so they cannot disagree with what runs.

**Who uses it, and how.**
- **Implementer.** The `orient` skill returns the EPs relevant to the slice (by touched package and capability) with the capability rows, ADRs and invariants. The slice brief lists those EPs. Any intended deviation is declared up front in the brief (`Deviates: EP-nn — reason`), or recorded in the slice record when found during the work.
- **Verifier** (every slice). It receives `ENGINEERING.md` with the invariants and ADRs. It runs the §3 checklist, and **every finding cites INV/EP IDs**. A deviation without a recorded reason is a finding. A recorded deviation whose reason doesn't hold is escalated as an implementer–verifier disagreement (`PROCESS.md` (escalation rubric, #7)) when it is High.
- **Area reviews and lens reviews** (`PROCESS.md` (review altitudes)). They check adherence across the whole area or lens, especially what diffs hide: EP-02 (deep modules), EP-11 (slice edges), EP-21 (brand coverage), EP-23 (concept duplication).
- **Drift auditor** (weekly, run locally in Claude Code on the owner's machine; CI runs only the deterministic checks, `PROCESS.md` (agents)). It runs every automated check in §0 against the whole repo, lists the recorded deviations that have aged past their slice (a deviation that keeps recurring should become an ADR or an EP change), and opens fix slices (`PROCESS.md` (self-healing)).
- **Red teams** (`PROCESS.md` (red teams)). They cite EP-18/19 for data safety, EP-21/26 for security and privacy, and EP-13/27 for UX errors.
- **Reflection agent.** It reviews EP finding rates and escapes per EP, and proposes edits.

**Governance (as for `PROCESS.md`).**
- Agents **propose** changes, with the evidence (an escape, a metric, a repeated deviation).
- **The owner approves**, in the Claude Code session, recorded with the owner's confirmation quoted in `IMPROVEMENTS.md` (owner, 2026-10-24; ADR-0045). Tightening (a new EP, a stricter check) may be applied by agents and approved after the fact; loosening (removing or weakening an EP or its enforcement) needs the owner first (`PROCESS.md` (guardrails on self-change)).
- Every change is an `IMPROVEMENTS.md` entry that names the EP and the section of this file it changed, in the same change as the check it alters.
- Promoting a principle to an invariant follows "Invariants evolve" (`PROCESS.md` (invariants evolve)).

**`CLAUDE.md` files.** They point into `design/` (this file, `ARCHITECTURE.md`, `INVARIANTS.md`, `PROCESS.md`) plus a few working rules. From then on the doc auditor's no-drift rule (`PROCESS.md` (no-drift docs)) covers them.
