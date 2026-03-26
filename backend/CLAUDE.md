# Backend Rules

Python + Beancount + SQLite backend. FastAPI with Pydantic models.

## Error Handling (MANDATORY)

- **Raise `APIError`** for all errors — never `HTTPException` or raw error dicts
- `APIError(message, code, status_code, details)` — `code` is a required string (e.g. `"VALIDATION_ERROR"`, `"FILE_NOT_FOUND"`)
- **Return `ApiResponse[T]`** via `success_json_response(data)` for all non-file-serving endpoints
- File-serving endpoints (returning raw JSON/text content) may use `JSONResponse`/`PlainTextResponse`, but errors must still use `APIError`
- SSE streaming endpoints use `StreamingResponse` with errors as SSE events
- Never return raw dicts like `{"success": True, "path": "..."}` — always wrap in `ApiResponse`
- Reference: `app/exceptions.py`, `app/error_handler.py`, `app/helpers/response_helpers.py`

## Config Conventions

- Conventional directories (csv_rules, xls_rules, email_rules, recipes, ofx_mappings.yaml) live under `config/` — these are derived properties on the `Config` model, not user-configurable fields
- `config_dir` property always returns `Path('./config')` relative to CWD
- Only `ledger_file` is a user-configurable file path (supports hot-switching at runtime)

## API Codegen Workflow

When adding or modifying endpoints:
1. Complete all backend work first
2. **Stop and ask the user** to restart the backend and regenerate the frontend API (`npm run generate-api` from `frontend/`). Do not attempt to regenerate yourself — the backend must be running with the new code.
3. After the user confirms, proceed with frontend work.
