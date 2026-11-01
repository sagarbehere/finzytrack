"""The rebuild after a save, inside the engine (`design/specs/engine-jobs.md` §6.7, §7; ADR-0068 D1; BL-0118; S0070).

After every landed save the sidecar queues a background `rebuild`: Beancount's own load of the disk
(`beancount_port.real_load`: its loader without the pickle cache, never the overlay replica; no encrypted file is
decrypted), so the screens show Beancount's result and its errors, not a prediction. It reads the ledger and writes
nothing. Nothing gates on it and nothing compares it with anything.

`run(ports, hint, job)` (it runs the ledger's plugins, so the sidecar retires its engine after it):
1. the ledger set named by `hint` (the saved plan's fingerprint, or the previous try's answer) observed;
2. Beancount's real load of its root;
3. the same set observed again.
`loaded` when the two observations are equal and the load parsed no file and used no documents root outside the set:
the result is Beancount's own for the disk as observed. Otherwise `moved`, with the fingerprint to try with next: the
set observed after the load, widened by the files, documents roots and `document` files the load used outside it. The
sidecar tries again in a fresh engine, at most 3 tries in a row (§7's load rule). The answer carries the result's
counts only (INV-23).

Limit (spec §15): a glob `include` the set does not name (an outside edit added it after the save) is not observed as
a glob; the files it matched are, so only a match that appears or goes during the load is missed, and the next rebuild
shows it. An encrypted file met raises `port.EncryptedFileMet` and any port operation may raise `OSError`: the
rebuild did not run to a result, which the sidecar logs.
"""

from __future__ import annotations

import gc
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet
from ledger_engine.ledger.overlay import documents_roots
from ledger_engine.tla import tla_action

type Phase = Literal["updating_views"]
PHASE: Final[Phase] = "updating_views"
type Outcome = Literal["loaded", "moved"]


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


def _observe(ledger_set: LedgerSet) -> Fingerprint:
    return _fingerprint.observe(ledger_set)


def _real_load(root: str) -> port.ParseResult:
    """Beancount's own loader, the garbage collector paused while it runs (ADR-0008 amendment)."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        return port.real_load(root)
    finally:
        if collecting:
            gc.enable()


@dataclass(frozen=True, slots=True)
class Ports:
    """The rebuild's effects: Beancount's real load of a root, and the observation of a ledger set."""

    load: Callable[[str], port.ParseResult] = _real_load
    observe: Callable[[LedgerSet], Fingerprint] = _observe


@dataclass(frozen=True, slots=True)
class Answer:
    """`loaded` (Beancount's result for the disk as `fingerprint` observed it) or `moved` (`fingerprint` names the set
    to try with next); `entries` and `errors`: the counts of the load's result (`loaded` only, else 0)."""

    outcome: Outcome
    fingerprint: Fingerprint
    entries: int = 0
    errors: int = 0


def _used(entries: list[port.Directive], options: port.OptionsMap) -> LedgerSet:
    """What the load used of the disk, as a ledger set (its globs are not known: the module's limit)."""
    roots = documents_roots(options) if options.get("documents") else []
    docs = [e.filename for e in entries if isinstance(e, port.Document)]
    return LedgerSet(str(options["filename"]), tuple(options["include"]), (), tuple(roots), tuple(docs))


def _wider(a: LedgerSet, b: LedgerSet) -> LedgerSet | None:
    """`a` widened by what `b` names outside it, or None when `b` names nothing outside it."""
    files = set(b.files) - set(a.files)
    documents = set(b.documents) - set(a.documents)
    document_files = set(b.document_files) - set(a.document_files)
    if not (files or documents or document_files):
        return None
    return LedgerSet(
        a.root,
        (*a.files, *sorted(files)),
        a.globs,
        (*a.documents, *sorted(documents)),
        (*a.document_files, *sorted(document_files)),
    )


@tla_action("R4_Load", "R4_Check")
def run(ports: Ports, hint: Fingerprint, job: Job) -> Answer:
    """The module's docstring: reads the ledger, writes nothing."""
    ledger_set = hint.ledger_set
    before = ports.observe(ledger_set)  # step 1
    job.progress(PHASE)
    entries, errors, options = ports.load(ledger_set.root)  # step 2: Beancount's own loader (user code)
    after = ports.observe(ledger_set)  # step 3
    wider = _wider(ledger_set, _used(entries, options))
    if wider is not None:
        return Answer("moved", ports.observe(wider))
    if after != before:
        return Answer("moved", after)
    # TODO(BL-0113): build the mirror from this result and publish it, after §7's `.open` check.
    return Answer("loaded", after, len(entries), len(errors))
