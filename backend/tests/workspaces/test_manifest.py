"""The workspace manifest `.finzytrack/workspace.json` and the paths it may hold (ADR-0017, ADR-0019, EP-14; S0051).

What the open needs from a folder: who the workspace is (its lineage id, which travels with the folder and is never
the registry key), where its ledger is (a root inside the folder, or `external`), and whether this app may read it
at all (`min_reader`, INV-17). A newer manifest is told apart before anything else is validated, since a newer app
may change any other field (NEWER_WORKSPACE, never WS_CONFIG_INVALID). The golden fixtures in
`corpora/workspaces/manifests/` are read by every reader of this format (EP-14).
"""

from __future__ import annotations

import json
import ntpath
import posixpath
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.paths import InvalidWorkspacePath, WorkspacePath
from app.workspaces.format.manifest import (
    FORMAT,
    MANIFEST_PATH,
    External,
    Inside,
    InvalidReason,
    LineageId,
    Manifest,
    ManifestInvalid,
    ManifestNewer,
    read_manifest,
)

FIXTURES = Path(__file__).resolve().parents[3] / "corpora" / "workspaces" / "manifests"
LINEAGE = "0192f3a4-5b6c-7d8e-9f00-a1b2c3d4e5f6"


def manifest(ledger: str = '{"root": "main.beancount"}', **fields: str) -> bytes:
    values = {"format": "1", "min_reader": "1", "lineage_id": f'"{LINEAGE}"', "ledger": ledger, **fields}
    return ("{" + ", ".join(f'"{k}": {v}' for k, v in values.items() if v != "<omit>") + "}").encode()


# ---- golden fixtures ---------------------------------------------------------------------------------------------


def test_this_app_reads_and_writes_format_1() -> None:
    assert FORMAT == 1
    assert MANIFEST_PATH == ".finzytrack/workspace.json"
    assert str(WorkspacePath.parse(MANIFEST_PATH)) == MANIFEST_PATH


@pytest.mark.cap("CAP-WS-PORTABLE")
def test_a_v1_manifest_with_its_ledger_inside_the_folder() -> None:
    m = read_manifest((FIXTURES / "v1-inside.json").read_bytes())
    assert m == Manifest(
        format=1,
        min_reader=1,
        lineage_id=LineageId("0192f3a4-5b6c-7d8e-9f00-a1b2c3d4e5f6"),
        ledger=Inside(WorkspacePath.parse("books/main.beancount")),
    )


def test_a_v1_manifest_with_an_external_ledger() -> None:
    """ADR-0017: the absolute path of an external ledger lives only in the machine-local registry row."""
    m = read_manifest((FIXTURES / "v1-external.json").read_bytes())
    assert m == Manifest(
        format=1, min_reader=1, lineage_id=LineageId("0192f3a4-5b6c-7d8e-9f00-aabbccddeeff"), ledger=External()
    )


@pytest.mark.inv("INV-17")
def test_a_newer_manifest_this_app_may_still_read_is_read_and_its_extra_fields_ignored() -> None:
    m = read_manifest((FIXTURES / "v2-readable.json").read_bytes())
    assert m == Manifest(
        format=2,
        min_reader=1,
        lineage_id=LineageId("0192f3a4-5b6c-7d8e-9f00-000000000002"),
        ledger=Inside(WorkspacePath.parse("main.beancount")),
    )


@pytest.mark.inv("INV-17")
@pytest.mark.cap("CAP-DATA-NEWER-REFUSE")
def test_a_manifest_that_needs_a_newer_reader_is_newer_whatever_else_it_holds() -> None:
    assert read_manifest((FIXTURES / "v3-newer.json").read_bytes()) == ManifestNewer(format=3, min_reader=2)


@pytest.mark.inv("INV-17")
@given(
    st.integers(min_value=FORMAT + 1, max_value=10**6).flatmap(
        lambda r: st.tuples(st.just(r), st.integers(min_value=r, max_value=10**6))
    ),
    st.dictionaries(st.text(max_size=8), st.text(max_size=8), max_size=3),
)
def test_any_manifest_needing_a_newer_reader_is_newer(versions: tuple[int, int], junk: dict[str, str]) -> None:
    min_reader, fmt = versions
    fields = {k: v for k, v in junk.items() if k not in ("format", "min_reader")}
    data = json.dumps({**fields, "format": fmt, "min_reader": min_reader}).encode()
    assert read_manifest(data) == ManifestNewer(format=fmt, min_reader=min_reader)


# ---- refused, with the field ---------------------------------------------------------------------------------------

INVALID: dict[str, tuple[bytes, str | None, InvalidReason]] = {
    "not UTF-8": (b'{"format": 1, "x": "\xff"}', None, "not-utf8"),
    "a byte-order mark": (b"\xef\xbb\xbf" + manifest(), None, "not-json"),
    "not JSON": (b"{format: 1}", None, "not-json"),
    "empty": (b"", None, "not-json"),
    "a list": (b"[1]", None, "not-an-object"),
    "a duplicate key": (b'{"format": 1, "format": 9, "min_reader": 1}', None, "duplicate-key"),
    "NaN": (manifest(min_reader="NaN"), None, "not-json"),
    "format missing": (manifest(format="<omit>"), "format", "invalid"),
    "format a boolean": (manifest(format="true"), "format", "invalid"),
    "format and min_reader booleans": (manifest(format="true", min_reader="true"), "format", "invalid"),
    "min_reader a boolean": (manifest(min_reader="true"), "min_reader", "invalid"),
    "format a float": (manifest(format="1.0"), "format", "invalid"),
    "format a string": (manifest(format='"1"'), "format", "invalid"),
    "format zero": (manifest(format="0"), "format", "invalid"),
    "min_reader missing": (manifest(min_reader="<omit>"), "min_reader", "invalid"),
    "min_reader zero": (manifest(min_reader="0"), "min_reader", "invalid"),
    "min_reader above format": (manifest(format="1", min_reader="2"), "min_reader", "invalid"),
    "lineage missing": (manifest(lineage_id="<omit>"), "lineage_id", "invalid"),
    "lineage upper case": (manifest(lineage_id=f'"{LINEAGE.upper()}"'), "lineage_id", "invalid"),
    "lineage without dashes": (manifest(lineage_id=f'"{LINEAGE.replace("-", "")}"'), "lineage_id", "invalid"),
    "lineage in braces": (manifest(lineage_id=f'"{{{LINEAGE}}}"'), "lineage_id", "invalid"),
    "lineage a number": (manifest(lineage_id="1"), "lineage_id", "invalid"),
    "ledger missing": (manifest(ledger="<omit>"), "ledger", "invalid"),
    "ledger another word": (manifest(ledger='"internal"'), "ledger", "invalid"),
    "ledger root missing": (manifest(ledger="{}"), "ledger", "invalid"),
    "ledger root absolute": (manifest(ledger='{"root": "/srv/books/main.beancount"}'), "ledger", "invalid"),
    "ledger root escaping": (manifest(ledger='{"root": "../main.beancount"}'), "ledger", "invalid"),
    "ledger root a Windows path": (manifest(ledger='{"root": "C:\\\\books\\\\main.beancount"}'), "ledger", "invalid"),
    "ledger root not a string": (manifest(ledger='{"root": 7}'), "ledger", "invalid"),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_a_malformed_manifest_is_refused_with_its_field(case: str) -> None:
    data, field, reason = INVALID[case]
    assert read_manifest(data) == ManifestInvalid(field=field, reason=reason)


def test_the_refusal_list_is_non_trivial() -> None:
    assert len(INVALID) >= 20
    assert isinstance(read_manifest(manifest()), Manifest)  # the base of every case is itself valid


# ---- WorkspacePath ---------------------------------------------------------------------------------------------------

ACCEPTED = ["main.beancount", "books/2024/main.beancount", ".finzytrack/workspace.json", "a b/c-d_e.bean", "Ü/ß"]
REFUSED = [
    "",
    "/main.beancount",
    "../main.beancount",
    "books/../../x",
    "books/..",
    "./main.beancount",
    "books//main.beancount",
    "books/",
    ".",
    "books\\main.beancount",
    "C:main.beancount",
    "C:/books/main.beancount",
    "//server/share/main.beancount",
    "books/.. /x",  # Windows trims trailing dots and spaces: ".. " is ".."
    "books/.../x",
    "books/main.beancount.",
    "books/main.beancount ",
    "a\x00b",
    # Windows device names, with any extension and in any case: the name opens the device, not a file there.
    "CON",
    "a/NUL",
    "books/COM1.beancount",
    "books/com9",
    "lpt1.txt",
    "books/LPT9.tar.gz",
    "COM\u00b9",
    "CONIN$",
    "conout$.beancount",
    "a/aux.txt",
    "Prn",
    "nul .txt",
    "COM0",
    "LPT0.x",
    # Characters Windows does not allow in a name.
    "books/a?b",
    "books/a*b",
    'books/a"b',
    "books/a<b",
    "books/a>b",
    "books/a|b",
    "books/a\tb",
    "books/a\x1fb",
]
ALSO_ACCEPTED = ["CONSOLE/main.beancount", "books/NULL.beancount", "COM10", "a/LPT.txt", "auxiliary/x", "con-1"]


@pytest.mark.inv("INV-27")
@pytest.mark.parametrize("text", ACCEPTED + ALSO_ACCEPTED)
def test_a_relative_path_inside_the_folder_is_accepted_as_written(text: str) -> None:
    assert str(WorkspacePath.parse(text)) == text


@pytest.mark.inv("INV-27")
@pytest.mark.parametrize("text", REFUSED)
def test_a_path_that_is_absolute_escapes_or_is_not_normal_is_refused(text: str) -> None:
    with pytest.raises(InvalidWorkspacePath):
        WorkspacePath.parse(text)


@pytest.mark.inv("INV-27")
@given(
    st.text(alphabet=st.sampled_from("ab./\\: C\x00"), max_size=12)
    | st.text(max_size=12)
    | st.lists(
        st.sampled_from(["con", "NUL", "Aux", "COM1", "lpt9", "CONIN$", "a", ".txt", " ", "/", "."]), max_size=5
    ).map("".join)
)
def test_an_accepted_path_stays_inside_any_folder_on_any_system(text: str) -> None:
    try:
        p = WorkspacePath.parse(text)
    except InvalidWorkspacePath:
        return
    assert str(p) == text
    posix = posixpath.normpath(posixpath.join("/w", text))
    assert posix.startswith("/w/")
    nt = ntpath.normpath(ntpath.join("C:\\w", text))
    assert nt.startswith("C:\\w\\")
    assert all(part.rstrip(". ") not in ("", "..") for part in text.split("/"))
    assert not ntpath.isreserved(text)  # no device name, no character Windows refuses


def test_a_workspace_path_is_a_value() -> None:
    assert WorkspacePath.parse("a/b") == WorkspacePath.parse("a/b")
    assert WorkspacePath.parse("a/b") != WorkspacePath.parse("a/c")
    assert WorkspacePath.parse("a/b").parts == ("a", "b")
