# typings/: local type stubs for the parts of Beancount 3.2.0 that carry no type information

pyright (strict, EP-22) reads these in place of the library's own modules (`stubPath` defaults to `typings`). Each
stub replaces its whole module for the type checker, so it declares exactly the names the engine and its tests
use, with the types Beancount 3.2.0's code has (read from its source, S0049). Only `ledger_engine.ledger.beancount_port`
imports Beancount in product code (INV-02); the engine's tests may too. A Beancount upgrade (ADR-0004) re-reads
every stub against the new source.
