"""The rebuild after a save, inside the engine (`design/specs/engine-jobs.md` §6.7, §7; ADR-0068 D1; BL-0118; S0070).

After every landed save the sidecar queues a background `rebuild`: Beancount's own load of the disk
(`beancount_port.real_load`: its loader without the pickle cache, never the overlay replica; no encrypted file is
decrypted), so the screens show Beancount's result and its errors, not a prediction. It reads the ledger and writes
nothing. Nothing gates on it and nothing compares it with anything.

`run(ports, hint, job, target)` (it runs the ledger's plugins, so the sidecar retires its engine after it):
0. the mirror folder and its cleanup (`design/specs/mirror/publish.md` §4 step 1, S0087, BL-0113): first in every
   try, whatever follows; then this workspace's `.open` journal (one, or a journal folder that cannot be listed:
   `paused`, engine-jobs §7, before any result is returned; S0088); a published generation that is already fresh (§8:
   the disk, this engine build, its DDL, today) and whose stamp names all the hint names (BL-0212 V1: a glob or a file
   the hint names and the stamp lacks may have changed unseen) ends the job here: `fresh`, nothing loaded or built;
1. the ledger set named by `hint` (the saved plan's fingerprint, or the previous try's answer) observed;
2. Beancount's real load of its root;
3. the same set observed again.
`loaded` when the two observations are equal and the load parsed no file, used no documents root and imported no
code (S0099) outside the set:
the result is Beancount's own for the disk as observed, and it is published (publish.md §4 steps 2 to 8,
`mirror.publish.publish`): built into a new generation stamped with an observation of the set the load read (its
files, documents roots and `document` files, and the hint's globs that still lead to them, `_read_by`; S0087 round
1), then, just before the swap,
that set observed again (a change: `moved`, not published) and this workspace's `.open` journal looked for (one, or a
journal folder that cannot be listed: `paused`, not published; engine-jobs §7). Otherwise `moved`, with the
fingerprint to try with next: the set observed after the load, widened by the files, documents roots and `document`
files the load used outside it. The sidecar tries again in a fresh engine, at most 3 tries in a row (§7's load rule).
The answer carries counts, the generation's number and `ledger_version` only (INV-23).

Limit (spec §15): a glob `include` the set does not name (an outside edit added it after the save) is not observed as
a glob; the files it matched are, so only a match that appears or goes during the load is missed, and the next rebuild
shows it. An encrypted file met raises `port.EncryptedFileMet` and any port operation may raise `OSError`: the
rebuild did not run to a result, which the sidecar logs.
"""

from __future__ import annotations

import gc
import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.fs.port import FileSystem
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as _fingerprint
from ledger_engine.ledger import imports, load_failure, precision, publishing
from ledger_engine.ledger.core.ledger_set import Fingerprint, LedgerSet, Module, ModuleKey, decode
from ledger_engine.ledger.overlay import documents_roots
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.store import SqliteStore
from ledger_engine.tla import tla_action

type Phase = Literal["updating_views"]
PHASE: Final[Phase] = "updating_views"
type Outcome = Literal["loaded", "moved", "fresh", "paused", "refused"]


class Job(Protocol):
    def progress(self, phase: Phase) -> None: ...


def _observe(ledger_set: LedgerSet) -> Fingerprint:
    return _fingerprint.observe(ledger_set)


def _real_load(root: str, parsed: list[str]) -> port.ParseResult:
    """Beancount's own loader, the garbage collector paused while it runs (ADR-0008 amendment)."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        return port.real_load(root, parsed)
    finally:
        if collecting:
            gc.enable()


@dataclass(frozen=True, slots=True)
class Ports:
    """The rebuild's effects: Beancount's real load of a root, the observation of a ledger set, and the publish's:
    the file-system port, the mirror's store and the name's random tag."""

    load: Callable[[str, list[str]], port.ParseResult] = _real_load
    warnings: Callable[[list[port.Directive], port.OptionsMap, Sequence[str]], list[precision.RoundingWarning]] = (
        precision.of_disk
    )
    observe: Callable[[LedgerSet], Fingerprint] = _observe
    fs: FileSystem = field(default_factory=for_this_os)
    store: pb.Store = field(default_factory=SqliteStore)
    tag: Callable[[], str] = pb.new_tag


Target = publishing.Target
"""Where and as of when the result is published (shared with the open, `publishing.Target`)."""


@dataclass(frozen=True, slots=True)
class Answer:
    """`loaded` (Beancount's result for the disk as `fingerprint` observed it; `published` says what became of it),
    `moved` (`fingerprint` names the set to try with next), `fresh` (the published generation already describes the
    disk: nothing loaded; `published` is it) or `paused` (an unfinished save's `.open` journal at the re-check: not
    published). `entries` and `errors`: the counts of the load's result (`loaded` only, else 0). `cleaned`: what the
    cleanup did (None when it did not run)."""

    outcome: Outcome
    fingerprint: Fingerprint
    entries: int = 0
    errors: int = 0
    published: pb.Published | pb.NotPublished | None = None
    cleaned: pb.Cleaned | None = None
    refusal: load_failure.Reason | None = None
    refused: str | None = None
    line: int | None = None


def _used(entries: list[port.Directive], options: port.OptionsMap, modules: imports.Modules) -> LedgerSet:
    """What the load used of the disk, as a ledger set (its globs are not known: the module's limit), with the
    modules its imports looked up (`imports`, S0099)."""
    roots = documents_roots(options) if options.get("documents") else []
    docs = [e.filename for e in entries if isinstance(e, port.Document)]
    return LedgerSet(
        str(options["filename"]), tuple(options["include"]), (), tuple(roots), tuple(docs), tuple(sorted(modules))
    )


def _read_by(observed: Fingerprint, used: LedgerSet) -> LedgerSet:
    """The set a loaded result is stamped with (publish.md §8): the files, documents roots and `document` files the
    load used (every `document` file by itself, also one whose path starts with a used root: the root's listing need not
    name it, a `..` in its path or a folder that cannot be listed; S0088 round 1, red team F2), and of the hint's
    `include` globs (a plain path is one too) those whose matches, as observed, include a file the load read, or that
    matched nothing (Beancount's loader does not report its patterns: an include the ledger dropped matched only files
    the load no longer reads, and goes; one that matches nothing stays, since it may still be in the ledger)."""
    roots = tuple(sorted(set(used.documents)))
    files = tuple(sorted(set(used.files)))
    read = set(files)
    globs = tuple(g for g, matches in observed.globs if not matches or read & set(matches))
    document_files = tuple(sorted(set(used.document_files)))  # each one itself (S0088 round 1, red team F2)
    return LedgerSet(observed.root, files, globs, roots, document_files, tuple(sorted(set(used.modules))))


def _listed(observed: Fingerprint) -> frozenset[str]:
    """Every file a documents root's listing named in `observed`, by the name `os.walk` joins to the root's own string
    (`root/...`), the same join Beancount's documents step makes for the `Document` entries it creates (S0070 r1 F1)."""
    return frozenset(
        posixpath.join(root, name) for root, listing in observed.documents if listing is not None for name, _ in listing
    )


def _wider(a: LedgerSet, b: LedgerSet, listed: frozenset[str]) -> LedgerSet | None:
    """`a` widened by what `b` names outside it, or None when `b` names nothing outside it. A `document` file is in `a`
    when `a` names it, or when the listing of one of `a`'s documents roots observed before the load named it
    (`listed`); never by its path alone (BL-0217, S0088 red team r2 F1): a file in a folder under the root that can be
    entered but not listed, or with a `..` in its path, starts with the root's path but no listing names it, so the
    hint never observed it, and a load that used it is a moved disk (such a file deleted during the load was stamped
    only after it, and the generation stayed fresh). Each one counts by itself, as `_read_by` and
    `publishing.within` count it."""
    files = set(b.files) - set(a.files)
    documents = set(b.documents) - set(a.documents)
    document_files = {f for f in set(b.document_files) - set(a.document_files) if f not in listed}
    modules = set(b.modules) - set(a.modules)  # code the hint never observed (S0099)
    if not (files or documents or document_files or modules):
        return None
    return LedgerSet(
        a.root,
        (*a.files, *sorted(files)),
        a.globs,
        (*a.documents, *sorted(documents)),
        (*a.document_files, *sorted(document_files)),
        (*a.modules, *sorted(modules)),
    )


@tla_action("StartPublish", "P_Obs", "P_Read", model="mirror-publish")
def run(ports: Ports, hint: Fingerprint, job: Job, target: Target) -> Answer:
    """The module's docstring: reads the ledger; writes only the workspace's mirror folder in the app's cache."""

    def observe(fp: Fingerprint) -> Fingerprint:
        return ports.observe(fp.ledger_set)

    with pb.open_folder(ports.fs, target.app_cache, target.workspace_key) as d:  # step 0 (publish.md §4 step 1)
        cleaned = pb.clean(d, ports.store, target.now_ns)
        if publishing.unfinished(ports.fs, target):  # engine-jobs §7: before any result (S0088)
            return Answer("paused", hint, cleaned=cleaned)
        cur = publishing.fresh_current(d, ports.store, cleaned, observe, ports.fs, hint.root, target, hint.ledger_set)
        if cur is not None:
            done = pb.Published(cur.gen, cur.meta.ledger_version)
            return Answer("fresh", decode(cur.meta.fingerprint.encode("ascii")), published=done, cleaned=cleaned)
        ledger_set = hint.ledger_set
        before = ports.observe(ledger_set)  # step 1
        job.progress(PHASE)
        parsed: list[str] = []
        modules: dict[ModuleKey, Module | None] = {}
        try:
            with imports.recording(modules):  # what its imports read (S0099, BL-0124)
                result = ports.load(ledger_set.root, parsed)  # step 2: Beancount's own loader (user code)
        except (OSError, port.EncryptedFileMet):
            raise
        except Exception as e:  # Beancount's own load raised on the ledger: refused, never raised
            if not load_failure.beancounts(e):
                raise
            f = load_failure.of(e, ledger_set.root)
            # A second look at what the failed load read (S0098 round 2, verifier F2): a file it parsed outside the
            # set was never observed before the load, so the set widened by it is tried again; a disk changed since
            # the load is tried again too. Only an unchanged set is refused.
            outside = sorted(set(parsed) - set(ledger_set.files))
            code = sorted(set(modules) - set(ledger_set.modules))  # code it imported that the set never observed
            if outside or code:
                widened = LedgerSet(
                    ledger_set.root,
                    (*ledger_set.files, *outside),
                    ledger_set.globs,
                    ledger_set.documents,
                    ledger_set.document_files,
                    (*ledger_set.modules, *code),
                )
                return Answer("moved", ports.observe(widened), cleaned=cleaned)
            again = ports.observe(ledger_set)
            if again != before:
                return Answer("moved", again, cleaned=cleaned)
            return Answer("refused", hint, cleaned=cleaned, refusal=f.reason, refused=f.path, line=f.line)
        entries, errors, options = result
        # Finzytrack's warnings on numbers Beancount rounded (`precision`): the files read again before the set is
        # observed again, so a change in between is a moved disk; a file gone or unreadable meanwhile is one too.
        try:
            warnings = ports.warnings(entries, options, parsed)
        except OSError:
            return Answer("moved", ports.observe(ledger_set), cleaned=cleaned)
        after = ports.observe(ledger_set)  # step 3
        used = _used(entries, options, modules)
        wider = _wider(ledger_set, used, _listed(before))
        if wider is not None:
            return Answer("moved", ports.observe(wider), cleaned=cleaned)
        if after != before:
            return Answer("moved", after, cleaned=cleaned)
        # The generation is stamped with what the load read (publish.md §8; round 1, verifier V1), not the hint: a
        # hint can name files the ledger no longer includes (the queued rebuild of an earlier save), and a stamp
        # naming them would make the gate refuse every save once such a file changes (`_read_by` says which globs
        # stay). Observed now, then the hint's set once more: unchanged, so the stamp describes the state the load
        # read.
        stamped_set = _read_by(after, used)
        stamp = ports.observe(stamped_set)
        again = ports.observe(ledger_set)
        if again != after:
            return Answer("moved", again, cleaned=cleaned)
        seen: list[Fingerprint] = []
        check = publishing.recheck(ports.fs, ports.observe, stamp, target)

        def recheck() -> pb.Recheck:  # publish.md §4 step 7, engine-jobs §7
            verdict = check()
            if verdict == "moved":
                seen.append(ports.observe(ledger_set))  # the next try starts from the hint's set as it is now
            return verdict

        out = pb.publish(
            d,
            cleaned,
            ports.store,
            (entries, [*errors, *warnings], options),
            stamp,
            floor=target.floor,
            today=target.today,
            built_at=target.built_at,
            engine_build=target.engine_build,
            recheck=recheck,
            by="rebuild",
            tag=ports.tag(),
        )
    if isinstance(out, pb.NotPublished) and out.why == "moved":
        return Answer("moved", seen[-1], cleaned=cleaned)
    if isinstance(out, pb.NotPublished) and out.why == "paused":
        return Answer("paused", after, cleaned=cleaned)
    return Answer("loaded", stamp, len(entries), len(errors), published=out, cleaned=cleaned)
