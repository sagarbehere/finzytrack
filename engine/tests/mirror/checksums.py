"""The mirror's per-table checksums over the corpus ledgers (BL-0057, S0096): a change detector, not a check of
correctness.

Every ledger root of the corpus (`roots`) is copied to a temp folder, loaded with the overlay load, and its mirror's
rows (`ledger_engine.mirror.build.tables`) are normalized and hashed per table. The committed snapshot is
`corpora/ledgers/mirror-checksums.json`, printed by `engine/scripts/gen_mirror_checksums.py` and checked by
`scripts/check_generated.py` (INV-19). Its values are the build's own output (EP-29: a characterization pin), so a
changed checksum says only that rows changed; the row tests and the INV-21 goldens say whether they are right. To
review a change, print a table's rows on the base and on the head (`gen_mirror_checksums.py --rows CASE TABLE`).

- **Roots, by behaviour** (`roots`): every case row of the write and parity corpora, the parity corpus's generated
  cases, every INV-21 case, the oracle's ledger, every root the v0.2.2 install's goldens name. `unread_files` holds
  the cover check: every ledger file under `corpora/` is read by some root's load or sits in a refused case.
- **Normalized** (`normalized`): the case folder's path becomes `<case>` in every text value, and Beancount's
  `input_hash` option (an MD5 over file names and modification times) becomes `<input_hash>`; the build date and the
  stamp are fixed. POSIX paths only (the engine runs on Linux and macOS in CI).
- **Order**: a table's rows are hashed sorted. SQL row order is not part of the surface; an order the spec defines is
  a column (`posting_id`, ...) and is hashed with its row. Beancount does not sort an include glob's matches, so the
  order of entries from different files on the same date and line follows the file system (BL-0234).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ledger_engine.ledger.overlay import Loaded, load
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror.build import BuildFailed, Stamp, Tables, tables
from ledger_engine.mirror.core import schema
from tests.ledger import parity

REPO = Path(__file__).resolve().parents[3]
CORPORA = REPO / "corpora"
LEDGERS = CORPORA / "ledgers"
INSTALLS = CORPORA / "workspaces" / "legacy"
SNAPSHOT = LEDGERS / "mirror-checksums.json"

TODAY = date(2026, 11, 10)
STAMP = Stamp("1/checksums", "checksums", "2026-11-10T00:00:00+00:00", "checksums", "open")
CASE = "<case>"
INPUT_HASH = "<input_hash>"
LEDGER_SUFFIXES = (".beancount", ".gpg", ".asc")


@dataclass(frozen=True, slots=True)
class Root:
    """One ledger root, copied into its own temp `folder`; `refused` is the encrypted file a refuse case names."""

    case: str
    root: Path
    folder: Path
    source: Path | None  # the corpus folder `folder` was copied from (None for a generated case)
    refused: Path | None = None


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def roots(tmp: Path) -> list[Root]:
    """Every corpus root, each copied under `tmp` (INV-24: never loaded in place)."""
    out: list[Root] = []
    for c in parity.corpus_cases(tmp / "parity") + parity.generated_cases(tmp / "parity-generated"):
        source = None if c.generated else parity.CORPUS / c.name
        out.append(Root(f"parity/{c.name}", c.root, c.folder, source, c.refused))
    for name, row in sorted(_toml(LEDGERS / "write" / "cases.toml").items()):
        dest = tmp / "write" / name
        shutil.copytree(LEDGERS / "write" / name, dest)
        refused = row.get("refused")
        root = dest / row.get("root", "main.beancount")
        out.append(
            Root(
                f"write/{name}",
                root,
                dest,
                LEDGERS / "write" / name,
                dest / refused if isinstance(refused, str) else None,
            )
        )
    for name in sorted(_toml(LEDGERS / "inv21" / "cases.toml")):
        dest = tmp / "inv21" / name
        shutil.copytree(LEDGERS / "inv21" / name, dest)
        out.append(Root(f"inv21/{name}", dest / "main.beancount", dest, LEDGERS / "inv21" / name))
    dest = tmp / "oracle" / "nasty"
    shutil.copytree(LEDGERS / "oracle" / "nasty", dest)
    out.append(Root("oracle/nasty", dest / "main.beancount", dest, LEDGERS / "oracle" / "nasty"))
    goldens = json.loads((INSTALLS / "goldens.json").read_text(encoding="utf-8"))
    for name, install in sorted(goldens["installs"].items()):
        dest = tmp / "installs" / name
        shutil.copytree(INSTALLS / name, dest)
        (dest / "_data").rename(dest / "data")  # the fixture stores `data/` as `_data/` (corpora/README.md)
        for rel in sorted(install["roots"]):
            out.append(Root(f"installs/{name}/{rel}", dest / rel, dest, INSTALLS / name))
    return out


def inventory() -> list[str]:
    """The case names `roots` must give, read from the corpus's own tables (claimed equals found)."""
    names = [f"parity/{n}" for n in parity.corpus_rows()]
    names += [
        f"parity/{n}"
        for n in ("example", "example-split", "example-custom-roots", "absolute-paths", "root-missing", "not-utf8")
    ]
    names += [f"write/{n}" for n in _toml(LEDGERS / "write" / "cases.toml")]
    names += [f"inv21/{n}" for n in _toml(LEDGERS / "inv21" / "cases.toml")]
    names += ["oracle/nasty"]
    goldens = json.loads((INSTALLS / "goldens.json").read_text(encoding="utf-8"))
    names += [f"installs/{n}/{rel}" for n, i in goldens["installs"].items() for rel in i["roots"]]
    return sorted(names)


def corpus_ledger_files() -> list[str]:
    """Every ledger file under corpora/, relative to it."""
    return sorted(
        p.relative_to(CORPORA).as_posix()
        for p in CORPORA.rglob("*")
        if p.is_file() and p.name.endswith(LEDGER_SUFFIXES)
    )


# ---- normalization and hashing ------------------------------------------------------------------------------------


def _text(value: str, folder: re.Pattern[str]) -> str:
    return folder.sub(CASE, value)


def normalized(made: Tables, folder: Path) -> dict[str, list[list[Any]]]:
    """Every table's rows with the case folder's path as `<case>` and `input_hash` as `<input_hash>`, sorted."""
    where = re.compile(re.escape(str(folder)) + r"(?![\w.-])")  # the folder itself, not `<folder>b` beside it
    out: dict[str, list[list[Any]]] = {}
    for name, table in made.items():
        rows: list[list[Any]] = []
        for row in table:
            values = [_text(v, where) if isinstance(v, str) else v for v in row]
            if name == "ledger_options" and values[0] == "input_hash":
                values[1] = json.dumps(INPUT_HASH)
            rows.append(values)
        out[name] = sorted(rows, key=lambda r: json.dumps(r, ensure_ascii=False))
    return out


def digest(rows: list[list[Any]]) -> str:
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative(p: Path, folder: Path) -> str:
    return p.relative_to(folder).as_posix()


def corpus_path(r: Root, copied: str) -> str | None:
    """The corpus file a copied path stands for, relative to corpora/ (None outside the copy or for a generated
    case). The install fixture's `data/` is `_data/` in the corpus."""
    p = Path(copied)
    if r.source is None or not p.is_relative_to(r.folder):
        return None
    rel = p.relative_to(r.folder).as_posix()
    if r.source.is_relative_to(INSTALLS) and rel.startswith("data/"):
        rel = "_data/" + rel.removeprefix("data/")
    return (r.source / rel).relative_to(CORPORA).as_posix()


@dataclass(frozen=True, slots=True)
class Built:
    """One root's snapshot `entry`, its normalized `rows` (None when the load was refused), and the files it read."""

    entry: dict[str, Any]
    rows: dict[str, list[list[Any]]] | None
    read: tuple[str, ...]


def build_root(r: Root) -> Built:
    with parity.fresh_plugins(r.folder):
        loaded = load(str(r.root), {}, DESKTOP)
    if not isinstance(loaded, Loaded):
        subject = Path(loaded.subject)
        where = _relative(subject, r.folder) if subject.is_relative_to(r.folder) else loaded.subject
        return Built({"refused": loaded.reason, "file": where}, None, (loaded.subject,))
    made = tables(loaded.entries, loaded.errors, loaded.options, TODAY, STAMP)
    if isinstance(made, BuildFailed):
        return Built({"build_failed": made.detail}, None, ())
    rows = normalized(made, r.folder)
    entry = {"tables": {name: {"rows": len(t), "sha256": digest(t)} for name, t in rows.items()}}
    return Built(entry, rows, tuple(str(f) for f in loaded.options.get("include") or ()))


def read_files(built: dict[Root, Built], files: list[str]) -> set[str]:
    """The corpus files the loads read (relative to corpora/). A refused load reads nothing past its encrypted file
    (ADR-0059), so every file of a refused case's folder counts as read by its refusal."""
    read = {p for r, b in built.items() for f in b.read if (p := corpus_path(r, f)) is not None}
    for r, b in built.items():
        if b.rows is None and r.source is not None:
            prefix = r.source.relative_to(CORPORA).as_posix() + "/"
            read |= {f for f in files if f.startswith(prefix)}
    return read


def unread_files(built: dict[Root, Built], files: list[str] | None = None) -> list[str]:
    """Corpus ledger files that no root's load read."""
    files = corpus_ledger_files() if files is None else files
    read = read_files(built, files)
    return [f for f in files if f not in read]


def empty_tables(built: dict[Root, Built]) -> list[str]:
    """Tables with no row in any case: a change to their rows could not be seen."""
    return [n for n in TABLE_NAMES if not any(b.rows and b.rows.get(n) for b in built.values())]


def build_all(tmp: Path) -> dict[Root, Built]:
    return {r: build_root(r) for r in roots(tmp)}


def snapshot(built: dict[Root, Built]) -> dict[str, dict[str, Any]]:
    """Case -> its entry, sorted by case."""
    return {r.case: b.entry for r, b in sorted(built.items(), key=lambda kv: kv[0].case)}


def render(header: str, snap: dict[str, dict[str, Any]]) -> str:
    """The snapshot file: the header first (`check_generated.py`), one key per line, so each table's `sha256` is on
    its own line (the privacy scan's hash-pin form) and a diff names the case and table that changed."""
    lines = [
        "{",
        f'  "$comment": {json.dumps(header)},',
        f'  "as_of": {json.dumps(TODAY.isoformat())},',
        '  "cases": {',
    ]
    for i, (case, entry) in enumerate(snap.items()):
        end = "," if i < len(snap) - 1 else ""
        if "tables" not in entry:
            lines.append(f"    {json.dumps(case)}: {json.dumps(entry)}{end}")
            continue
        lines.append(f"    {json.dumps(case)}: {{")
        items = list(entry["tables"].items())
        lines += [
            f"      {json.dumps(name)}: {json.dumps(value)}{',' if j < len(items) - 1 else ''}"
            for j, (name, value) in enumerate(items)
        ]
        lines.append(f"    }}{end}")
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def parse(text: str) -> dict[str, dict[str, Any]]:
    """The snapshot file's cases, in the shape `snapshot` gives (the inverse of `render`)."""
    cases: dict[str, dict[str, Any]] = json.loads(text)["cases"]
    return {c: e if "refused" in e or "build_failed" in e else {"tables": e} for c, e in cases.items()}


def iter_rows(tmp: Path, case: str) -> Iterator[tuple[str, list[list[Any]]]]:
    """A case's normalized rows per table (for review: `gen_mirror_checksums.py --rows`)."""
    for r in roots(tmp):
        if r.case == case:
            yield from (build_root(r).rows or {}).items()
            return
    raise KeyError(case)


TABLE_NAMES = tuple(t.name for t in schema.TABLES)
