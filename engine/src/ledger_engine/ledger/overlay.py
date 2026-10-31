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
  where the replica's succeeded (R4 could never run; S0067, BL-0172). A plugin that decrypts a side file meets the
  same refusal as R4's load (`beancount_port.run_transformations`); one raised while a plugin is imported refuses the
  load.
- **The policy** is asked about each file before the load touches it (before its existence is checked or its first
  bytes read by the encrypted-file test) and, once all files are parsed and before any plugin runs, about each user
  plugin, `pythonpath` directory and documents root (`policy.LoadPolicy`).
- **A candidate the load never reaches** is refused: validating the set without it would validate the wrong text
  (INV-22). A candidate for a file that does not exist yet is reached through a plain `include` of its path; a glob
  matches only files on disk, as in Beancount, so a new file reached only through a glob is refused (BL-0117).
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
  known after the plugins ran; their record comes from Beancount's own check (`_plugin_documents`).
- **The documents step reads the disk**, as in Beancount's own load. A file a save creates under a documents root
  is not in the prediction: the preview may miss the `document` entry it makes, and R4 then finds a difference and
  shows its notice (S0068 removed S0067's listing swap, whose names a user plugin could see; spec §15).

A load holds `sys.path` and the plugin modules of the process while it runs; engine jobs run one at a time
(ADR-0064). The save runs it inside its engine job (S0063); the mirror build will (TODO(BL-0113)).
"""

from __future__ import annotations

import glob
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from os import path
from typing import Literal, cast

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import fingerprint as fp
from ledger_engine.ledger.core.ledger_set import Absent, FileState, Fingerprint
from ledger_engine.ledger.policy import LoadPolicy

type RefusalReason = Literal[
    "encrypted",
    "not-reached",
    "read-refused",
    "plugin-refused",
    "pythonpath-refused",
    "documents-refused",
]

ENCRYPTED_SUFFIXES = (".gpg", ".asc")
DOCUMENTS_PLUGIN = "beancount.ops.documents"


@dataclass(frozen=True, slots=True)
class Loaded:
    """Beancount's result: the booked, transformed and validated entries, every error, and the options map; and
    what the load read (`fingerprint`: observe its `ledger_set` again to learn whether the next load would differ)."""

    entries: list[port.Directive]
    errors: list[port.BeancountError]
    options: port.OptionsMap
    fingerprint: Fingerprint


@dataclass(frozen=True, slots=True)
class LoadRefused:
    """The load did not happen: `subject` is the file, plugin or directory it refused."""

    reason: RefusalReason
    subject: str


type LoadResult = Loaded | LoadRefused


class NotAbsoluteError(ValueError):
    """A caller's error: the root and every candidate are named by absolute paths."""

    def __init__(self, given: str) -> None:
        super().__init__(f"a ledger path must be absolute: {given!r}")


def load(root: str, overlay: Mapping[str, bytes], policy: LoadPolicy) -> LoadResult:
    """Load the ledger set rooted at `root` (an absolute path) with `overlay`'s bytes in place of the files they
    name (absolute paths), under `policy`."""
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

    seen = _Seen()
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
    documents = {d: fp.listing(d) for d in _documents_roots(options)}
    document_files = {e.filename: fp.document_file(e.filename) for e in entries if isinstance(e, port.Document)}

    # loader._load, after _parse_recursive.
    port.sort_entries(entries)
    entries, balance_errors = port.book(entries, options)
    parse_errors.extend(balance_errors)
    try:
        entries, errors = port.run_transformations(entries, parse_errors, options)
    except port.EncryptedFileMet as e:  # raised while a plugin was imported (its calls' errors are Beancount's)
        return LoadRefused("encrypted", e.path)
    listed = {path.join(d, name) for d, names in documents.items() if names is not None for name in names}
    document_files.update(_plugin_documents(entries, errors, options, listed | set(document_files)))
    errors.extend(port.validate(entries, options))
    options["input_hash"] = port.input_hash(options["include"])
    fingerprint = Fingerprint.of(path.normpath(root), seen.files, seen.globs, documents, document_files)
    return Loaded(entries, errors, options, fingerprint)


@dataclass(slots=True)
class _Seen:
    """What `_parse_recursive` read and looked for; the first answer recorded for a path or a glob stays (an include
    that matched nothing and then matched in the same load keeps its empty answer, so the next observation differs:
    the safe side)."""

    files: dict[str, FileState] = field(default_factory=dict[str, FileState])
    globs: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


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
        if on_disk:
            with open(filename, "rb") as f:
                data = f.read()
        else:
            data = candidates[filename]
        record.files.setdefault(filename, fp.content(data))
        src_entries, src_errors, src_options = port.parse_string(data, filename)
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
    (a missing `.asc` makes it raise)."""
    ext = path.splitext(filename)[1]
    if ext == ".gpg":
        return True
    return ext == ".asc" and (not path.exists(filename) or port.is_encrypted_file(filename))


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
    for resolved in _documents_roots(options):
        if not policy.may_scan_documents(resolved):
            return LoadRefused("documents-refused", resolved)
    return None


def _documents_roots(options: port.OptionsMap) -> list[str]:
    """Each documents root as beancount.ops.documents.find_documents resolves it. (Only here: a root that does not
    exist leaves `filename` unset, and then no documents option either.)"""
    roots: list[str] = []
    for raw in options["documents"]:
        directory = path.normpath(str(raw))
        base = path.dirname(options["filename"])
        roots.append(directory if path.isabs(directory) else path.abspath(path.normpath(path.join(base, directory))))
    return roots
