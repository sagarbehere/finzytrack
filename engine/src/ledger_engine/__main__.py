"""The engine process (ADR-0054), run by the sidecar's bootstrap under `-s -S -B -P` (S0057). See
`ledger_engine.protocol.server`."""

from ledger_engine.protocol.server import main

raise SystemExit(main())
