"""KERNEL, the read half of the mirror (ARCHITECTURE.md §2.2, §2.3; ADR-0015, ADR-0016).

The engine builds and publishes the mirror (`ledger_engine.mirror`); the main sidecar only reads it: `files` opens a
mirror file read-only, `reader` holds the typed reads of the app's own SQL, and `query` the one hardened runner for
user SQL. No read loads the ledger (ADR-0011) and nothing is kept between calls (INV-10): every call opens its own
connection and closes it.
"""
