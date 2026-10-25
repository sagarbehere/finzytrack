"""The blobs a range of commits adds or changes, for the push-time scans (S0031, the M0 gate red team H2).

A range is `BASE..TIP` (the commits reachable from TIP and not from BASE) or a bare `TIP` (its whole history).
Every commit in the range is diffed against each of its parents (a merge against every parent, a root commit
against the empty tree), renames are reported as additions, and each (path, mode, blob) is returned once. So a
blob added in one commit and deleted in the next is still returned, and a file renamed onto a refused name is
returned under its new name. Deleted paths are not returned.

Every read runs with replacement objects off (`--no-replace-objects`, S0031 verifier F2): the objects read are the
ones `git push` sends.

Used by `scripts/check_staged.py --range` and `scripts/privacy_scan.py --range`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

ZERO = "0" * 40
SPEC = re.compile(r"^(?:([0-9a-f]{40})\.\.)?([0-9a-f]{40})$")


@dataclass(frozen=True)
class Blob:
    path: str
    mode: str  # 100644, 100755, 120000 (symlink), 160000 (submodule)
    sha: str


class RangeError(Exception):
    pass


def _git(*args: str, data: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "--no-replace-objects", *args], input=data, capture_output=True)
    if proc.returncode != 0:
        raise RangeError(f"git {' '.join(args[:3])} failed: {proc.stderr.decode(errors='replace').strip()[-300:]}")
    return proc.stdout


def parse_spec(spec: str) -> tuple[str | None, str]:
    """(base or None, tip). Only full 40-hex object names: a range never names a ref, which could move."""
    m = SPEC.match(spec)
    if not m or m.group(2) == ZERO or m.group(1) == ZERO:
        raise RangeError(f"a range is BASE..TIP or TIP with full 40-hex commit names, not {spec!r}")
    return m.group(1), m.group(2)


def changed_blobs(spec: str) -> list[Blob]:
    base, tip = parse_spec(spec)
    commits = _git("rev-list", "--reverse", f"{base}..{tip}" if base else tip)
    if not commits.strip():
        return []
    out = _git("diff-tree", "--stdin", "-r", "-z", "--no-renames", "--root", "-m", "--no-commit-id",
               "--diff-filter=ACMRT", data=commits)
    fields = out.split(b"\0")
    seen: dict[tuple[str, str, str], None] = {}
    i = 0
    while i < len(fields):
        head = fields[i].decode("utf-8", errors="surrogateescape")
        if not head.startswith(":"):
            i += 1
            continue
        parts = head[1:].split()
        if len(parts) != 5 or i + 1 >= len(fields):
            raise RangeError(f"unexpected diff-tree output: {head[:80]!r}")
        path = fields[i + 1].decode("utf-8", errors="surrogateescape")
        seen[(path, parts[1], parts[3])] = None
        i += 2
    return [Blob(p, m, s) for p, m, s in seen]


def read_blobs(shas: list[str]) -> dict[str, bytes]:
    """The content of each blob, read with one `git cat-file --batch`."""
    wanted = list(dict.fromkeys(shas))
    if not wanted:
        return {}
    out = _git("cat-file", "--batch", data="".join(f"{s}\n" for s in wanted).encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for sha in wanted:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode().split()
        if len(header) != 3 or header[0] != sha:
            raise RangeError(f"cat-file: unexpected header {out[pos:nl][:80]!r} for {sha}")
        size = int(header[2])
        blobs[sha] = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return blobs


def commit_messages(spec: str) -> list[tuple[str, str]]:
    """(commit sha, full message) for every commit of the range (S0031 verifier F4)."""
    base, tip = parse_spec(spec)
    out = _git("log", "--format=%H%x00%B%x00", f"{base}..{tip}" if base else tip).decode("utf-8", errors="replace")
    parts = out.split("\0")
    return [(parts[i].strip(), parts[i + 1]) for i in range(0, len(parts) - 1, 2) if SHA.match(parts[i].strip())]


SHA = re.compile(r"^[0-9a-f]{40}$")


def tag_message(sha: str) -> str:
    """The message of an annotated tag object, without its header (tagger name and address are not scanned)."""
    if not SHA.match(sha):
        raise RangeError(f"a tag is named by its 40-hex object name, not {sha!r}")
    raw = _git("cat-file", "tag", sha).decode("utf-8", errors="replace")
    return raw.split("\n\n", 1)[1] if "\n\n" in raw else ""


def blob_at(tip: str, path: str) -> bytes | None:
    """The file `path` in commit `tip`, or None when the commit has no such file."""
    proc = subprocess.run(["git", "--no-replace-objects", "cat-file", "blob", f"{tip}:{path}"], capture_output=True)
    return proc.stdout if proc.returncode == 0 else None
