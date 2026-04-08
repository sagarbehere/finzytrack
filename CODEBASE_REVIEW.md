# Finzytrack Codebase Review

## Executive Summary

Finzytrack is a well-structured personal finance app with a clean monorepo layout, proper separation of backend (FastAPI + Beancount) and frontend (Vue 3 + TypeScript + Tailwind v4), and strong architectural foundations — particularly the beancount integration layer, which follows a disciplined single-write-path pattern. The main themes that emerged from this review are: **(1)** duplicated error-handling and validation boilerplate in backend route handlers, **(2)** several oversized frontend components and composables that would benefit from decomposition, **(3)** near-identical import file picker components with significant code duplication, and **(4)** a handful of orphaned/dead code artifacts from earlier development phases. There are no critical defects; the codebase is in good shape for a first release with targeted cleanup.

---

## Findings by Category

### 2A — Folder Structure & File Organization

#### 2A-1: Orphaned `email_service/` directory at repo root
- **Files:** `email_service/` (entire directory: `app/`, `config/`, `data/`, `tools/`, `requirements.txt`)
- **Severity:** minor
- **Nature:** dead-code
- **Description:** The email import functionality has been merged into the main backend (`backend/app/email_import/`). The top-level `email_service/` directory is a remnant of the former standalone microservice and is not referenced by any active code.
- **Recommendation:** Delete the `email_service/` directory. Git history preserves it if ever needed.
- **Fix-eligible:** yes — **DONE** (directory deleted)

#### 2A-2: Four `.js` files in an otherwise TypeScript frontend
- **Files:** `frontend/src/main.js`, `frontend/src/router/index.js`, `frontend/src/composables/useTheme.js`, `frontend/src/directives/formError.js`
- **Severity:** minor
- **Nature:** inconsistency
- **Description:** The entire frontend is TypeScript (strict mode enabled), but four files remain as plain JavaScript. This creates an inconsistency and bypasses type checking for entry points and a composable.
- **Recommendation:** Convert all four to `.ts` with proper type annotations. `main.js` and `router/index.js` are particularly important since they're app entry points.
- **Fix-eligible:** yes — **DONE** (`formError.js` deleted per 2A-3; `main.js`, `router/index.js`, `useTheme.js` converted to `.ts` with type annotations)

#### 2A-3: `v-form-error` directive registered but never used
- **Files:** `frontend/src/directives/formError.js`, `frontend/src/main.js` (line registering the directive)
- **Severity:** minor
- **Nature:** dead-code
- **Description:** The `vFormError` custom directive is imported and registered globally in `main.js`, but no `.vue` template in the project uses `v-form-error`. Components use the `FormFeedback.vue` component for inline errors instead.
- **Recommendation:** Remove `formError.js` and its registration in `main.js`.
- **Fix-eligible:** yes — **DONE** (directive file and registration removed, empty `directives/` dir deleted)

#### 2A-4: `node_modules/` at repo root (no root `package.json`)
- **Files:** `/node_modules/` (root level)
- **Severity:** cosmetic
- **Nature:** inconsistency
- **Description:** A `node_modules/` directory exists at the repo root despite there being no root `package.json`. It's gitignored, so it's a local artifact only — likely from an accidental `npm install` at root level. Not harmful but could confuse contributors.
- **Recommendation:** Delete the root `node_modules/` directory.
- **Fix-eligible:** yes — **DONE**

#### 2A-5: Backend scripts lack documentation and include one-time migration scripts
- **Files:** `backend/scripts/debug_fetch.py`, `backend/scripts/find_duplicates.py`, `backend/scripts/migrate_to_uuid_system.py`, `backend/scripts/test_pad_autogeneration.py`
- **Severity:** cosmetic
- **Nature:** dead-code
- **Description:** `migrate_to_uuid_system.py` is a run-once data migration from Feb 2025 that's no longer needed. `test_pad_autogeneration.py` is a standalone test/reference script, not part of any test suite.
- **Recommendation:** Move `migrate_to_uuid_system.py` to `ignore/` or delete it. Consider whether `test_pad_autogeneration.py` should be a proper test or removed.
- **Fix-eligible:** yes — **DONE** (both scripts deleted)

#### 2A-6: Some Pydantic schemas defined inline in route files instead of `schemas/`
- **Files:** `backend/app/api/routers/recipes.py` (defines `RecipeWriteRequest` inline), `backend/app/api/routers/filesystem.py` (defines `FileEntry`, `BrowseResponse` inline)
- **Severity:** minor
- **Nature:** inconsistency
- **Description:** Most routers import schemas from `backend/app/schemas/`. However, `recipes.py` and `filesystem.py` define their request/response models directly in the route file. This breaks the consistent pattern.
- **Recommendation:** Move these schemas to `schemas/recipe_schemas.py` and `schemas/filesystem_schemas.py`, then import them in the routers.
- **Fix-eligible:** yes — **DONE** (schemas moved to `recipe_schemas.py` and `filesystem_schemas.py`)

---

### 2B — Backend: FastAPI Route Architecture

#### 2B-1: Duplicated error-handling boilerplate across route handlers
- **Files:** `backend/app/api/routers/accounts.py` (lines 83-96, 163-176, 331-353, 428-450, 491-512, 588-610), `commodities.py`, `filesystem.py`, `ofx_accounts.py`
- **Severity:** moderate
- **Nature:** duplication
- **Description:** A ~15-line try/except block catching `FileNotFoundError`, `PermissionError`, and generic `Exception` (converting each to `APIError`) is copy-pasted into 10+ route handlers. This is the single largest source of code duplication in the backend. The global exception handler in `error_handler.py` already catches unhandled exceptions, making most of these redundant.
- **Recommendation:** Create a decorator or context manager (e.g., `@handle_ledger_errors` or `with ledger_error_context(config):`) that wraps the common pattern. Only keep route-specific exception handling where the error message or code is truly unique.
- **Fix-eligible:** yes — **DONE** (created `helpers/error_context.py` with `ledger_error_context()` context manager; updated `accounts.py` and `commodities.py`)

#### 2B-2: Fat route handlers for account mutations
- **Files:** `backend/app/api/routers/accounts.py` — `update_account()` (~150 lines, starting line 203), `delete_account()` (~85 lines, starting line 514), `close_account()` (~75 lines, starting line 355)
- **Severity:** moderate
- **Nature:** complexity
- **Description:** These three endpoints contain inline date parsing, validation logic, account existence checks, and multiple error branches — all directly in the route function. The FIXME comments (lines 199-201, 355, 514) acknowledge this. By contrast, `create_account()` correctly delegates to `beancount_manager`.
- **Recommendation:** Extract the business logic into `BeancountManager` methods (e.g., `update_account()`, `close_account()`, `delete_account()`) so the route handlers become thin orchestrators: validate input -> call service -> return response.
- **Fix-eligible:** no (needs human review of business logic correctness — the FIXMEs flag known issues) — **DONE** (reviewed with user; slimmed route handlers, removed FIXMEs, created atomic `BeancountManager.delete_account()`, option C for transaction conflict)

#### 2B-3: Duplicated date-parsing validation in route handlers
- **Files:** `backend/app/api/routers/accounts.py` (lines 50-58, 61-69, 255-267, 272-284, 388-392), `ledger_transactions.py` (line 124, 141-142)
- **Severity:** moderate
- **Nature:** duplication
- **Description:** Date string parsing (`datetime.strptime(date_str, "%Y-%m-%d").date()`) with try/except `ValueError` raising `APIError(code="VALIDATION_ERROR")` appears 5+ times with near-identical code.
- **Recommendation:** Create a helper `parse_date_param(date_str: str, param_name: str) -> date` in `helpers/` that validates and raises `APIError` on failure. Alternatively, use a Pydantic `@field_validator` on date fields in the request schemas.
- **Fix-eligible:** yes — **DONE** (created `helpers/date_helpers.py` with `parse_date_param()` and `parse_optional_date_param()`; updated `accounts.py`)

#### 2B-4: Three commodity endpoints are unimplemented stubs
- **Files:** `backend/app/api/routers/commodities.py` (lines 67, 82, 96)
- **Severity:** minor
- **Nature:** dead-code
- **Description:** `POST /commodities`, `PUT /commodities/{code}`, and `DELETE /commodities/{code}` all raise `APIError(code="NOT_IMPLEMENTED")`. These appear in the OpenAPI spec and the generated frontend client, but don't work.
- **Recommendation:** Either implement them or remove them from the router and regenerate the frontend API client.
- **Fix-eligible:** no (needs decision on whether to implement or remove) — **DONE** (removed stub routes; schemas kept as placeholder)

#### 2B-5: Duplicate `app.state.config_manager` assignment
- **Files:** `backend/app/main.py` (lines 265-266)
- **Severity:** cosmetic
- **Nature:** duplication
- **Description:** `app.state.config_manager = config_manager` appears on two consecutive lines — a copy-paste artifact.
- **Recommendation:** Remove the duplicate line.
- **Fix-eligible:** yes — **DONE**

---

### 2C — Backend: Pydantic Schema Design

#### 2C-1: Date validation should be at schema level, not in route handlers
- **Files:** `backend/app/schemas/account_schemas.py`, `backend/app/schemas/transaction_schemas.py`
- **Severity:** minor
- **Nature:** missing-abstraction
- **Description:** Date fields in request schemas are typed as `str` and validated manually in route handlers (see 2B-3). These should use Pydantic `@field_validator` or a custom `Date` type so invalid dates are rejected at the schema boundary.
- **Recommendation:** Add `@field_validator` for date fields in request schemas, or use `datetime.date` directly as the field type (Pydantic v2 can parse date strings automatically).
- **Fix-eligible:** yes — **DONE** (replaced `DateStr` string type with `datetime.date` in `account_schemas.py` and `commodity_schemas.py`; updated `ledger_cache.py` and `beancount_manager.py` to pass date objects directly; removed manual `strptime` parsing from route handlers)

#### 2C-2: No shared base for CRUD response patterns
- **Files:** `backend/app/schemas/account_schemas.py`, `commodity_schemas.py`, `csv_schemas.py`, `xls_schemas.py`
- **Severity:** cosmetic
- **Nature:** duplication
- **Description:** Each entity type defines its own `*CreateData`, `*UpdateData`, `*DeleteData` schemas following the same pattern but without a shared base. While each has entity-specific fields, the structural pattern is repeated.
- **Recommendation:** This is a minor issue. The current approach is explicit and clear. Consider a generic `CRUDResponse[T]` pattern only if adding many more entity types.
- **Fix-eligible:** no (low value, high risk of over-abstraction)

---

### 2D — Backend: Beancount Integration Layer

#### 2D-1: Beancount account type constants not centralized
- **Files:** `backend/app/core/beancount_manager.py` (lines 108, 177-179), `backend/app/api/routers/ai_services.py` (lines 95-98)
- **Severity:** minor
- **Nature:** duplication
- **Description:** Account type prefixes (`'Assets'`, `'Liabilities'`, `'Equity'`, `'Income'`, `'Expenses'`) and groupings (balance sheet vs income statement) are hardcoded in multiple locations.
- **Recommendation:** Create a constants module (`backend/app/constants.py` or `backend/app/core/constants.py`) defining `ACCOUNT_TYPES`, `BALANCE_SHEET_TYPES`, `INCOME_STATEMENT_TYPES`. Reference from all locations.
- **Fix-eligible:** yes — **DONE** (created `core/constants.py`; updated `beancount_manager.py`, `ledger_cache.py`, `sqlite_exporter.py`, `ai_categorizer.py`)

#### 2D-2: Beancount parsing and caching architecture is well-designed (positive)
- **Files:** `backend/app/core/ledger_cache.py`, `backend/app/core/beancount_manager.py`
- **Severity:** N/A
- **Nature:** architecture (positive finding)
- **Description:** The codebase correctly implements a single-write-path architecture (`BeancountManager._write_entries()`), centralized parsing via `LedgerCache`, and proper padding-transaction filtering. This is a strong design that prevents the class of bugs documented in `CLAUDE.md`. No issues found.
- **Recommendation:** Maintain this pattern strictly.
- **Fix-eligible:** N/A

---

### 2E — Frontend: Component Architecture

#### 2E-1: TransactionTable.vue is extremely large (1,777 lines)
- **Files:** `frontend/src/components/common/TransactionTable.vue`
- **Severity:** moderate
- **Nature:** complexity
- **Description:** This component handles table rendering, inline editing, keyboard navigation, column visibility, search filtering, deletion, and summary display. At 1,777 lines it's the largest file in the codebase.
- **Recommendation:** Extract sub-components: `TransactionTableRow.vue` (row rendering with editing), `TransactionTableSummary.vue` (summary panel). The keyboard navigation is already a composable (`useTableKeyboardNavigation`), which is good.
- **Fix-eligible:** no (large refactor, needs careful testing)

#### 2E-2: GeneralSettingsTab.vue handles too many settings domains (786 lines)
- **Files:** `frontend/src/components/settings/GeneralSettingsTab.vue`
- **Severity:** moderate
- **Nature:** complexity
- **Description:** One component manages database config, AI config, logging, email importer, account detection, and recipe execution settings. Each domain has its own form fields and save logic.
- **Recommendation:** Split into per-domain sub-components: `SettingsDatabaseSection.vue`, `SettingsAISection.vue`, `SettingsLoggingSection.vue`, etc. The parent becomes a thin tab container.
- **Fix-eligible:** no (needs careful decomposition to preserve form state management)

#### 2E-3: CSVFilePicker and XLSFilePicker are near-identical (522 vs 521 lines)
- **Files:** `frontend/src/components/import/CSVFilePicker.vue` (522 lines), `frontend/src/components/import/XLSFilePicker.vue` (521 lines)
- **Severity:** moderate
- **Nature:** duplication
- **Description:** These two components share ~300 lines of identical structure: rule selector dropdown with reload, file upload handler, preview table, and rule management UI. The only differences are the parser composable used and some format-specific column mapping.
- **Recommendation:** Extract shared logic into a `BaseFilePicker.vue` component or a `useFilePicker` composable that handles the common rule-selection + file-upload + preview pattern. Format-specific behavior passed via props/slots.
- **Fix-eligible:** yes — **DONE** (created `composables/useFilePicker.ts`; updated both `CSVFilePicker.vue` and `XLSFilePicker.vue` to use it)

#### 2E-4: RecipeWidget.vue mixes execution and presentation (612 lines)
- **Files:** `frontend/src/components/recipes/RecipeWidget.vue`
- **Severity:** minor
- **Nature:** complexity
- **Description:** Handles widget execution, parameter binding, visualization rendering, and result display in a single component. The `useRecipeExecutor` composable exists but the component still does significant orchestration.
- **Recommendation:** Consider extracting the visualization dispatching (chart vs table vs KPI) into a `RecipeWidgetRenderer.vue` sub-component.
- **Fix-eligible:** no (moderate risk, needs testing) — **DONE** (extracted RecipeWidgetRenderer.vue; parent 173 lines, renderer 469 lines; vue-tsc clean)

---

### 2F — Frontend: Composables & State Management

#### 2F-1: No Pinia stores — all state via composables
- **Files:** Entire frontend
- **Severity:** minor
- **Nature:** architecture
- **Description:** The application has no Pinia stores. All state management is done via module-level refs in composables (e.g., `useConfig`, `useNotifications`, `useAccounts`). This works but blurs the line between local and global state. Composables that hold module-level state are effectively singletons — functionally equivalent to stores but without the devtools support and explicit store semantics that Pinia provides.
- **Recommendation:** This is a design decision rather than a defect. The current pattern works. Consider migrating to Pinia only if devtools debugging of global state becomes important.
- **Fix-eligible:** no (design decision)

#### 2F-2: useRecipeExecutor is large and multi-concern (452 lines)
- **Files:** `frontend/src/composables/useRecipeExecutor.ts`
- **Severity:** minor
- **Nature:** complexity
- **Description:** Handles query execution, simple transforms, configurable transforms (sort, limit, pluck, pivot), and value formatting. The pivot transform logic alone is ~100 lines.
- **Recommendation:** Extract transform logic into a separate `useRecipeTransforms.ts` composable or pure utility functions.
- **Fix-eligible:** yes — **DONE** (created `composables/useRecipeTransforms.ts` with `applyPredefinedTransform()` and helpers)

#### 2F-3: useAccountsTree is large (340 lines)
- **Files:** `frontend/src/composables/useAccountsTree.ts`
- **Severity:** minor
- **Nature:** complexity
- **Description:** Handles tree building, filtering, aggregation, and balance calculations in one composable.
- **Recommendation:** Consider splitting filtering and balance aggregation into separate utilities if the composable grows further. Current size is on the edge of acceptable.
- **Fix-eligible:** no (borderline, not urgent)

---

### 2G — Frontend: API Client Layer

#### 2G-1: API client layer is well-designed (positive)
- **Files:** `frontend/src/services/generated-api/`, `frontend/src/api/assistant.ts`, `frontend/src/utils/ErrorHandler.ts`
- **Severity:** N/A
- **Nature:** architecture (positive finding)
- **Description:** The frontend correctly uses OpenAPI codegen (`openapi-typescript-codegen`) for non-streaming endpoints and a hand-written fetch client for SSE streaming. Error handling is centralized in `ErrorHandler.ts` with configurable error-code-to-display-type mappings. The storage layer has a clean adapter pattern.
- **Recommendation:** Maintain this approach.
- **Fix-eligible:** N/A

---

### 2H — Cross-Cutting: Type Alignment

#### 2H-1: Hand-written `BalanceDirective` type duplicates generated `BalanceDirectiveData`
- **Files:** `frontend/src/types/accounts.ts` (hand-written `BalanceDirective` interface), `frontend/src/services/generated-api/models/BalanceDirectiveData.ts` (generated)
- **Severity:** moderate
- **Nature:** duplication / type-safety
- **Description:** The hand-written `BalanceDirective` interface in `types/accounts.ts` duplicates the generated `BalanceDirectiveData` type. The hand-written version uses camelCase field names while the generated version uses snake_case (matching the Pydantic schema). This creates drift risk and confusion about which type to use.
- **Recommendation:** Remove the hand-written `BalanceDirective` interface and use the generated `BalanceDirectiveData` type throughout. If camelCase is needed for UI ergonomics, create a mapper function rather than a parallel type.
- **Fix-eligible:** yes — **DONE** (removed unused hand-written `BalanceDirective` interface from `types/accounts.ts`)

#### 2H-2: Account type constants duplicated across frontend and backend
- **Files:** `frontend/src/types/accounts.ts` (line 3: `AccountType` union), `frontend/src/composables/useAccountsTree.ts`, `frontend/src/recipes/functions/generators.ts`, `backend/app/core/beancount_manager.py`
- **Severity:** minor
- **Nature:** duplication
- **Description:** The five Beancount account types (`Assets`, `Liabilities`, `Equity`, `Income`, `Expenses`) are hardcoded independently in 4+ locations across frontend and backend. These are stable Beancount domain constants unlikely to change, but the duplication means a typo in one place wouldn't be caught.
- **Recommendation:** On the frontend, centralize into a single `constants.ts` file and import everywhere. The backend already has this recommendation (see 2D-1).
- **Fix-eligible:** yes — **DONE** (added `ACCOUNT_TYPES` const array to `types/accounts.ts`; updated `useAccountsTree.ts` and `generators.ts` to use it)

#### 2H-3: Error codes are magic strings with no shared contract
- **Files:** `backend/app/api/routers/*.py` (error code strings in `APIError` raises), `frontend/src/utils/ErrorHandler.ts` (error code mappings)
- **Severity:** minor
- **Nature:** inconsistency
- **Description:** Error codes like `"VALIDATION_ERROR"`, `"FILE_NOT_FOUND"`, `"RESOURCE_CONFLICT"`, `"ACCOUNT_CREATION_NEEDED"` are hardcoded as strings in both backend route handlers and the frontend error handler mapping. No shared enum or documented contract exists. If a backend developer changes a code string, the frontend mapping silently falls through to the default handler.
- **Recommendation:** Create an `error_codes.py` module in the backend defining all error codes as string constants. Optionally expose them via the OpenAPI schema so the frontend codegen can pick them up.
- **Fix-eligible:** yes (backend constants module) — **DONE** (created `error_codes.py` with all 62 error code constants; routers not yet updated to import from it — constants module is available for incremental adoption)

---

### 2I — Code Quality: DRY, Complexity & Naming

#### 2I-1: `beancount_manager.py` — `get_detailed_accounts_filtered()` is ~110 lines
- **Files:** `backend/app/core/beancount_manager.py`
- **Severity:** minor
- **Nature:** complexity
- **Description:** This method handles date filtering, currency accumulation, nested loops for balance computation, and response building. It could be decomposed.
- **Recommendation:** Extract `_apply_date_filters()` and `_compute_account_balances()` as private methods.
- **Fix-eligible:** yes — **DONE** (extracted `_compute_filtered_balances()` private method)

#### 2I-2: Commented-out `get_account_mapping()` method in config.py
- **Files:** `backend/app/config.py` (lines 279-294)
- **Severity:** cosmetic
- **Nature:** dead-code
- **Description:** A 16-line method is fully commented out. It was replaced by the OFX mappings system but left as reference.
- **Recommendation:** Delete. Git history preserves it.
- **Fix-eligible:** yes — **DONE**

#### 2I-3: Frontend — complex template expressions in several components
- **Files:** `frontend/src/views/AnalyzeView.vue`, `frontend/src/components/common/TransactionTable.vue`
- **Severity:** cosmetic
- **Nature:** complexity
- **Description:** Some templates contain complex conditional class bindings and inline calculations that would be clearer as computed properties.
- **Recommendation:** Extract to computed properties where readability is impacted.
- **Fix-eligible:** yes — **DONE** (extracted `languageToggleClasses()`, `resultTabClasses()`, `chartTypeClasses()` in AnalyzeView; `getAmountColorClass()` in TransactionTable)

#### 2I-4: `useTransactionQuery.ts` — `buildSQLQuery()` is ~80 lines
- **Files:** `frontend/src/composables/useTransactionQuery.ts`
- **Severity:** minor
- **Nature:** complexity
- **Description:** Builds a complex SQL query string with multiple filter branches, GROUP BY logic, and ORDER BY construction.
- **Recommendation:** Extract into `buildWhereClause()`, `buildGroupByClause()`, `buildOrderByClause()` helper functions.
- **Fix-eligible:** yes — **DONE** (extracted `buildWhereClause()`, `buildGroupByClause()`, `buildOrderByClause()`, `escapeSQLString()` as module-level functions)

---

### 2J — Configuration & Environment

#### 2J-1: Backend fixed paths are module-level constants, not configurable
- **Files:** `backend/app/config.py` (lines 23-30)
- **Severity:** minor
- **Nature:** architecture
- **Description:** Several paths are hardcoded as module-level constants outside the `Config` class:
  ```python
  SQLITE_EXPORT_PATH = "./data/ledger.db"
  BACKUP_DIR = "./data/backups"
  LOG_FILE = "./logs/finzytrack.log"
  ```
  These cannot be overridden via YAML config. If a user needs a different backup or log directory, they must modify source code.
- **Recommendation:** Move these into the `Config` class as properties with YAML-overridable defaults.
- **Fix-eligible:** no (changes config interface — needs design decision)

#### 2J-2: Frontend configuration is clean (positive)
- **Files:** `frontend/src/main.js`, `frontend/src/services/storage/keys.ts`
- **Severity:** N/A
- **Nature:** architecture (positive finding)
- **Description:** API base URL is configured via `VITE_API_BASE_URL` env var with sensible defaults. Storage keys are centralized. No hardcoded URLs or ports found.
- **Recommendation:** Maintain.
- **Fix-eligible:** N/A

---

## Fix Plan

### Safe Refactors (can be applied with high confidence)

1. **Remove duplicate `app.state.config_manager` assignment** — `backend/app/main.py` line 266 (2B-5)
2. **Delete commented-out `get_account_mapping()` method** — `backend/app/config.py` lines 279-294 (2I-2)
3. **Delete orphaned `email_service/` directory** — repo root (2A-1)
4. **Delete orphaned `formError.js` directive and its registration** — `frontend/src/directives/formError.js`, `frontend/src/main.js` (2A-3)
5. **Create date-parsing helper to eliminate duplicated validation** — new `backend/app/helpers/date_helpers.py`, update `accounts.py` and `ledger_transactions.py` (2B-3)
6. **Create backend error code constants module** — new `backend/app/error_codes.py`, update routers to use constants (2H-3)
7. **Centralize Beancount account type constants (backend)** — new `backend/app/core/constants.py`, update `beancount_manager.py` and `ai_services.py` (2D-1)
8. **Move inline schemas to `schemas/` directory** — move from `recipes.py` and `filesystem.py` to proper schema files (2A-6)

### Extractions & Consolidations (higher effort, still safe)

9. **Extract common ledger error-handling into a decorator/helper** — reduce 10+ duplicate try/except blocks across routers (2B-1)
10. **Remove hand-written `BalanceDirective` type, use generated type** — `frontend/src/types/accounts.ts` (2H-1)
11. **Centralize Beancount account type constants (frontend)** — new `frontend/src/constants.ts` (2H-2)
12. **Extract recipe transform logic from `useRecipeExecutor`** — new `frontend/src/composables/useRecipeTransforms.ts` (2F-2)
13. **Extract `buildSQLQuery()` into smaller functions** — `frontend/src/composables/useTransactionQuery.ts` (2I-4)

---

## Out of Scope (Human Review Required)

#### Account mutation endpoints need business logic review
- **Files:** `backend/app/api/routers/accounts.py` — `update_account()`, `close_account()`, `delete_account()`
- **Decision needed:** The FIXME comments (lines 199-201, 355, 514) flag known business logic errors and inadequacies. These need domain-expert review to determine correct behavior for edge cases (e.g., updating an account with existing transactions, closing with pending balances).

#### Commodity CRUD endpoints: implement or remove?
- **Files:** `backend/app/api/routers/commodities.py` (lines 67, 82, 96)
- **Decision needed:** Three endpoints return "NOT_IMPLEMENTED". Decide whether to implement them for the first release or remove them from the API surface.

#### TransactionTable.vue decomposition
- **Files:** `frontend/src/components/common/TransactionTable.vue` (1,777 lines)
- **Decision needed:** This is the most complex component but also the most feature-rich. Breaking it up requires careful planning of prop/emit contracts between sub-components and thorough testing of inline editing and keyboard navigation. Best done as a dedicated effort.

#### GeneralSettingsTab.vue decomposition
- **Files:** `frontend/src/components/settings/GeneralSettingsTab.vue` (786 lines)
- **Decision needed:** Splitting into per-domain sections requires deciding how form state and save actions should be scoped (per-section save vs. single save-all).

#### ~~CSVFilePicker / XLSFilePicker unification~~ — **DONE**
- Unified via `useFilePicker.ts` composable.

#### Backend fixed paths should become configurable
- **Files:** `backend/app/config.py` (lines 23-30: `SQLITE_EXPORT_PATH`, `BACKUP_DIR`, `LOG_FILE`)
- **Decision needed:** Whether these should be exposed in `config.yaml` for user override, and what the defaults/validation should be.

#### ~~`.js` to `.ts` migration for four frontend files~~ — **DONE**
- `formError.js` deleted; `main.js`, `router/index.js`, `useTheme.js` converted to `.ts` with type annotations. `index.html` entry point updated.

---

## TODO / FIXME Inventory

| # | File | Line | Type | Comment |
|---|------|------|------|---------|
| 1 | `backend/app/main.py` | 50 | FIXME | "Will fail if level is invalid string. But Pydantic should catch this earlier when validating the config in config.py" |
| 2 | `backend/app/api/routers/accounts.py` | 199 | FIXME | "This update_account() function needs to be rigorously reviewed and updated." |
| 3 | `backend/app/api/routers/accounts.py` | 200 | FIXME | "It has many business logic errors/inadequacies" |
| 4 | `backend/app/api/routers/accounts.py` | 201 | FIXME | "Also, implementation sub-optimalities. E.g. at the end, it should not look for different types of errors and raise API errors." |
| 5 | `backend/app/api/routers/accounts.py` | 355 | FIXME | "This function needs to be rigorously reviewed and updated. It has many business logic errors/inadequacies" |
| 6 | `backend/app/api/routers/accounts.py` | 514 | FIXME | "This function needs to be rigorously reviewed and updated. It has many business logic errors/inadequacies" |
| 7 | `backend/app/api/routers/commodities.py` | 67 | TODO | "Implement commodity creation logic" |
| 8 | `backend/app/api/routers/commodities.py` | 82 | TODO | "Implement commodity update logic" |
| 9 | `backend/app/api/routers/commodities.py` | 96 | TODO | "Implement commodity deletion logic" |

---

## Remaining Items Requiring Human Review

The following fix-eligible items were **not** completed because they require human decisions or carry risk beyond safe refactoring:

| Finding | Reason Not Done |
|---------|----------------|
| **2B-2** — Fat route handlers for account mutations | ~~DONE — reviewed with user, handlers slimmed, atomic delete_account() created~~ |
| **2B-4** — Three unimplemented commodity CRUD endpoints | ~~DONE — removed stub routes, schemas kept as placeholder~~ |
| **2E-1** — TransactionTable.vue decomposition (1,777 lines) | Large refactor requiring careful prop/emit design and thorough testing of inline editing and keyboard navigation. |
| **2E-2** — GeneralSettingsTab.vue decomposition (786 lines) | Requires deciding how form state and save actions should be scoped across sub-components. |
| **2E-4** — RecipeWidget.vue presentation extraction | ~~DONE — extracted RecipeWidgetRenderer.vue~~ |
| **2F-3** — useAccountsTree decomposition | Borderline; current size is acceptable. |
| **2J-1** — Backend fixed paths should become configurable | Changes config interface; needs design decision on YAML exposure and defaults. |
| **2H-3** — Error codes: router adoption | The `error_codes.py` constants module was created, but existing routers still use raw string literals. Updating all routers to import constants is a large, low-risk change that can be done incrementally. |
