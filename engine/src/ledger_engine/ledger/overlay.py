"""The overlay load: Beancount's full load of a ledger set, with candidate bytes in place of some files (ADR-0010).

Mirrors Beancount 3.2.0's `beancount/loader.py`: `_parse_recursive` (the only loader step that touches files) and the
sequence of `_load` after it. A file with candidate bytes is parsed from them (`parse_string` with its path as the
report filename, so entries carry the same metadata and includes resolve from the same folder); every other file
from disk (`parse_file`). Then Beancount's own sort, `booking.book`, `run_transformations` and `validation.validate`,
unchanged. `load(root, {}, policy)` is the plain load of the disk, and equals `loader.load_file(root)`; the
equivalence and the pin of the mirrored source are `engine/tests/ledger/test_overlay.py` (guardrails 1 and 2). A
Beancount upgrade re-mirrors this module first (ADR-0004).

Where it differs from Beancount, on purpose (each refuses the whole load, EP-13; nothing is partly loaded):
- **Encrypted files** in the include tree, the root included, are refused, never decrypted (ADR-0059); a candidate
  for a `.gpg` or `.asc` file (any letter case) is refused before anything is read (ADR-0010 guardrail 4). A missing
  `.gpg` or `.asc` include is the loader's "does not exist" error, like any missing file (Beancount would try to
  decrypt or open it and fail).
- **The policy** is asked about each file before the load touches it (before its existence is checked or its first
  bytes read by the encrypted-file test) and, once all files are parsed and before any plugin runs, about each user
  plugin, `pythonpath` directory and documents root (`policy.LoadPolicy`).
- **A candidate the load never reaches** is refused: validating the set without it would validate the wrong text
  (INV-22). A candidate for a file that does not exist yet is reached through a plain `include` of its path; a glob
  matches only files on disk, as in Beancount, so a new file reached only through a glob is refused (BL-0117).
- **No pickle cache**, no `log_timings`, no `encoding` argument (the engine passes none).

A load holds `sys.path` and the plugin modules of the process while it runs; engine jobs run one at a time
(ADR-0064). Nothing calls this over the protocol yet: the mirror build runs it inside an engine job
(TODO(BL-0109)).
"""

from __future__ import annotations

import glob
from collections.abc import Mapping
from dataclasses import dataclass
from os import path
from typing import Literal

from ledger_engine.ledger import beancount_port as port
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


@dataclass(frozen=True, slots=True)
class Loaded:
    """Beancount's result: the booked, transformed and validated entries, every error, and the options map."""

    entries: list[port.Directive]
    errors: list[port.BeancountError]
    options: port.OptionsMap


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

    parsed = _parse_recursive(path.normpath(root), candidates, policy)
    if isinstance(parsed, LoadRefused):
        return parsed
    entries, parse_errors, options = parsed
    unreached = sorted(set(candidates) - set(options["include"]))
    if unreached:
        return LoadRefused("not-reached", unreached[0])
    refused = _ask_before_running(options, policy)
    if refused is not None:
        return refused

    # loader._load, after _parse_recursive.
    port.sort_entries(entries)
    entries, balance_errors = port.book(entries, options)
    parse_errors.extend(balance_errors)
    entries, errors = port.run_transformations(entries, parse_errors, options)
    errors.extend(port.validate(entries, options))
    options["input_hash"] = port.input_hash(options["include"])
    return Loaded(entries, errors, options)


def _parse_recursive(root: str, candidates: Mapping[str, bytes], policy: LoadPolicy) -> port.ParseResult | LoadRefused:
    """loader._parse_recursive for a single root file, with the overlay, the policy and the refusals above."""
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
        if on_disk and not path.exists(filename):
            parse_errors.append(port.load_error(f'File "{filename}" does not exist'))
            continue
        if on_disk and port.is_encrypted_file(filename):
            return LoadRefused("encrypted", filename)
        seen.add(filename)
        if on_disk:
            src_entries, src_errors, src_options = port.parse_file(filename)
        else:
            src_entries, src_errors, src_options = port.parse_string(candidates[filename], filename)
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
            matched = glob.glob(search, recursive=True)
            if not matched and not glob.has_magic(search) and path.normpath(search) in candidates:
                matched = [search]  # a new file, named by its path: after the write, the glob finds it
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


def _ask_before_running(options: port.OptionsMap, policy: LoadPolicy) -> LoadRefused | None:
    """The policy's questions about what the transformations would run and scan, before any of it happens."""
    for name, _config in options["plugin"]:
        if not policy.may_run_plugin(name):
            return LoadRefused("plugin-refused", name)
    for directory in options["pythonpath"]:
        if not policy.may_add_pythonpath(directory):
            return LoadRefused("pythonpath-refused", directory)
    for raw in options["documents"]:
        directory = path.normpath(str(raw))
        # As beancount.ops.documents.find_documents resolves a documents root. (Only here: a root that does not
        # exist leaves `filename` unset, and then no documents option either.)
        base = path.dirname(options["filename"])
        resolved = directory if path.isabs(directory) else path.abspath(path.normpath(path.join(base, directory)))
        if not policy.may_scan_documents(resolved):
            return LoadRefused("documents-refused", resolved)
    return None
