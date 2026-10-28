"""The ledger kernel: Beancount wrapped, never re-implemented (ARCHITECTURE.md §2.2; EP-03; INV-02).

`beancount_port` is the only module that imports Beancount; `overlay.load` is the only full load (ADR-0010);
`policy.LoadPolicy` decides what a load may read and run; `grammar` holds the account and currency grammar and the
tolerances (ADR-0024).
"""
