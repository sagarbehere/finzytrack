"""The engine-jobs spec cites what the user reads, and never restates it (S0079, BL-0192; INV-19; spec §11).

`backend/scripts/gen_engine_job_texts.py` renders every text of `app.platform.engine_jobs.text`, `save_text`,
`recovery_text` and the registry's engine-job codes into `design/generated/engine-job-texts.md`, each under a key
(`msg:…`); `scripts/check_generated.py` keeps that file equal to the code. `design/specs/engine-jobs.md` says when
each text shows and cites it by its key. Here, both ways: every key the spec cites exists, every rendered text is
cited, and no sentence of the reference is also quoted in the spec (one home: the code).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "design" / "specs" / "engine-jobs.md"
TEXTS = REPO / "design" / "generated" / "engine-job-texts.md"

KEY = re.compile(r"`(msg:[A-Za-z0-9_.]+)`")
ROW = re.compile(r"^\| `(msg:[A-Za-z0-9_.]+)` \| (?:[^|\\]|\\.)* \| (.*) \|$")
SPLIT = re.compile(r"<[^<>]+>|\*\*(?:Message|Next step):\*\*")
MIN_FRAGMENT = 40
"""Fragments shorter than this (a phase, "Cancelled.") are not compared: they recur as plain words."""


def _texts() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in TEXTS.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = m.group(2).replace("\\|", "|")
    return rows


def test_every_key_the_spec_cites_exists_and_every_rendered_text_is_cited() -> None:
    texts = _texts()
    assert len(texts) >= 80, "the reference's rows were not found"
    cited = set(KEY.findall(SPEC.read_text(encoding="utf-8")))
    assert sorted(cited - set(texts)) == [], "keys the spec cites that the reference does not have"
    assert sorted(set(texts) - cited) == [], "texts in the reference that the spec never cites"


def test_no_sentence_of_the_reference_is_also_quoted_in_the_spec() -> None:
    spec = " ".join(SPEC.read_text(encoding="utf-8").split())
    doubled = sorted(
        f"{key}: {fragment}"
        for key, text in _texts().items()
        for fragment in (f.strip(" .,;:") for f in SPLIT.split(text))
        if len(fragment) >= MIN_FRAGMENT and fragment in spec
    )
    assert doubled == [], "words with two homes: cite the key instead"


ONE_OS_HOW_TO = re.compile(r"\bMac\b|macOS|Finder|Terminal|chmod|Get Info|File Explorer|PowerShell|icacls")
"""Words of one OS's way to do something (S0150; owner, 2026-11-22: "fold the Mac wording into S0150")."""


def test_no_engine_job_text_gives_one_os_how_to() -> None:
    """S0150 (BL-0087): the sidecar's save, recovery, open, create and copy texts reach a user on macOS, Linux and
    Windows alike, so none gives one OS's way to do something (the base's 11 keys said "Your Mac does not allow",
    "Finder: Get Info", "Terminal: chmod -N", "your Mac's own disk"). Under IMP-0078 the cause is told in the generic
    form. Checked on the reference, which `scripts/check_generated.py` holds equal to the code. Must not pass: any
    rendered text with such a word."""
    found = sorted(f"{key}: {m.group(0)}" for key, text in _texts().items() for m in ONE_OS_HOW_TO.finditer(text))
    assert found == [], found


TEXT_SOURCES = (
    *sorted((REPO / "backend" / "src" / "app" / "platform" / "engine_jobs").glob("*.py")),
    REPO / "backend" / "src" / "app" / "platform" / "errors" / "registry.py",
    REPO / "backend" / "src" / "app" / "platform" / "adapters" / "app_folder.py",
)
"""The sidecar's text modules and the registry, read as source: a text the reference does not render is checked too."""


def one_os_words_in_source(source: str) -> list[str]:
    """Every string literal of `source` (f-strings' parts included; docstrings and other bare string statements left
    out) that holds a word of `ONE_OS_HOW_TO`, with its line."""
    tree = ast.parse(source)
    bare = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return [
        f"{n.lineno}: {m.group(0)}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in bare
        for m in ONE_OS_HOW_TO.finditer(n.value)
    ]


def test_no_sidecar_text_in_the_source_gives_one_os_how_to() -> None:
    """S0150 round 1 (V1, M1): the reference renders most texts, not all (the blocked screen for
    `engine.unsupported_platform` said "It works on macOS and Linux" and was in none). Must not pass: a string
    literal in a text module with such a word."""
    found = [f"{p.name}:{hit}" for p in TEXT_SOURCES for hit in one_os_words_in_source(p.read_text(encoding="utf-8"))]
    assert found == [], found


def test_the_source_scan_sees_a_literal_and_an_f_string_but_not_a_docstring() -> None:
    """@regression-guard: the scan's own reach."""
    source = '"""On a Mac."""\nX = "Your Mac"\ndef f(n):\n    """Finder."""\n    return f"{n} (Terminal: chmod)"\n'
    assert [h.split(": ")[1] for h in one_os_words_in_source(source)] == ["Mac", "Terminal", "chmod"]
