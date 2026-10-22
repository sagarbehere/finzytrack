"""Statement import — investment statements (CAS today; OFX / CSV rules later) → ledger.

``parsers/`` turn a file into the neutral ``types.StatementData``; ``plan`` turns that
into what would be written (pure); ``snapshot`` reads the ledger state the plan needs;
``store`` keeps a parsed statement in memory between preview calls.
See dev-docs/cas-import.md.
"""
