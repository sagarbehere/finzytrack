"""The demo generator's output changes only with a new GENERATOR_VERSION (S0027 verifier F2; S0032, seeded plant C08).

`GENERATOR_VERSION` is "raised when the same inputs would give other bytes" (`app.content.demo.compose`). The pins of
each version live in `demo_pins.toml`, which is append-only: a version's pins never change and are never removed. A pin
beside the version in the same file could be regenerated under the same version (plant C08: the demo's tax rate
changed and the pins updated to match, and every gate stayed green), so the file is checked against its own git
history: every earlier committed state, and the working tree, must keep every pin it had.

This needs the repository's history: a checkout without it (a shallow clone, a copy without `.git`) fails, never
skips. CI's backend job fetches the whole history for it.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import subprocess
import tomllib
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from app.content.demo import GENERATOR_VERSION, Size, default_fragments, generate

pytestmark = pytest.mark.cap("CAP-DATA-DEMO-GEN")

PINS = Path(__file__).with_name("demo_pins.toml")
ANCHOR = date(2026, 10, 25)
SIZES = {s.name for s in Size}
HEX64 = re.compile(r"[0-9a-f]{64}")

type Key = tuple[int, str]


def parse(text: str) -> tuple[dict[Key, str], list[str]]:
    """{(version, size): sha256} and the problems of one state of the pins file."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return {}, [f"not TOML ({exc})"]
    problems = [f"unknown top-level key {k!r}" for k in doc if k != "pin"]
    pins: dict[Key, str] = {}
    entries: object = doc.get("pin", [])
    items = cast("list[object]", entries) if isinstance(entries, list) else [entries]
    for i, item in enumerate(items):
        pin = cast("dict[str, object]", item) if isinstance(item, dict) else {}
        if set(pin) != {"version", "size", "sha256"}:
            problems.append(f"pin {i + 1}: needs exactly version, size and sha256")
            continue
        version, size, digest = pin["version"], pin["size"], pin["sha256"]
        if type(version) is not int or version < 1:
            problems.append(f"pin {i + 1}: version must be a whole number from 1")
        elif not isinstance(size, str) or size not in SIZES:
            problems.append(f"pin {i + 1}: size {size!r} is not one of {sorted(SIZES)}")
        elif not isinstance(digest, str) or not HEX64.fullmatch(digest):
            problems.append(f"pin {i + 1}: sha256 must be 64 lowercase hex digits")
        elif (version, size) in pins:
            problems.append(f"pin {i + 1}: version {version} {size} is pinned twice")
        else:
            pins[version, size] = digest
    return pins, problems


def history_problems(states: Sequence[tuple[str, str]]) -> list[str]:
    """Problems across the states of the pins file, oldest first, each labelled (a commit, or the working tree)."""
    problems: list[str] = []
    parsed: list[tuple[str, dict[Key, str]]] = []
    for label, text in states:
        pins, errors = parse(text)
        problems += [f"{label}: {e}" for e in errors]
        parsed.append((label, pins))
    for (_, old), (label, new) in itertools.pairwise(parsed):
        for (version, size), digest in sorted(old.items()):
            if (version, size) not in new:
                problems.append(f"{label}: removes the pin of version {version} {size} (pins are append-only)")
            elif new[version, size] != digest:
                problems.append(
                    f"{label}: changes the pin of version {version} {size}; output that changes needs a new "
                    "GENERATOR_VERSION with its own pins"
                )
    return problems


def current_problems(pins: dict[Key, str], version: int, digests: dict[str, str]) -> list[str]:
    """The generator's version is the newest pinned one, and its output is that version's pins."""
    versions = {v for v, _ in pins}
    if not versions or version != max(versions):
        return [f"GENERATOR_VERSION {version} is not the newest pinned version ({sorted(versions)})"]
    expected = {s: d for (v, s), d in pins.items() if v == version}
    if expected != digests:
        return [
            f"the output differs from version {version}'s pins: raise GENERATOR_VERSION and add its pins "
            f"(expected {expected}, got {digests})"
        ]
    return []


def _clean_env() -> dict[str, str]:
    """The environment without GIT_* variables: a hook's GIT_DIR must not redirect `git -C`."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Replacement refs could show another blob than the one committed; a signature check could add text to the output.
    cmd = ["git", "--no-replace-objects", "-C", str(repo), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, env=_clean_env())  # noqa: S603 -- git with fixed arguments


def git_states(path: Path) -> list[tuple[str, str]]:
    """Every committed state of `path` (oldest first; a deletion is an empty state), then the working tree's."""
    folder = path.parent
    top = _git(folder, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        pytest.fail(f"{path} is not in a git checkout: the pins check needs the history ({top.stderr.strip()})")
    repo = Path(top.stdout.strip())
    if _git(repo, "rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        pytest.fail("a shallow clone has no full history for the pins check: fetch it all (fetch-depth: 0 in CI)")
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    log = _git(repo, "log", "--no-show-signature", "--reverse", "--format=%H", "--", rel)
    if log.returncode != 0:
        pytest.fail(f"git log failed: {log.stderr.strip()}")
    states: list[tuple[str, str]] = []
    for sha in log.stdout.split():
        shown = _git(repo, "show", "--no-show-signature", f"{sha}:{rel}")
        states.append((sha[:12], shown.stdout if shown.returncode == 0 else ""))
    return [*states, ("working tree", path.read_text(encoding="utf-8") if path.exists() else "")]


def test_the_output_is_the_current_versions_pins() -> None:
    pins, errors = parse(PINS.read_text(encoding="utf-8"))
    assert errors == []
    text = {s.name: generate(ANCHOR, s, default_fragments()).files["main.beancount"] for s in Size}
    digests = {name: hashlib.sha256(t.encode()).hexdigest() for name, t in text.items()}
    assert current_problems(pins, GENERATOR_VERSION, digests) == []


def test_the_pins_are_append_only_in_their_git_history() -> None:
    states = git_states(PINS)
    assert history_problems(states) == []


# --- must-not-pass (EP-29) ---

V1 = {"DEMO": "a" * 64, "K5": "b" * 64, "K54": "c" * 64}


def pins_text(*entries: tuple[int, str, str]) -> str:
    return "".join(f'[[pin]]\nversion = {v}\nsize = "{s}"\nsha256 = "{d}"\n\n' for v, s, d in entries)


def version(v: int, digests: dict[str, str]) -> list[tuple[int, str, str]]:
    return [(v, s, d) for s, d in digests.items()]


ONE = pins_text(*version(1, V1))
V2 = {"DEMO": "d" * 64, "K5": "e" * 64, "K54": "f" * 64}


def test_a_new_version_with_its_own_pins_is_accepted() -> None:
    two = pins_text(*version(1, V1), *version(2, V2))
    assert history_problems([("a", ONE), ("b", ONE), ("c", two)]) == []
    assert current_problems(parse(two)[0], 2, V2) == []


@pytest.mark.parametrize(
    ("label", "newer"),
    [
        ("pins regenerated under the same version (plant C08)", pins_text(*version(1, V1 | {"DEMO": "9" * 64}))),
        ("a pin removed", pins_text(*version(1, {"DEMO": V1["DEMO"], "K5": V1["K5"]}))),
        ("an old version dropped for a new one", pins_text(*version(2, V2))),
        ("the file deleted", ""),
        ("the old version rewritten beside a new one", pins_text(*version(1, V2), *version(2, V2))),
    ],
)
def test_a_changed_or_removed_pin_is_refused(label: str, newer: str) -> None:
    assert history_problems([("old", ONE), ("new", newer)]) != [], label


def test_a_rewrite_in_an_earlier_commit_is_refused_even_when_later_states_agree() -> None:
    rewritten = pins_text(*version(1, V1 | {"K54": "0" * 64}))
    assert history_problems([("a", ONE), ("b", rewritten), ("tree", rewritten)]) != []


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("a version that is not a whole number", ONE.replace("version = 1", "version = true", 1)),
        ("version zero", ONE.replace("version = 1", "version = 0", 1)),
        ("an unknown size", ONE.replace('"K54"', '"K540"')),
        ("a short digest", ONE.replace("c" * 64, "c" * 63)),
        ("an upper-case digest", ONE.replace("c" * 64, "C" * 64)),
        ("a pin twice", ONE + pins_text((1, "DEMO", "a" * 64))),
        ("an extra field", ONE.replace('size = "DEMO"', 'size = "DEMO"\nnote = "x"')),
        ("a missing field", ONE.replace('size = "DEMO"\n', "", 1)),
        ("an unknown top-level key", "generator = 1\n" + ONE),
        ("not TOML", ONE + "[[pin\n"),
    ],
)
def test_a_malformed_pins_file_is_refused(label: str, text: str) -> None:
    assert parse(text)[1] != [], label
    assert history_problems([("only", text)]) != [], label


@pytest.mark.parametrize(
    ("label", "pins", "current", "digests"),
    [
        ("the output differs from the version's pins", ONE, 1, V1 | {"K5": "9" * 64}),
        ("a size missing from the output", ONE, 1, {"DEMO": V1["DEMO"], "K5": V1["K5"]}),
        ("the version has no pins", ONE, 2, V2),
        ("the version is not the newest pinned", pins_text(*version(1, V1), *version(2, V2)), 1, V1),
        ("no pins at all", "", 1, V1),
    ],
)
def test_the_current_output_must_be_the_newest_versions_pins(
    label: str, pins: str, current: int, digests: dict[str, str]
) -> None:
    assert current_problems(parse(pins)[0], current, digests) != [], label


# --- the history reader, on a scratch repository ---

ISOLATED = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _run(repo: Path, *args: str) -> None:
    cmd = ["git", "-C", str(repo), *args]
    subprocess.run(cmd, check=True, capture_output=True, env=_clean_env() | ISOLATED)  # noqa: S603 -- git with fixed arguments


def test_the_reader_returns_every_committed_state_then_the_working_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    pins = repo / "sub" / "pins.toml"
    pins.parent.mkdir()
    for i, text in enumerate([ONE, ONE + "# note\n"]):
        pins.write_text(text)
        _run(repo, "add", "sub/pins.toml")
        _run(repo, "commit", "-q", "-m", f"c{i}")
    (repo / "other.txt").write_text("x")
    _run(repo, "add", "other.txt")
    _run(repo, "commit", "-q", "-m", "unrelated")
    pins.write_text("tree\n")
    assert [text for _, text in git_states(pins)] == [ONE, ONE + "# note\n", "tree\n"]


def test_the_reader_fails_on_a_shallow_clone(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    for i in range(2):
        (repo / "pins.toml").write_text(ONE + "#" * i)
        _run(repo, "add", "pins.toml")
        _run(repo, "commit", "-q", "-m", f"c{i}")
    _run(tmp_path, "clone", "-q", "--depth", "1", repo.resolve().as_uri(), "shallow")
    with pytest.raises(pytest.fail.Exception, match="shallow clone"):
        git_states(tmp_path / "shallow" / "pins.toml")


def test_the_reader_fails_outside_a_checkout(tmp_path: Path) -> None:
    (tmp_path / "pins.toml").write_text(ONE)
    with pytest.raises(pytest.fail.Exception, match="not in a git checkout"):
        git_states(tmp_path / "pins.toml")


def test_the_reader_ignores_a_git_dir_set_by_a_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Git exports GIT_DIR to some hooks; it must not make the reader read another repository's history.
    repos: dict[str, Path] = {}
    for name, text in (("this", ONE), ("other", pins_text(*version(1, V2)))):
        repo = tmp_path / name
        repo.mkdir()
        _run(repo, "init", "-q")
        (repo / "pins.toml").write_text(text)
        _run(repo, "add", "pins.toml")
        _run(repo, "commit", "-q", "-m", name)
        repos[name] = repo
    monkeypatch.setenv("GIT_DIR", str(repos["other"] / ".git"))
    assert [text for _, text in git_states(repos["this"] / "pins.toml")] == [ONE, ONE]


def test_the_reader_reads_the_committed_blobs_not_replacements(tmp_path: Path) -> None:
    # A `git replace` of a rewritten pins blob by the original would hide the rewrite from a reader that honours it.
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    rewritten = pins_text(*version(1, V1 | {"DEMO": "9" * 64}))
    for i, text in enumerate([ONE, rewritten]):
        (repo / "pins.toml").write_text(text)
        _run(repo, "add", "pins.toml")
        _run(repo, "commit", "-q", "-m", f"c{i}")
    blobs: dict[str, str] = {}
    for name, rev in (("one", "HEAD~1:pins.toml"), ("rewritten", "HEAD:pins.toml")):
        blobs[name] = _git(repo, "rev-parse", rev).stdout.strip()
    _run(repo, "replace", blobs["rewritten"], blobs["one"])
    (repo / "pins.toml").write_text(ONE)  # the working tree agrees with the replacement too
    states = git_states(repo / "pins.toml")
    assert [text for _, text in states] == [ONE, rewritten, ONE]
    assert history_problems(states) != []
