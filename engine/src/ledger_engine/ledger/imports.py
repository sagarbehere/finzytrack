"""What a load's imports read: the plugin code it ran, recorded for its fingerprint (S0099, BL-0124;
`design/specs/engine-jobs.md` §1 "Fingerprint").

`recording(into)` wraps one load. Each module the load's imports look up through the path search
(`beancount_port.recording_imports`) goes into `into`, keyed by its name and the folders searched, with the answer
(`core.ledger_set.Module`, or None when not found), the SHA-256 of the file found and, for a package, its folders'
importable names (`fingerprint.package_listing`), taken at the lookup, before the loader reads it; the loader then
compiles a source module from its source alone, never from cached bytecode (`beancount_port._SourceOnly`): an edit after
that makes the next observation differ (the safe side). The first answer for a key stays, as for the ledger's files
(`overlay._Seen`): a module not found and then found in one load (two `plugin` lines, a file made in between) gave a
result the next load would not give, so the next observation must differ.

Every module the load imports is recorded, wherever it was found: a plugin beside the ledger, a module a plugin imports
while it runs, the standard library and Beancount's own steps when they are first imported then. A module the engine
imported before the load is not looked up by it (the module cache answers), so it is not an input of the load. A file
in a `pythonpath` folder, which comes first on `sys.path` while the plugins run, can replace a module found later on
the path; recording each lookup with its folders catches that too (S0099 brief, "which modules count").

**One load with plugins per engine** (spec §5.1): a second load in the same process would not look up the modules the
first imported (the module cache answers) and would run that older code without naming it. Every job loads at most
once, and an engine that ran user code is ended after its job, so this holds; a job that loads twice must start the
second load in a fresh process (as the copy's check does, `fresh_load`). Nothing here keeps state between loads
(INV-10: the engine holds no store across jobs).

Limit (spec §1, "plugin files"): code a plugin loads by itself (`exec`, `importlib.util.spec_from_file_location`, a
finder it installs, a thread that imports after the load) is not recorded, like files a plugin reads by itself.
"""

from __future__ import annotations

from collections.abc import Generator, MutableMapping
from contextlib import contextmanager

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint
from ledger_engine.ledger.core.ledger_set import Module, ModuleKey

type Modules = MutableMapping[ModuleKey, Module | None]


@contextmanager
def recording(into: Modules) -> Generator[None]:
    """Record into `into` what the load run inside the block imports (the module's docstring)."""

    def on_lookup(lookup: port.ModuleLookup) -> None:
        into.setdefault((lookup.name, lookup.path), fingerprint.module(lookup))

    with port.recording_imports(on_lookup):
        yield
