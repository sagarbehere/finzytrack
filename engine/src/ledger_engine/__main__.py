"""`python -I -m ledger_engine`: the engine process (ADR-0054). See `ledger_engine.protocol.server`."""

from ledger_engine.protocol.server import main

raise SystemExit(main())
