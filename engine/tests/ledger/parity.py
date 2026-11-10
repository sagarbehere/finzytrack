"""The overlay load against Beancount's own loader (ADR-0010 guardrail 1; INV-03; S0049, S0094).

`same` is the comparison S0049 built for the D1 write corpus (moved here unchanged in S0094 so the parity corpus uses
it too): entries equal, errors equal by type, source and message, options equal key by key (`dcontext` by its text;
`input_hash` left out only when the files were written between the two loads). `load_file` is Beancount's own loader,
the oracle (ADR-0024).

The parity corpus (S0094; `corpora/ledgers/parity/`, `cases.toml`) and the cases generated at test time live here too:
`corpus_cases` reads the hand-written ones, `generated_cases` writes the others into a temp folder (Beancount's example
ledger from its own pinned generator, that ledger split over a recursive glob and under custom root names, and the
cases that need an absolute path or no file). `FEATURES` is the inventory of loader behaviour the corpus is built
from, each with a detector that must find it in the case that claims it.
"""

from __future__ import annotations

import glob
import io
import random
import re
import shutil
import sys
import tomllib
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from os import path
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.parser import options as beancount_options
from beancount.parser import parser
from beancount.scripts import example
from beancount.utils import encryption

from ledger_engine.ledger.overlay import Loaded, LoadRefused

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "corpora" / "ledgers" / "parity"

type Result = tuple[list[Any], list[Any], dict[str, Any]]


# ---- the comparison (S0049) --------------------------------------------------------------------------------------


def same(
    result: Loaded | LoadRefused,
    expected: Result,
    *,
    written_after: bool = False,
) -> None:
    """`written_after`: the files changed between the two loads, so `input_hash` (a stat of the files on disk, which
    only Beancount's pickle cache reads) differs by design and is left out."""
    assert isinstance(result, Loaded), result
    entries, errors, options = expected
    if written_after:
        options = {k: v for k, v in options.items() if k != "input_hash"}
        result = replace(result, options={k: v for k, v in result.options.items() if k != "input_hash"})
    assert result.entries == entries
    assert [(type(e).__name__, e.source, e.message) for e in result.errors] == [
        (type(e).__name__, e.source, e.message) for e in errors
    ]
    assert set(result.options) == set(options)
    for key, value in options.items():
        if key == "dcontext":
            assert str(result.options[key]) == str(value)
        else:
            assert result.options[key] == value, key


def load_file(root: Path) -> Result:
    entries, errors, options = loader.load_file(str(root))
    return list(entries), list(errors), dict(options)


# ---- the corpus --------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """One ledger of the parity corpus, in a temp copy: `root` is its root file (it may not exist), `features` what it
    claims to exercise, `refused` (a refuse case) the encrypted file the load must refuse."""

    name: str
    root: Path
    expect: str
    features: tuple[str, ...]
    refused: Path | None = None
    errors: dict[str, int] = field(default_factory=dict[str, int])
    generated: bool = False

    @property
    def folder(self) -> Path:
        return self.root.parent

    def texts(self) -> dict[str, str]:
        """Every text file of the case, by its path relative to the case folder (documents included)."""
        out: dict[str, str] = {}
        for p in sorted(self.folder.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".picklecache"):
                out[p.relative_to(self.folder).as_posix()] = p.read_bytes().decode("utf-8", errors="replace")
        return out


def corpus_rows() -> dict[str, dict[str, Any]]:
    return tomllib.loads((CORPUS / "cases.toml").read_text(encoding="utf-8"))


def corpus_cases(tmp: Path) -> list[Case]:
    """Every hand-written case, each copied into its own folder under `tmp` (INV-24: never loaded in place)."""
    cases: list[Case] = []
    for name, row in sorted(corpus_rows().items()):
        dest = tmp / name
        shutil.copytree(CORPUS / name, dest)
        refused = row.get("refused")
        cases.append(
            Case(
                name=name,
                root=dest / row.get("root", "main.beancount"),
                expect=row["expect"],
                features=tuple(row["features"]),
                refused=dest / refused if isinstance(refused, str) else None,
                errors=dict(row.get("errors", {})),
            )
        )
    return cases


# Beancount's example ledger (`bean-example`, shipped in the pinned Beancount 3.2.0; GPL-2.0-only like the engine),
# generated, never committed. The CLI ignores its date options in 3.2.0 (`LiberalDate.convert` returns nothing, so it
# always ends today), so the generator's function is called with fixed dates. Its output is pinned: a Beancount
# upgrade that changes the generator fails `test_the_example_generator_is_pinned` until the hash is updated.
EXAMPLE_SEED = 20261110
EXAMPLE_DATES = (date(1980, 5, 12), date(2022, 1, 1), date(2024, 1, 1))  # birth, begin, end
EXAMPLE_PIN = {"sha256": "349cf0f6467fb77f29fd744effe3c6383c7fd3b0742024413ab66af0e1b9a5bc"}


def example_ledger() -> str:
    """Beancount's generated example ledger, the same text on every run (the global random state is put back)."""
    state = random.getstate()
    try:
        random.seed(EXAMPLE_SEED)
        out = io.StringIO()
        birth, begin, end = EXAMPLE_DATES
        example.write_example_file(birth, begin, end, True, out)
    finally:
        random.setstate(state)
    return out.getvalue()


CUSTOM_ROOTS = {
    "Assets": "Vermoegen",
    "Liabilities": "Schulden",
    "Equity": "Eigenkapital",
    "Income": "Einnahmen",
    "Expenses": "Ausgaben",
}
ROOT_NAME = re.compile(r"(?<![\w:-])(Assets|Liabilities|Equity|Income|Expenses)(?=:)")
HEADING = re.compile(r"^\* (.+)$", re.M)


def split_by_heading(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The example's org sections: the header and its options section stay in the root, every other section is its
    own file."""
    starts = [m.start() for m in HEADING.finditer(text)]
    head = text[: starts[1]]
    parts: list[tuple[str, str]] = []
    for i, start in enumerate(starts[1:], 1):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        title = HEADING.match(text, start)
        assert title is not None
        slug = re.sub(r"[^a-z0-9]+", "-", title.group(1).lower()).strip("-")
        # Two depths, so the recursion is used: `parts/03-banking/ledger.beancount`, `parts/04/credit-cards/...`.
        parts.append((f"{i:02d}-{slug}" if i % 2 else f"{i:02d}/{slug}", text[start:end]))
    return head, parts


def generated_cases(tmp: Path) -> list[Case]:
    """The cases built at test time, each in its own folder under `tmp`."""
    text = example_ledger()
    cases: list[Case] = []

    def case(
        name: str,
        features: tuple[str, ...],
        files: dict[str, str],
        root: str = "main.beancount",
        errors: dict[str, int] | None = None,
    ) -> None:
        folder = tmp / name
        for rel, content in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(content, encoding="utf-8")
        folder.mkdir(parents=True, exist_ok=True)
        cases.append(Case(name, folder / root, "load", features, errors=errors or {}, generated=True))

    case("example", ("example-ledger",), {"main.beancount": text})

    head, parts = split_by_heading(text)
    files = {"main.beancount": head + 'include "parts/**/*.beancount"\n'}
    files.update({f"parts/{rel}/ledger.beancount": body for rel, body in parts})
    case("example-split", ("example-ledger", "include-recursive-glob"), files)

    renamed = ROOT_NAME.sub(lambda m: CUSTOM_ROOTS[m.group(1)], text)
    names = "".join(f'option "name_{k.lower()}" "{v}"\n' for k, v in CUSTOM_ROOTS.items())
    case("example-custom-roots", ("example-ledger", "custom-roots"), {"main.beancount": names + renamed})

    folder = tmp / "absolute-paths"
    other, docs = folder / "elsewhere" / "other.beancount", folder / "scans"
    statement = docs / "Assets" / "Cash" / "2024-01-31.statement.txt"
    receipt = folder / "receipts" / "2024-02-01.receipt.txt"
    case(
        "absolute-paths",
        ("include-absolute", "documents-root-absolute", "document-directive"),
        {
            "main.beancount": (
                f'option "documents" "{docs}"\ninclude "{other}"\n2024-01-01 open Assets:Cash USD\n'
                f'2024-02-01 document Assets:Cash "{receipt}"\n'
            ),
            "elsewhere/other.beancount": "2024-01-01 open Expenses:Food USD\n",
            str(statement.relative_to(folder)): "synthetic placeholder (S0094)\n",
            str(receipt.relative_to(folder)): "synthetic placeholder (S0094)\n",
        },
    )
    case("root-missing", ("root-missing",), {}, root="absent.beancount", errors={"LoadError": 1})
    # Bytes that are not UTF-8 (a Latin-1 export pasted in): the overlay parses bytes it read, Beancount the file.
    folder = tmp / "not-utf8"
    folder.mkdir(parents=True)
    (folder / "main.beancount").write_bytes(
        b'2020-01-01 open Assets:Cash USD\n2020-01-02 note Assets:Cash "caf\xe9 na\xefve"\n'
        b'2020-01-03 open Assets:Caf\xe9 USD\n2020-01-04 note Assets:Cash "plain"\n'
    )
    cases.append(
        Case(
            "not-utf8", folder / "main.beancount", "load", ("file-not-utf8",), errors={"LexerError": 2}, generated=True
        )
    )
    return cases


# ---- what each case is checked for -------------------------------------------------------------------------------


@contextmanager
def fresh_plugins(folder: Path) -> Generator[None]:
    """A case's own plugin modules are forgotten after each load, so the next copy imports its own (the engine runs
    a ledger's plugins in an engine it then retires; ADR-0064)."""
    try:
        yield
    finally:
        for name, module in list(sys.modules.items()):
            origin = getattr(module, "__file__", None)
            if isinstance(origin, str) and Path(origin).is_relative_to(folder):
                del sys.modules[name]


@dataclass(slots=True)
class Facts:
    """What a detector sees: the case's files, Beancount's result, and each loaded file parsed alone by Beancount's
    parser (its own includes, options, plugins and entries)."""

    case: Case
    texts: dict[str, str]
    result: Result | None
    parsed: dict[str, tuple[list[Any], list[Any], dict[str, Any]]] = field(default_factory=dict[str, Any])

    @property
    def options(self) -> dict[str, Any]:
        assert self.result is not None
        return self.result[2]

    @property
    def errors(self) -> list[str]:
        return [] if self.result is None else [str(e.message) for e in self.result[1]]

    def others(self) -> list[str]:
        return [f for f in self.parsed if f != str(self.case.root)]

    def includes(self) -> Iterator[tuple[str, str]]:
        """(file, include pattern as written) for every include line of every loaded file."""
        for f, (_, _, options) in self.parsed.items():
            for pattern in options["include"]:
                yield f, pattern


def facts(case: Case, result: Result | None) -> Facts:
    out = Facts(case, case.texts(), result)
    files = [] if result is None else list(result[2]["include"])
    for f in files:
        entries, errors, options = parser.parse_file(f)
        out.parsed[f] = (list(entries), list(errors), dict(options))
    return out


def _resolved(f: str, pattern: str) -> str:
    return path.normpath(pattern if path.isabs(pattern) else path.join(path.dirname(f), pattern))


def _include_plain(x: Facts) -> bool:
    return any(
        not path.isabs(p) and not glob.has_magic(p) and _resolved(f, p) in x.options["include"] for f, p in x.includes()
    )


def _include_glob(x: Facts) -> bool:
    return any(glob.has_magic(p) and "**" not in p and len(glob.glob(_resolved(f, p))) >= 2 for f, p in x.includes())


def _include_recursive_glob(x: Facts) -> bool:
    """A `**` pattern whose matches lie at two depths or more (the recursion is used, not only one level)."""
    for f, p in x.includes():
        if "**" in p:
            depths = {m.count("/") for m in glob.glob(_resolved(f, p), recursive=True)}
            if len(depths) >= 2:
                return True
    return False


def _duplicates(x: Facts) -> list[str]:
    prefix = 'Duplicate filename parsed: "'
    return [m[len(prefix) : -1] for m in x.errors if m.startswith(prefix)]


def _options_of_includes_ignored(x: Facts) -> bool:
    """An included file sets an option that is not merged into the ledger's (all but `operating_currency`, the
    display context and `insert_pythonpath`), to a value the ledger's options do not have."""
    merged = {"operating_currency", "dcontext", "insert_pythonpath", "include", "filename", "plugin"}
    for f in x.others():
        own = x.parsed[f][2]
        for key, value in own.items():
            default = beancount_options.OPTIONS_DEFAULTS.get(key)
            if key not in merged and value != default and value != x.options.get(key):
                return True
    return False


def _root_parse(x: Facts) -> dict[str, Any]:
    return x.parsed[str(x.case.root)][2]


def _plugin_files(x: Facts) -> dict[str, str]:
    """Each plugin the ledger lists that is a module file in one of its `pythonpath` folders: name -> its text."""
    found: dict[str, str] = {}
    for name, _ in x.options["plugin"]:
        for folder in x.options["pythonpath"]:
            p = Path(folder) / f"{name}.py"
            if p.is_file():
                found[name] = p.read_text(encoding="utf-8")
    return found


def _plugin_changes_entries(x: Facts) -> bool:
    """A transaction whose tags differ from the same transaction as its file's parse gave it."""
    assert x.result is not None
    parsed_tags = {
        (e.meta["filename"], e.meta["lineno"]): e.tags
        for entries, _, _ in x.parsed.values()
        for e in entries
        if hasattr(e, "tags") and e.meta is not None
    }
    return any(
        hasattr(e, "tags") and parsed_tags.get((e.meta["filename"], e.meta["lineno"]), e.tags) != e.tags
        for e in x.result[0]
    )


def _documents_found(x: Facts) -> bool:
    """A `document` entry Beancount found by scanning a documents root (no directive names its file)."""
    assert x.result is not None
    named = {e.filename for entries, _, _ in x.parsed.values() for e in entries if type(e).__name__ == "Document"}
    return any(type(e).__name__ == "Document" and e.filename not in named for e in x.result[0])


def _refused_by_glob(x: Facts) -> bool:
    assert x.case.refused is not None
    root = x.case.root.read_text(encoding="utf-8")
    patterns = re.findall(r'^include "([^"]+)"', root, re.M)
    return any(glob.has_magic(p) and str(x.case.refused) in glob.glob(_resolved(str(x.case.root), p)) for p in patterns)


def _refused_nested(x: Facts) -> bool:
    """The encrypted file is named by an include line of a file other than the root."""
    assert x.case.refused is not None
    for rel, text in x.texts.items():
        here = x.case.folder / rel
        if here == x.case.root:
            continue
        for p in re.findall(r'^include "([^"]+)"', text, re.M):
            if _resolved(str(here), p) == str(x.case.refused):
                return True
    return False


def _decodes(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _is_root(x: Facts) -> bool:
    return x.case.refused == x.case.root


DEFAULT_ROOTS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")
ROOT_OPTIONS = ("name_assets", "name_liabilities", "name_equity", "name_income", "name_expenses")

# The inventory of loader behaviour (S0094 Hand-off, "Population"), read from Beancount 3.2.0's `loader.py`
# (`_parse_recursive`, `aggregate_options_map`, `run_transformations`), `ops/documents.py` and `utils/encryption.py`.
# Every option, built-in plugin and directive type is a population of its own (`test_parity_corpus.py`), read from
# Beancount at run time.
FEATURES: dict[str, Callable[[Facts], bool]] = {
    # files (`_parse_recursive`)
    "include-plain": _include_plain,
    "include-absolute": lambda x: any(path.isabs(p) for _, p in x.includes()),
    "include-glob": _include_glob,
    "include-recursive-glob": _include_recursive_glob,
    "include-nested": lambda x: any(f != str(x.case.root) for f, _ in x.includes()),
    "include-parent-folder": lambda x: any(p.startswith("../") for _, p in x.includes()),
    "include-no-match": lambda x: any(m.endswith("does not match any files") for m in x.errors),
    "include-duplicate": lambda x: any(d != str(x.case.root) for d in _duplicates(x)),
    "include-cycle": lambda x: (
        str(x.case.root) in _duplicates(x)
        and any(_resolved(f, p) == str(x.case.root) for f, p in x.includes() if f != str(x.case.root))
    ),
    "root-missing": lambda x: f'File "{x.case.root}" does not exist' in x.errors,
    "plain-asc": lambda x: any(f.endswith(".asc") for f in x.options["include"]),
    # options across files (`aggregate_options_map`)
    "options-of-includes-ignored": _options_of_includes_ignored,
    "operating-currency-merged": lambda x: bool(
        set(x.options["operating_currency"]) - set(_root_parse(x)["operating_currency"])
    ),
    "precision-merged": lambda x: str(x.options["dcontext"]) != str(_root_parse(x)["dcontext"]),
    "pythonpath-from-include": lambda x: any(p != str(x.case.folder) for p in x.options["pythonpath"]),
    "custom-roots": lambda x: all(x.options[o] != d for o, d in zip(ROOT_OPTIONS, DEFAULT_ROOTS, strict=True)),
    # plugins (`run_transformations`)
    "plugin-raw-mode": lambda x: x.options["plugin_processing_mode"] == "raw",
    "plugin-config": lambda x: any(config is not None for _, config in x.options["plugin"]),
    "plugin-local": lambda x: bool(_plugin_files(x)),
    "plugin-function-object": lambda x: any(
        re.search(r"^__plugins__ = \[[a-z_]+\]", t, re.M) for t in _plugin_files(x).values()
    ),
    "plugin-without-plugins-list": lambda x: any(
        not re.search(r"^__plugins__ =", t, re.M) for t in _plugin_files(x).values()
    ),
    "plugin-import-error": lambda x: any(m.startswith("Error importing") for m in x.errors),
    "plugin-raises": lambda x: any(m.startswith("Error applying plugin") for m in x.errors),
    "plugin-in-include-ignored": lambda x: any(
        name not in {n for n, _ in x.options["plugin"]} for f in x.others() for name, _ in x.parsed[f][2]["plugin"]
    ),
    "plugin-adds-entries": lambda x: (
        x.result is not None
        and any(str(e.meta["filename"]).startswith("<") and e.meta["filename"] != "<load>" for e in x.result[0])
    ),
    "plugin-changes-entries": _plugin_changes_entries,
    # documents (`ops/documents.py`)
    "documents-root": _documents_found,
    "documents-roots-several": lambda x: len(x.options["documents"]) >= 2,
    "documents-root-absolute": lambda x: any(path.isabs(d) for d in x.options["documents"]),
    "document-directive": lambda x: any(
        type(e).__name__ == "Document" for entries, _, _ in x.parsed.values() for e in entries
    ),
    "document-missing-file": lambda x: any(m.startswith("File does not exist") for m in x.errors),
    # encrypted files (`utils/encryption.is_encrypted_file`; ADR-0059: refused)
    "encrypted-root-gpg": lambda x: _is_root(x) and x.case.root.suffix == ".gpg",
    "encrypted-root-asc": lambda x: _is_root(x) and x.case.root.suffix == ".asc",
    "encrypted-include-glob": _refused_by_glob,
    "encrypted-include-nested": _refused_nested,
    "file-not-utf8": lambda x: any(not _decodes(Path(f).read_bytes()) for f in x.parsed),
    # Beancount's example ledger (`bean-example`), the source Fava's example ledgers are made from
    "example-ledger": lambda x: "THIS FILE HAS BEEN AUTO-GENERATED" in x.texts.get("main.beancount", ""),
}


def beancount_decrypts(p: Path) -> bool:
    """Beancount's own test: would its loader decrypt this file instead of parsing it?"""
    return bool(encryption.is_encrypted_file(str(p)))
