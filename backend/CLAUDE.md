# Backend Rules

Python + Beancount + SQLite backend. FastAPI with Pydantic models.

## Money Handling (MANDATORY)

Money is `Decimal` in Python and `TEXT` (string-decimal) in SQLite. **Never `float` on the money path.** Aggregations use explicit `SUM(CAST(amount AS REAL))` with a comment pointing at `dev-docs/money-types.md`. Full contract: [`dev-docs/money-types.md`](../dev-docs/money-types.md).

## Error Handling (MANDATORY)

- **Raise `APIError`** for all errors — never `HTTPException` or raw error dicts
- `APIError(message, code, status_code, details)` — `code` is a required string (e.g. `"VALIDATION_ERROR"`, `"FILE_NOT_FOUND"`)
- **Return `ApiResponse[T]`** via `success_json_response(data)` for all non-file-serving endpoints
- File-serving endpoints (returning raw JSON/text content) may use `JSONResponse`/`PlainTextResponse`, but errors must still use `APIError`
- SSE streaming endpoints use `StreamingResponse` with errors as SSE events
- Never return raw dicts like `{"success": True, "path": "..."}` — always wrap in `ApiResponse`
- Reference: `app/exceptions.py`, `app/error_handler.py`, `app/helpers/response_helpers.py`

## Ledger Write Architecture (MANDATORY)

All writes to the Beancount ledger file **must** go through `BeancountManager._write_entries()`. This is the single authorised write path. No exceptions.

**Rules:**
- **Never do text manipulation on the ledger file** — no reading as raw text, no string concatenation, no line-level editing. The ledger is a structured document owned by the Beancount printer.
- **Every write is a full rewrite** — read entries from cache, modify the in-memory entry list, then pass the complete list to `_write_entries()` which truncates and rewrites the entire file via `printer.format_entry()`.
- **To add entries**, use `append_entries(new_entries)` which reads cache + appends + calls `_write_entries()`.
- **To modify/delete entries**, read from `cache.get_entries()`, mutate the list, then call `_write_entries(entries)`.
- **`_write_entries()` is the only caller of `atomic_ledger_write()`**, which handles backup creation, atomic temp-file writes, and cache invalidation. No other code should call `atomic_ledger_write()` directly.
- **Padding transaction filter** — `_write_entries()` silently drops auto-generated padding transactions (flag `'P'` with no stable ID) because Beancount regenerates them from `pad`+`balance` directives at parse time. This filter must live in exactly one place.

**Why:** Beancount's `loader.load_file()` auto-generates padding transactions (`flag='P'`) in memory from `pad` directives. If any write path bypasses the filter (e.g., raw text append, text-level editing), these phantom transactions get materialized to disk and accumulate. This was a real bug that took significant effort to diagnose.

**Reference:** `app/core/beancount_manager.py` — `_write_entries()`, `append_entries()`

## Config Conventions

- Conventional directories (csv_rules, xls_rules, email_rules, recipes, ofx_mappings.yaml) live under `config/` — these are derived properties on the `Config` model, not user-configurable fields
- `config_dir` property always returns `Path('./config')` relative to CWD
- Only `ledger_file` is a user-configurable file path (supports hot-switching at runtime)

## Testing (MANDATORY)

Tests must verify the **specification**, not mirror the implementation. See `dev-docs/testing-approach.md` for the full strategy.

**Rules when writing tests:**
- Every mutating endpoint test (create, update, delete) must verify the outcome via a subsequent read — never trust the response alone
- Use exact assertions (`==`) not subset assertions (`issubset`) unless there's a documented reason
- Assert exact counts, not `> 0`
- Engine contract tests are parameterized by engine (`@pytest.fixture(params=["beancount"])`) — they must not use Beancount-specific internals
- Do not assert on error message strings — assert on error codes and status codes

**Running tests:**
```bash
cd backend
python -m pytest tests/ -q                    # run all tests
python -m coverage run -m pytest tests/ -q    # run with coverage
python -m coverage report --show-missing      # see uncovered lines
```

**Before merging changes to `app/core/` or `app/api/routers/`:**
1. Run `python -m pytest tests/` — all tests must pass
2. Run coverage — new/changed code should be covered
3. Run mutation testing on changed files:
   ```bash
   python -m mutmut run --paths-to-mutate=<changed-file> --tests-dir=tests/
   ```
4. Investigate surviving mutations on covered lines — add tests for real behavioral gaps (not metadata/error-message/default-param mutations)

## API Codegen Workflow

When adding or modifying endpoints:
1. Complete all backend work first
2. **Stop and ask the user** to restart the backend and regenerate the frontend API (`npm run generate-api` from `frontend/`). Do not attempt to regenerate yourself — the backend must be running with the new code.
3. After the user confirms, proceed with frontend work.
