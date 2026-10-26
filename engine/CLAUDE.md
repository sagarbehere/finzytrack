# engine/ (the ledger engine)

Pointers only. The process split, protocol and supervision: `design/ARCHITECTURE.md` §2.0; why: ADR-0054. Pins: §10 (EP-24). Principles: `design/ENGINEERING.md`.

- A separate program with its own environment (`uv.lock`): Beancount 3.2.0 on Python 3.13 (ADR-0004), GPL-2.0-only as a whole. Every new dependency must pass the `[engine]` list in `licences.allow.toml` (GPL-2.0-compatible).
- It **never imports the main sidecar** (`app`), and nothing outside `engine/` imports `ledger_engine`; they talk only through the protocol. Inside the engine only `src/ledger_engine/ledger/beancount_port.py` imports `beancount` (INV-02), and only it may import or run code by name (`import_module`, `__import__`, `runpy`, `exec`, `eval`). `scripts/check_engine_boundary.py` enforces all four.
- The protocol's single source is `src/ledger_engine/protocol/messages.py`. After changing it, regenerate `contracts/engine-protocol.schema.json` (`.venv/bin/python -I -m ledger_engine.protocol.schema > ../contracts/engine-protocol.schema.json`) in the same commit; the main side's codec (`backend/src/app/platform/engine_protocol.py`) and its contract test follow.
- No resident ledger between requests (INV-10); no pickles, shared memory or callbacks across the protocol (ADR-0054 decision 2).
- Gates: `uv sync --locked`, then from `engine/`: `.venv/bin/ruff format --check --config ruff.toml .`, `.venv/bin/ruff check --config ruff.toml .`, `.venv/bin/pyright` (strict), `.venv/bin/python -m pytest -q`.
