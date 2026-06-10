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

All writes to the Beancount ledger file **must** go through `LedgerManager._write_entries()` (which delegates to `_do_write_entries()` under the per-user write lock). This is the single authorised write path. No exceptions.

**Rules:**
- **Never do text manipulation on the ledger file** — no reading as raw text, no string concatenation, no line-level editing. The ledger is a structured document owned by the Beancount printer.
- **Every write is a full rewrite** — transient-parse the ledger via `_parse_ledger()` to get the current entry list, modify the in-memory list, then pass the complete list to `_write_entries()` which truncates and rewrites the entire file via the engine's `format_entries()`.
- **No in-memory ledger cache.** Post-CQRS, reads come from the SQLite mirror via `SqliteReader`; writes parse the ledger on demand. There is no `cache.get_entries()` to consult.
- **To add entries**, use `append_entries(new_entries)` which parses + appends + calls `_write_entries()`.
- **To modify/delete entries**, parse via `_parse_ledger()`, mutate the entry list, then call `_write_entries(entries)`.
- **`_do_write_entries()` is the only caller of `atomic_ledger_write()`**, which handles backup creation, atomic temp-file writes with fsync, and the durable rename. No other code should call `atomic_ledger_write()` directly.
- **Padding transaction filter** — `BeancountEngine.format_entries()` silently drops auto-generated padding transactions (flag `'P'` with no stable ID) because Beancount regenerates them from `pad`+`balance` directives at parse time. This filter must live in exactly one place: the engine's formatter, so every write path goes through it.

**Why:** Beancount's `loader.load_file()` auto-generates padding transactions (`flag='P'`) in memory from `pad` directives. If any write path bypasses the filter (e.g., raw text append, text-level editing), these phantom transactions get materialized to disk and accumulate. This was a real bug that took significant effort to diagnose.

**Exception — initial ledger creation:** `LedgerInitializer.ensure_ledger_exists()` may call `BackupManager.atomic_write()` directly to materialize a brand-new ledger from a template, but only when the ledger file does not yet exist. The template is human-authored raw text containing comments (`;; ...`) that are not Beancount entries and would be discarded if round-tripped through `format_entries()`. There are no entries to filter, so the padding-flag invariant is trivially satisfied. This is the only sanctioned bypass.

**Reference:** `app/core/ledger_manager.py` — `_write_entries()`, `_do_write_entries()`, `append_entries()`; `app/core/beancount_engine.py` — `format_entries()` (padding filter).

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
- **Edit-path tests must simulate the API construction of the updated entry.** Build a fresh `Transaction` / `Balance` / `Open` from scratch with only the fields the HTTP payload carries — *do not* use `entry._replace(narration=...)` on a parsed entry. Namedtuple `_replace` preserves parser-stamped `meta['filename']` and `meta['lineno']` by reference, which masks bugs in the multi-file write-routing logic (the API path constructs the updated entry without those fields). See `BeancountEngine._carry_source_location` for the rule every replacement site enforces, and `test_edit_via_fresh_transaction_object_still_routes_to_source_file` for the canonical test shape.

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
