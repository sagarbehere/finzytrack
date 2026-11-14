"""The overlay load: Beancount's full load of a ledger set, with candidate bytes in place of some files (ADR-0010).

Mirrors Beancount 3.2.0's `beancount/loader.py`: `_parse_recursive` (the only loader step that touches files) and the
sequence of `_load` after it. A file with candidate bytes is parsed from them (`parse_string` with its path as the
report filename, so entries carry the same metadata and includes resolve from the same folder); every other file
from the bytes this module read from disk, the same way (see below). Then Beancount's own sort, `booking.book`,
`run_transformations` and `validation.validate`, unchanged. `load(root, {}, policy)` is the plain load of the
disk, and equals `loader.load_file(root)`; the equivalence and the pin of the mirrored source are
`engine/tests/ledger/test_overlay.py` (guardrails 1 and 2). A Beancount upgrade re-mirrors this module first (ADR-0004).

Where it differs from Beancount, on purpose (each refuses the whole load, EP-13; nothing is partly loaded):
- **Encrypted files** in the include tree, the root included, are refused, never decrypted (ADR-0059); a candidate
  for a `.gpg` or `.asc` file (any letter case) is refused before anything is read (ADR-0010 guardrail 4). A missing
  (or dangling) `.gpg` or `.asc` file is refused too, before its existence is checked: Beancount asks whether a file
  is encrypted first, so it would try to decrypt a missing `.gpg` and open a missing `.asc`, and its load would fail
  where the replica's succeeded (S0067, BL-0172). A plugin that decrypts a side file meets the same refusal as the
  rebuild's real load (`beancount_port.run_transformations`); one raised while a plugin is imported refuses the
  load.
- **The policy** is asked about each file before the load touches it (before its existence is checked or its first
  bytes read by the encrypted-file test) and, once all files are parsed and before any plugin runs, about each user
  plugin, `pythonpath` directory and documents root (`policy.LoadPolicy`).
- **A candidate the load never reaches** is refused: validating the set without it would validate the wrong text
  (INV-22). A candidate for a file that does not exist yet is reached through a plain `include` of its path; a glob
  matches only files on disk, as in Beancount, so a new file reached only through a glob is refused (BL-0117).
- **Beancount's own load raising** on the ledger (its parse, booking, a plugin's import or its validation, as
  bean-check fails on the same ledger) is refused, never raised: `number-failed` or `beancount-failed`, naming the
  file and line of the transaction Beancount was working on when known (`load_failure`; S0049's contract, BL-0123),
  with what the load had read (`LoadRefused.read`, which the open observes again). A disk error stays the caller's
  (`OSError`), and so does a fault of the engine's own code or a full memory (`load_failure.beancounts`).
- **Its own decimal context**: every load runs in `beancount_port.BEANCOUNT_CONTEXT` (Python's default, as bean-check)
  and leaves the caller's as it was, so a plugin that changes the context changes only its own load.
- **Warnings on numbers Beancount rounded** (`precision`; owner, 2026-11-10, "Follow Beancount"): the load keeps
  Beancount's own 28-digit numbers and lists a warning per line it rounded in `Loaded.warnings`, apart from
  `Loaded.errors`, which stay exactly Beancount's (`Loaded.reported` gives both, as the mirror lists them).
- **No pickle cache**, no `log_timings`, no `encoding` argument (the engine passes none).
- **Every file is read once, by us, and parsed from those bytes** (`parse_string` with its path as the report
  filename, as `parse_file` does after opening it), so the load's fingerprint (`Loaded.fingerprint`, S0051) is the
  SHA-256 of exactly the bytes parsed: an editor's save between a read and the parse cannot make the fingerprint
  describe bytes the load did not read (ADR-0008 step 4, ADR-0013). Each `include`'s glob answer is the one
  followed, recorded under the string the glob was asked (a plain path is globbed too, and its `lexists` is asked
  of the path as written: a `..` after a link resolves elsewhere than the normalized path the loader then opens);
  each path the loader found missing is recorded absent; each documents root is listed after the policy allowed it
  and before Beancount scans it, and the file of each `document` directive is checked before Beancount checks it,
  so a change during the load makes the next observation differ. The `document` entries a plugin adds are only
  known after the plugins ran; their record comes from Beancount's own check (`_plugin_documents`). Each module the
  load's imports look up is recorded with the folders searched and the file found, hashed before it is loaded
  (`imports`, S0099, BL-0124).
- **The documents step reads the disk**, as in Beancount's own load. A file a save creates under a documents root
  is not in the preview: the preview may miss the `document` entry it makes, and the rebuild after the save shows it
  (S0068 removed S0067's listing swap, whose names a user plugin could see; spec §15).

A load holds `sys.path` and the plugin modules of the process while it runs; engine jobs run one at a time
(ADR-0064). The preview runs it inside its engine job (`ledger.preview`, S0116), the open too (S0088); the save runs
no load since S0070 (ADR-0068 D1).
"""

from __future__ import annotations

import errno
import glob
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import path
from typing import Literal, cast

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as fp
from ledger_engine.ledger import imports, load_failure, precision
from ledger_engine.ledger.core.ledger_set import Absent, FileState, Fingerprint, Module, ModuleKey
from ledger_engine.ledger.policy import LoadPolicy

type RefusalReason = Literal[
    "encrypted",
    "not-reached",
    "read-refused",
    "plugin-refused",
    "pythonpath-refused",
    "documents-refused",
    "number-failed",
    "beancount-failed",
]

ENCRYPTED_SUFFIXES = (".gpg", ".asc")
DOCUMENTS_PLUGIN = "beancount.ops.documents"


@dataclass(frozen=True, slots=True)
class Loaded:
    """Beancount's result: the booked, transformed and validated entries, every error, and the options map; and
    what the load read (`fingerprint`: observe its `ledger_set` again to learn whether the next load would differ);
    and Finzytrack's own warnings on numbers Beancount rounded (`precision`, BL-0123), kept apart from Beancount's
    errors so `errors` stays exactly Beancount's. Whoever shows the errors shows both (`reported`)."""

    entries: list[port.Directive]
    errors: list[port.BeancountError]
    options: port.OptionsMap
    fingerprint: Fingerprint
    warnings: list[port.BeancountError] = field(default_factory=list[port.BeancountError])

    @property
    def reported(self) -> list[port.BeancountError]:
        """Beancount's errors, then the warnings: what the mirror's `ledger_errors` lists."""
        return [*self.errors, *self.warnings]


@dataclass(frozen=True, slots=True)
class LoadRefused:
    """The load did not happen: `subject` is the file, plugin or directory it refused. For `number-failed` and
    `beancount-failed` (Beancount's own load raised, `load_failure`), `line` is the line in `subject` when known."""

    reason: RefusalReason
    subject: str
    line: int | None = None
    read: Fingerprint | None = field(default=None, compare=False)
    """For `number-failed` and `beancount-failed`: what the load had read when Beancount failed."""


type LoadResult = Loaded | LoadRefused


class NotAbsoluteError(ValueError):
    """A caller's error: the root and every candidate are named by absolute paths."""

    def __init__(self, given: str) -> None:
        super().__init__(f"a ledger path must be absolute: {given!r}")


def load(root: str, overlay: Mapping[str, bytes], policy: LoadPolicy) -> LoadResult:
    """Load the ledger set rooted at `root` (an absolute path) with `overlay`'s bytes in place of the files they
    name (absolute paths), under `policy`. In Beancount's own decimal context, the caller's restored after
    (`beancount_port.beancount_context`); what its imports read is recorded for its fingerprint (`imports`)."""
    seen = _Seen()
    with port.beancount_context(), imports.recording(seen.modules):
        return _load(root, overlay, policy, seen)


def _load(root: str, overlay: Mapping[str, bytes], policy: LoadPolicy, seen: _Seen) -> LoadResult:
    if not path.isabs(root):
        raise NotAbsoluteError(root)
    candidates: dict[str, bytes] = {}
    for name, data in overlay.items():
        if not path.isabs(name):
            raise NotAbsoluteError(name)
        candidates[path.normpath(name)] = data
    for name in sorted(candidates):
        if name.lower().endswith(ENCRYPTED_SUFFIXES):
            return LoadRefused("encrypted", name)

    parsed = _parse_recursive(path.normpath(root), candidates, policy, seen)
    if isinstance(parsed, LoadRefused):
        return parsed
    entries, parse_errors, options = parsed
    unreached = sorted(set(candidates) - set(options["include"]))
    if unreached:
        return LoadRefused("not-reached", unreached[0])
    refused = _ask_before_running(options, policy)
    if refused is not None:
        return refused
    documents = {d: fp.listing(d) for d in documents_roots(options)}
    document_files = {e.filename: fp.document_file(e.filename) for e in entries if isinstance(e, port.Document)}

    # loader._load, after _parse_recursive. Each of Beancount's steps that can raise on the ledger refuses instead
    # (`load_failure`); a disk error stays the caller's.
    try:
        port.sort_entries(entries)
        entries, balance_errors = port.book(entries, options)
    except OSError:
        raise
    except Exception as e:  # Beancount's booking raised on the ledger: refused, never raised
        if not load_failure.beancounts(e):
            raise
        return _failed(e, root, seen, documents, document_files)
    parse_errors.extend(balance_errors)
    try:
        entries, errors = port.run_transformations(entries, parse_errors, options)
    except port.EncryptedFileMet as e:  # raised while a plugin was imported (its calls' errors are Beancount's)
        return LoadRefused("encrypted", e.path)
    except OSError:
        raise
    except Exception as e:  # a plugin raised while it was imported: refused, never raised
        if not load_failure.beancounts(e):
            raise
        return _failed(e, root, seen, documents, document_files)
    listed = {path.join(d, name) for d, names in documents.items() if names is not None for name in names}
    document_files.update(_plugin_documents(entries, errors, options, listed | set(document_files)))
    try:
        errors.extend(port.validate(entries, options))
    except OSError:
        raise
    except Exception as e:  # Beancount's checks raised on an entry: refused, never raised
        if not load_failure.beancounts(e):
            raise
        return _failed(e, root, seen, documents, document_files)
    options["input_hash"] = port.input_hash(options["include"])
    fingerprint = Fingerprint.of(path.normpath(root), seen.files, seen.globs, documents, document_files, seen.modules)
    warnings: list[port.BeancountError] = [*precision.warnings(seen.data, entries, options)]
    return Loaded(entries, errors, options, fingerprint, warnings)


def _failed(
    error: Exception,
    subject: str,
    seen: _Seen,
    documents: Mapping[str, Mapping[str, bool] | None],
    document_files: Mapping[str, bool],
    root: str | None = None,
) -> LoadRefused:
    """The refusal of a load Beancount failed on, with the fingerprint of what the load had read so far (the caller
    observes it again: a disk that changed meanwhile is a moved disk, not a refusal; S0098 round 1, data-safety F2)."""
    failure = load_failure.of(error, path.normpath(subject))
    read = Fingerprint.of(
        path.normpath(root or subject), seen.files, seen.globs, documents, document_files, seen.modules
    )
    return LoadRefused(failure.reason, failure.path, failure.line, read)


@dataclass(slots=True)
class _Seen:
    """What `_parse_recursive` read and looked for; the first answer recorded for a path or a glob stays (an include
    that matched nothing and then matched in the same load keeps its empty answer, so the next observation differs:
    the safe side)."""

    files: dict[str, FileState] = field(default_factory=dict[str, FileState])
    globs: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    data: dict[str, bytes] = field(default_factory=dict[str, bytes])  # every parsed file's bytes, for `precision`
    modules: dict[ModuleKey, Module | None] = field(default_factory=dict[ModuleKey, Module | None])  # `imports`


def _parse_recursive(
    root: str, candidates: Mapping[str, bytes], policy: LoadPolicy, record: _Seen
) -> port.ParseResult | LoadRefused:
    """loader._parse_recursive for a single root file, with the overlay, the policy, the refusals and the record
    above."""
    entries: list[port.Directive] = []
    parse_errors: list[port.BeancountError] = []
    options: port.OptionsMap | None = None
    other_options: list[port.OptionsMap] = []
    stack = [root]
    seen: set[str] = set()

    while stack:
        source = stack.pop(0)
        is_top_level = options is None
        filename = path.normpath(source)
        on_disk = filename not in candidates
        if filename in seen:
            parse_errors.append(port.load_error(f'Duplicate filename parsed: "{filename}"'))
            continue
        # The policy first, before the path is touched: not even its existence is checked, nor its first bytes
        # read by the encrypted-file test.
        if not policy.may_read(filename):
            return LoadRefused("read-refused", filename)
        # Beancount asks "encrypted?" before "exists?": a missing `.gpg` it would decrypt, a missing `.asc` it would
        # open; either fails its load, so it is refused here too, never reported missing (S0067, BL-0172).
        if on_disk and _beancount_decrypts(filename):
            return LoadRefused("encrypted", filename)
        if on_disk and not path.exists(filename):
            record.files.setdefault(filename, Absent())
            parse_errors.append(port.load_error(f'File "{filename}" does not exist'))
            continue
        seen.add(filename)
        data = fp.read_regular(filename) if on_disk else candidates[filename]  # never blocks on a FIFO (S0102)
        record.files.setdefault(filename, fp.content(data))
        record.data.setdefault(filename, data)
        try:
            src_entries, src_errors, src_options = port.parse_string(data, filename)
        except OSError:
            raise
        except Exception as e:  # Beancount's parser raised on the file: refused, never raised
            if not load_failure.beancounts(e):
                raise
            return _failed(e, filename, record, {}, {}, root=root)
        cwd = path.dirname(filename)

        entries.extend(src_entries)
        parse_errors.extend(src_errors)
        if is_top_level:
            options = src_options
        else:
            other_options.append(src_options)

        expanded: list[str] = []
        for include in src_options["include"]:
            search = include if path.isabs(include) else path.join(cwd, include)
            matched = fp.matches(search)
            if not matched and not glob.has_magic(search) and path.normpath(search) in candidates:
                matched = [search]  # a new file, named by its path: after the write, the glob finds it
            # Under the string the glob was asked, as written: for a plain path the glob asks `lexists` of it, and a
            # `..` after a link resolves elsewhere than the normalized path the loader then asks `exists` of.
            record.globs.setdefault(search, list(matched))
            if matched:
                expanded.extend(matched)
            else:
                parse_errors.append(port.load_error(f'File glob "{include}" does not match any files'))
        for include in expanded:
            stack.append(path.normpath(include if path.isabs(include) else path.join(cwd, include)))

    if options is None:
        options = port.default_options()
    options["include"] = sorted(seen)
    options = port.aggregate_options(options, other_options)
    return entries, parse_errors, options


def _beancount_decrypts(filename: str) -> bool:
    """Whether Beancount's loader would decrypt `filename` (or fail trying) instead of reading it: its own test,
    `is_encrypted_file`, which names a `.gpg` file by its name alone and opens a `.asc` one to look for a PGP header
    (a missing `.asc` makes it raise). Its open blocks on a FIFO: a `.asc` that is there and is no regular file is never
    handed to it and raises as the read of it would (`EINVAL`; S0102, BL-0245)."""
    ext = path.splitext(filename)[1]
    if ext == ".gpg":
        return True
    if ext != ".asc":
        return False
    if not path.exists(filename):
        return True
    if not fp.regular(filename):
        raise OSError(errno.EINVAL, "not a regular file", filename)
    return port.is_encrypted_file(filename)


def _plugin_documents(
    entries: list[port.Directive], errors: list[port.BeancountError], options: port.OptionsMap, known: set[str]
) -> dict[str, bool]:
    """The file of each `document` entry a plugin added (not a directive's, not a documents root's), with whether it
    existed when Beancount checked it, for those Beancount checks.

    Beancount checks them in its documents plugin (`verify_document_files_exist`), so only when that plugin runs
    after the plugin that added them: never in the default mode (it runs first, `loader.PLUGINS_PRE`) unless the
    ledger lists it again, and in raw mode where the ledger lists it; listed with a config, it fails before checking.
    Its "File does not exist" error says which files it found missing. Which plugin added an entry is not known:
    - no listed documents plugin: none was checked, so none is recorded (the load does not depend on them);
    - no other plugin after the last one: each was checked, and Beancount's errors give each answer, so a change
      after its check makes the next observation differ;
    - another plugin after it: an entry with an error was checked and missing; any other is asked now, right after
      the plugins (it may not have been checked, and recording it present when it is missing would make an unchanged
      set observe changed forever). A file Beancount found that vanishes between its check and this question is the
      one change hidden (S0051 journal, round 2).
    A `pathlib.Path` counts by its text, as in Beancount's error. Any other name (a plugin may set any value) is never
    asked: Beancount's own check fails on it, and its validation raises."""
    names: list[str] = []
    for entry in entries:
        filename: object = entry.filename if isinstance(entry, port.Document) else None
        if isinstance(filename, str | os.PathLike):
            name = cast(object, os.fspath(filename))  # a bytes path gives bytes
            if isinstance(name, str) and name not in known:
                names.append(name)
    plugins = [(name, config) for name, config in options["plugin"]]
    checks = [i for i, (name, config) in enumerate(plugins) if name == DOCUMENTS_PLUGIN and config is None]
    if not names or not checks:
        return {}
    messages = {m for e in errors if isinstance(m := getattr(e, "message", None), str)}
    every_one_checked = all(name == DOCUMENTS_PLUGIN for name, _ in plugins[checks[-1] + 1 :])
    record: dict[str, bool] = {}
    for name in names:
        if f'File does not exist: "{name}"' in messages:
            record[name] = False
        else:
            record[name] = True if every_one_checked else fp.document_file(name)
    return record


def _ask_before_running(options: port.OptionsMap, policy: LoadPolicy) -> LoadRefused | None:
    """The policy's questions about what the transformations would run and scan, before any of it happens."""
    for name, _config in options["plugin"]:
        if not policy.may_run_plugin(name):
            return LoadRefused("plugin-refused", name)
    for directory in options["pythonpath"]:
        if not policy.may_add_pythonpath(directory):
            return LoadRefused("pythonpath-refused", directory)
    for resolved in documents_roots(options):
        if not policy.may_scan_documents(resolved):
            return LoadRefused("documents-refused", resolved)
    return None


def documents_roots(options: port.OptionsMap) -> list[str]:
    """Each documents root as beancount.ops.documents.find_documents resolves it. (Only here: a root that does not
    exist leaves `filename` unset, and then no documents option either.)"""
    roots: list[str] = []
    for raw in options["documents"]:
        directory = path.normpath(str(raw))
        base = path.dirname(options["filename"])
        roots.append(directory if path.isabs(directory) else path.abspath(path.normpath(path.join(base, directory))))
    return roots
