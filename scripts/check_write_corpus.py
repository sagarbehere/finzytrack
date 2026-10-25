#!/usr/bin/env python3
"""The D1 write corpus check (S0020; spec: design/specs/write-path/write-corpus.md). Stdlib only.

`corpora/ledgers/write/` holds one folder per hand-written case, rooted at `main.beancount`, described in
`cases.toml`. This check refuses, per case: a folder without an entry or an entry without a folder; a declared
feature outside FEATURES; declared features that differ from what the detectors find in the case's include tree;
`expect = "refuse"` unless the case holds an included encrypted file, and a `load` case holding any file Beancount
might decrypt (conservative: any `.gpg` name, or a PGP message header anywhere in an `.asc` file); symlinks;
files whose git `text` attribute is not unset (the bytes must survive a checkout). It also refuses a corpus whose
cases together miss a feature of FEATURES. Whether each case loads with the declared errors is the oracle
environment's job (`scripts/oracle/tests/test_write_corpus.py`).

Usage: python3 scripts/check_write_corpus.py
Exit 0 = ok, 1 = refused.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = "corpora/ledgers/write"
CASES_FILE = "cases.toml"
ROOT_FILE = "main.beancount"

# The fixed list the corpus must cover, each with the spec line that asks for it.
FEATURES: dict[str, str] = {
    "crlf": "review:D1 §6 (CRLF)",
    "no-final-newline": "review:D1 §6",
    "tabs": "review:D1 §6",
    "unicode-payee": "review:D1 §6",
    "comment-between-postings": "review:D1 §6 (comments between postings)",
    "comment-after-postings": "review:D1 §6 (comments after postings)",
    "comment-inline-posting": "review:D1 §6 (comments after postings, on the posting's line)",
    "posting-metadata": "review:D1 §6",
    "pushtag": "review:D1 §6",
    "pushmeta": "review:D1 §6",
    "org-headings": "review:D1 §6",
    "glob-include": "review:D1 §6, §9 guardrail 1",
    "nested-include": "review:D1 §6, §9 guardrail 1",
    "implicit-prices": "review:D1 §6, §9 guardrail 1",
    "documents-folders": "review:D1 §6, §9 guardrail 1",
    "elided-amount": "review:D1 §6",
    "empty-cost-reduction": "review:D1 §6 ({} reductions)",
    "insert-entry": "review:D1 §6",
    "pythonpath-plugin": "review:D1 §9 guardrail 1",
    "duplicate-include": "review:D1 §9 guardrail 1",
    "plugin-modifies-entries": "review:D1 §8 (confirm with a corpus case); the change itself is checked on load",
    "total-price": "review:D1 §8 (@@)",
    "encrypted-gpg": "ADR-0059 (refused)",
    "encrypted-asc": "ADR-0059 (refused)",
    "asc-plain": "ADR-0059 (an .asc file without a PGP message is plain text Beancount reads: not refused)",
}
CASE_KEYS = {"features", "expect", "errors", "note"}
ERROR_KEYS = {"type", "message"}
EXPECTS = {"load", "refuse"}

DATE = r"\d{4}-\d{2}-\d{2}"
ACCOUNT = r"[A-Z][^\s:;\"]*(?::[^\s:;\"]+)+"
TXN_HEAD = re.compile(rf"^{DATE}[ \t]+(?:\*|!|txn)(?:[ \t]|$)")
POSTING = re.compile(rf"^([ \t]+)(?:[!*][ \t]+)?({ACCOUNT})(.*)$")
META = re.compile(r"^([ \t]+)[a-z][A-Za-z0-9_-]*:(?:[ \t]|$)")
COMMENT = re.compile(r"^[ \t]*;")
STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
INCLUDE = re.compile(r'^include[ \t]+"([^"]+)"')
OPEN = re.compile(rf"^{DATE}[ \t]+open[ \t]+({ACCOUNT})")
DOCUMENT = re.compile(rf'^{DATE}[ \t]+document[ \t]+{ACCOUNT}[ \t]+"([^"]+)"')
DOC_FILE = re.compile(rf"^{DATE}\..+")
PGP_HEADER = b"--BEGIN PGP MESSAGE--"


class Tree:
    """One case's files and the include tree Beancount would read from `main.beancount` (pure; no disk)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.edges: list[tuple[str, str, list[str]]] = []  # (includer, written target, resolved files)
        self.reached: list[str] = []
        todo = [ROOT_FILE] if ROOT_FILE in files else []
        while todo:
            cur = todo.pop(0)
            if cur in self.reached:
                continue
            self.reached.append(cur)
            if encrypted_exact(cur, files[cur]):
                continue  # Beancount would decrypt it; its text is not ours to read
            for line in lines(files[cur]):
                m = INCLUDE.match(line)
                if m:
                    found = resolve(posixpath.dirname(cur), m.group(1), files)
                    self.edges.append((cur, m.group(1), found))
                    todo.extend(found)

    def text_files(self) -> dict[str, list[str]]:
        return {p: lines(self.files[p]) for p in self.reached if not encrypted_exact(p, self.files[p])}


def lines(data: bytes) -> list[str]:
    return [ln.removesuffix("\r") for ln in data.decode("utf-8", errors="replace").split("\n")]


def resolve(base: str, target: str, files: dict[str, bytes]) -> list[str]:
    """Files an include names, relative to the including file; glob segments match within one folder."""
    want = posixpath.normpath(posixpath.join(base, target)).split("/")
    if not any(ch in target for ch in "*?["):
        p = "/".join(want)
        return [p] if p in files else []
    return sorted(
        p for p in files
        if len(parts := p.split("/")) == len(want) and all(segment_matches(a, b) for a, b in zip(parts, want))
    )


def segment_matches(name: str, pattern: str) -> bool:
    """Python's glob: a wildcard never matches a leading dot."""
    return fnmatch.fnmatchcase(name, pattern) and (pattern.startswith(".") or not name.startswith("."))


def encrypted_exact(name: str, data: bytes) -> bool:
    """Beancount 3.2.0's `is_encrypted_file`, approximately: the load test checks the real one on every file."""
    if posixpath.splitext(name)[1] == ".gpg":
        return True
    head = data[:1024]
    return posixpath.splitext(name)[1] == ".asc" and head.isascii() and PGP_HEADER in head


def might_decrypt(name: str, data: bytes) -> bool:
    """Conservative: what a `load` case must never hold, so loading it can never start gpg."""
    ext = posixpath.splitext(name)[1].lower()
    return ext == ".gpg" or (ext == ".asc" and b"BEGIN PGP MESSAGE" in data)


def code(line: str) -> str:
    """The line without its comment (a `;` outside strings starts one)."""
    cut = STRING.sub(lambda m: "x" * len(m.group()), line).find(";")
    return line if cut < 0 else line[:cut]


def txn_blocks(text: list[str]) -> list[tuple[int, int]]:
    """(header index, index after the last indented body line) per transaction."""
    out = []
    for i, line in enumerate(text):
        if TXN_HEAD.match(line):
            j = i + 1
            while j < len(text) and text[j][:1] in (" ", "\t") and text[j].strip():
                j += 1
            out.append((i, j))
    return out


def any_line(tree: Tree, pred: Callable[[str], bool]) -> bool:
    return any(pred(ln) for text in tree.text_files().values() for ln in text)


def postings(tree: Tree) -> list[str]:
    return [
        text[k]
        for text in tree.text_files().values()
        for i, j in txn_blocks(text)
        for k in range(i + 1, j)
        if POSTING.match(text[k])
    ]


def d_crlf(tree: Tree) -> bool:
    return any(
        b"\n" in d and d.count(b"\r\n") == d.count(b"\n") == d.count(b"\r")
        for p, d in tree.files.items() if p in tree.text_files()
    )


def d_no_final_newline(tree: Tree) -> bool:
    return any(tree.files[p] and not tree.files[p].endswith(b"\n") for p in tree.text_files())


def d_tabs(tree: Tree) -> bool:
    return any("\t" in POSTING.match(p).group(1) for p in postings(tree))  # type: ignore[union-attr]


def d_unicode_payee(tree: Tree) -> bool:
    for text in tree.text_files().values():
        for i, _ in txn_blocks(text):
            strings = STRING.findall(code(text[i]))
            if len(strings) >= 2 and not strings[0].isascii():
                return True
    return False


def body_kinds(text: list[str], i: int, j: int) -> list[str]:
    kinds = []
    for k in range(i + 1, j):
        ln = text[k]
        kinds.append("comment" if COMMENT.match(ln) else "posting" if POSTING.match(ln) else
                     "meta" if META.match(ln) else "other")
    return kinds


def d_comment_between_postings(tree: Tree) -> bool:
    for text in tree.text_files().values():
        for i, j in txn_blocks(text):
            kinds = body_kinds(text, i, j)
            for n, kind in enumerate(kinds):
                if kind == "comment" and "posting" in kinds[:n] and "posting" in kinds[n + 1:]:
                    return True
    return False


def d_comment_after_postings(tree: Tree) -> bool:
    for text in tree.text_files().values():
        for i, j in txn_blocks(text):
            kinds = body_kinds(text, i, j)
            if "posting" not in kinds:
                continue
            last = max(n for n, k in enumerate(kinds) if k == "posting")
            if "comment" in kinds[last + 1:] or (j < len(text) and COMMENT.match(text[j])):
                return True
    return False


def d_comment_inline_posting(tree: Tree) -> bool:
    return any(code(p) != p for p in postings(tree))


def d_posting_metadata(tree: Tree) -> bool:
    for text in tree.text_files().values():
        for i, j in txn_blocks(text):
            indent = None
            for k in range(i + 1, j):
                p, m = POSTING.match(text[k]), META.match(text[k])
                if p:
                    indent = len(p.group(1).expandtabs(4))
                elif m and indent is not None and len(m.group(1).expandtabs(4)) > indent:
                    return True
                elif not COMMENT.match(text[k]):
                    indent = None
    return False


def paired(tree: Tree, push: str, pop: str) -> bool:
    for text in tree.text_files().values():
        pushed = {m.group(1) for ln in text if (m := re.match(rf"^{push}[ \t]+(\S+)", ln))}
        popped = {m.group(1) for ln in text if (m := re.match(rf"^{pop}[ \t]+(\S+)", ln))}
        if pushed & popped:
            return True
    return False


def d_pushtag(tree: Tree) -> bool:
    return paired(tree, "pushtag", "poptag")


def d_pushmeta(tree: Tree) -> bool:
    return paired(tree, "pushmeta", "popmeta")


def d_org_headings(tree: Tree) -> bool:
    return any_line(tree, lambda ln: re.match(r"^\*+[ \t]+\S", ln) is not None)


def d_glob_include(tree: Tree) -> bool:
    return any(any(ch in target for ch in "*?[") and found for _, target, found in tree.edges)


def d_nested_include(tree: Tree) -> bool:
    return any(src != ROOT_FILE and found for src, _, found in tree.edges)


def d_implicit_prices(tree: Tree) -> bool:
    plugin = any_line(tree, lambda ln: re.match(r'^plugin[ \t]+"beancount\.plugins\.implicit_prices"', ln) is not None)
    priced = any(re.search(r"@|\{[^}]*\d", STRING.sub('""', code(p))) for p in postings(tree))
    return plugin and priced


def d_documents_folders(tree: Tree) -> bool:
    text = tree.text_files()
    if ROOT_FILE not in text:
        return False
    folders = [m.group(1) for ln in text[ROOT_FILE] if (m := re.match(r'^option[ \t]+"documents"[ \t]+"([^"]+)"', ln))]
    accounts = {m.group(1) for t in text.values() for ln in t if (m := OPEN.match(ln))}
    named = {posixpath.normpath(m.group(1)) for t in text.values() for ln in t if (m := DOCUMENT.match(ln))}
    for folder in folders:
        root = posixpath.normpath(folder)
        for p in tree.files:
            rel = posixpath.relpath(p, root)
            if p.startswith(root + "/") and p not in named and "/" in rel:
                account, name = rel.rsplit("/", 1)
                if account.replace("/", ":") in accounts and DOC_FILE.match(name):
                    return True
    return False


def d_elided_amount(tree: Tree) -> bool:
    return any(not POSTING.match(code(p)).group(3).strip() for p in postings(tree))  # type: ignore[union-attr]


def d_empty_cost_reduction(tree: Tree) -> bool:
    return any(re.search(r"(?:^|\s)-\s*\d[\d,]*(?:\.\d+)?\s+[A-Z][A-Z0-9'._-]*\s*\{\s*\}", code(p)) for p in postings(tree))


def d_insert_entry(tree: Tree) -> bool:
    return any_line(
        tree, lambda ln: re.match(rf'^{DATE}[ \t]+custom[ \t]+"fava-option"[ \t]+"insert-entry"[ \t]+"[^"]+"', ln) is not None
    )


def local_plugins(tree: Tree) -> list[str]:
    text = tree.text_files().get(ROOT_FILE, [])
    if not any(re.match(r'^option[ \t]+"insert_pythonpath"[ \t]+"TRUE"', ln) for ln in text):
        return []
    names = [m.group(1) for ln in text if (m := re.match(r'^plugin[ \t]+"([A-Za-z_][A-Za-z0-9_]*)"', ln))]
    return [n for n in names if f"{n}.py" in tree.files or f"{n}/__init__.py" in tree.files]


def d_pythonpath_plugin(tree: Tree) -> bool:
    return bool(local_plugins(tree))


def d_plugin_modifies_entries(tree: Tree) -> bool:
    # Text can only show that a local plugin runs; the load test checks that it changes a user entry.
    return bool(local_plugins(tree))


def d_duplicate_include(tree: Tree) -> bool:
    seen = [ROOT_FILE] + [f for _, _, found in tree.edges for f in found]  # the root counts: a self-include repeats it
    return len(seen) != len(set(seen))


def d_total_price(tree: Tree) -> bool:
    return any("@@" in STRING.sub('""', code(p)) for p in postings(tree))


def d_encrypted_gpg(tree: Tree) -> bool:
    return any(posixpath.splitext(p)[1] == ".gpg" for p in tree.reached)


def d_encrypted_asc(tree: Tree) -> bool:
    return any(posixpath.splitext(p)[1] == ".asc" and encrypted_exact(p, tree.files[p]) for p in tree.reached)


def d_asc_plain(tree: Tree) -> bool:
    return any(posixpath.splitext(p)[1] == ".asc" and not encrypted_exact(p, tree.files[p]) for p in tree.reached)


DETECTORS: dict[str, Callable[[Tree], bool]] = {
    name: globals()["d_" + name.replace("-", "_")] for name in FEATURES
}


def detect(files: dict[str, bytes]) -> set[str]:
    tree = Tree(files)
    return {name for name, fn in DETECTORS.items() if fn(tree)}


def check_entry(name: str, entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{name}: the entry must be a table"]
    problems = [f"{name}: unknown key {k!r}" for k in sorted(set(entry) - CASE_KEYS)]
    feats = entry.get("features")
    if not isinstance(feats, list) or not feats or not all(isinstance(f, str) for f in feats):
        problems.append(f"{name}: features must be a non-empty list of strings")
    elif len(set(feats)) != len(feats):
        problems.append(f"{name}: a feature is listed twice")
    if entry.get("expect") not in EXPECTS:
        problems.append(f"{name}: expect must be one of {sorted(EXPECTS)}")
    errors = entry.get("errors")
    if not isinstance(errors, list):
        problems.append(f"{name}: errors must be a list (empty when Beancount reports none)")
    else:
        for e in errors:
            if not isinstance(e, dict) or set(e) != ERROR_KEYS or not all(isinstance(v, str) and v for v in e.values()):
                problems.append(f"{name}: each error is {{type = \"...\", message = \"...\"}} with non-empty strings")
        if errors and entry.get("expect") == "refuse":
            problems.append(f"{name}: a refused case is never loaded, so it declares no errors")
    return problems


def check(cases: dict[str, object], folders: dict[str, dict[str, bytes]]) -> list[str]:
    """Problems with the corpus: `cases` is cases.toml parsed, `folders` maps case name -> {relative path: bytes}."""
    problems = []
    for name in sorted(set(folders) - set(cases)):
        problems.append(f"{name}: folder has no entry in {CASES_FILE}")
    for name in sorted(set(cases) - set(folders)):
        problems.append(f"{name}: entry in {CASES_FILE} has no folder")
    covered: set[str] = set()
    for name in sorted(set(cases) & set(folders)):
        entry, files = cases[name], folders[name]
        entry_problems = check_entry(name, entry)
        problems += entry_problems
        if entry_problems:
            continue
        assert isinstance(entry, dict)
        if ROOT_FILE not in files:
            problems.append(f"{name}: no {ROOT_FILE}")
            continue
        declared = set(entry["features"])
        for f in sorted(declared - set(FEATURES)):
            problems.append(f"{name}: unknown feature {f!r}")
        tree = Tree(files)
        for src, target, _ in tree.edges:
            if "**" in target:
                problems.append(f"{name}: {src} includes {target!r}: recursive globs are not modelled by this check")
        found = detect(files)
        for f in sorted(declared & set(FEATURES) - found):
            problems.append(f"{name}: declares {f!r} but the case does not show it")
        for f in sorted(found - declared):
            problems.append(f"{name}: shows {f!r} but does not declare it")
        encrypted = bool(found & {"encrypted-gpg", "encrypted-asc"})
        if entry["expect"] == "refuse" and not encrypted:
            problems.append(f"{name}: expect = \"refuse\" needs an included encrypted file (ADR-0059)")
        if entry["expect"] == "load":
            for p in sorted(p for p, d in files.items() if might_decrypt(p, d)):
                problems.append(f"{name}: {p} might be decrypted by Beancount, so the case must be refused, not loaded")
        covered |= declared & found
    for f in sorted(set(FEATURES) - covered):
        problems.append(f"corpus: no case covers {f!r} ({FEATURES[f]})")
    return problems


def read_corpus(root: Path) -> tuple[dict[str, object], dict[str, dict[str, bytes]], list[str]]:
    """Read cases.toml and every case folder from disk; refuse symlinks and stray files."""
    problems = []
    try:
        cases: dict[str, object] = tomllib.loads((root / CASES_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return {}, {}, [f"{CASES_FILE}: cannot read: {exc}"]
    folders: dict[str, dict[str, bytes]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            problems.append(f"{rel}: symlinks are refused in the corpus")
        elif p.is_file() and rel != CASES_FILE:
            if "/" not in rel:
                problems.append(f"{rel}: stray file; every file belongs to a case folder")
                continue
            case, inner = rel.split("/", 1)
            folders.setdefault(case, {})[inner] = p.read_bytes()
    return cases, folders, problems


def check_attributes(paths: list[str], attr_output: str) -> list[str]:
    """`git check-attr -z text` output: every corpus file must have `text` unset (exact bytes on checkout)."""
    fields = attr_output.split("\0")
    seen = {fields[i]: fields[i + 2] for i in range(0, len(fields) - 2, 3) if fields[i + 1] == "text"}
    return [f"{p}: git attribute text is {seen.get(p, 'missing')!r}, must be 'unset' (.gitattributes)"
            for p in paths if seen.get(p) != "unset"]


def main() -> int:
    root = REPO / CORPUS
    cases, folders, problems = read_corpus(root)
    problems += check(cases, folders)
    paths = sorted(p.relative_to(REPO).as_posix() for p in root.rglob("*") if p.is_file())
    try:
        out = subprocess.run(["git", "-C", str(REPO), "check-attr", "-z", "text", "--", *paths],
                             capture_output=True, text=True, check=True).stdout
        problems += check_attributes(paths, out)
    except (OSError, subprocess.CalledProcessError) as exc:
        problems.append(f"git check-attr failed: {exc}")
    for line in problems:
        print(f"check_write_corpus: {line}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
