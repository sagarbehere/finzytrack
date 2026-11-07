"""Where a copy puts each source file and which lines it rewrites (`ledger_engine.ledger.core.copy_layout`;
`design/specs/engine-jobs.md` §8.2, the copy; S0081). Written from the owner's decision 3 of 2026-11-07: "Copy every
file the ledger loads into the new workspace folder. Rewrite only the include lines and the documents option that
point outside, so they point to the copies. ADR-0017 already says copied workspaces write
`option "documents" "documents"`, so a v0.3 ledger's `../documents/<stem>-<hash>` comes in as `documents/`."
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from ledger_engine.ledger.core.copy_layout import Layout, Refusal, SourceSet, back, layout, place, rewrite

B = "/data/ledgers"
ROOT = f"{B}/main.beancount"


def src(
    files: Sequence[str] = (ROOT,),
    *,
    missing: Sequence[str] = (),
    globs: Sequence[str] = (),
    documents: Sequence[str] = (),
    document_files: Sequence[str] = (),
) -> SourceSet:
    return SourceSet(ROOT, tuple(files), tuple(missing), tuple(globs), tuple(documents), tuple(document_files))


def parse_of(
    includes: Sequence[str], documents: Sequence[str] = ()
) -> tuple[Callable[[], tuple[Sequence[str], Sequence[str]]], list[int]]:
    calls: list[int] = []

    def parse() -> tuple[Sequence[str], Sequence[str]]:
        calls.append(1)
        return includes, documents

    return parse, calls


# -- the layout ---------------------------------------------------------------------------------------------------


def test_files_under_the_ledgers_folder_keep_their_places() -> None:
    lay = layout(src([ROOT, f"{B}/prices/p.beancount"]))
    assert lay == Layout(B, ())
    assert place(lay, f"{B}/prices/p.beancount") == "prices/p.beancount"
    assert place(lay, ROOT) == "main.beancount"


def test_outside_files_go_under_outside_kept_relative_to_their_common_folder() -> None:
    lay = layout(src([ROOT, "/data/shared/a/rates.beancount", "/data/shared/b/more.beancount"]))
    assert place(lay, "/data/shared/a/rates.beancount") == "outside/a/rates.beancount"
    assert place(lay, "/data/shared/b/more.beancount") == "outside/b/more.beancount"


def test_a_documents_root_outside_comes_in_as_documents_and_a_second_as_documents_2() -> None:
    lay = layout(src(documents=["/data/documents/books-1a2b", "/other/docs"]))
    assert place(lay, "/data/documents/books-1a2b/Assets/Bank/x.pdf") == "documents/Assets/Bank/x.pdf"
    assert place(lay, "/other/docs") == "documents-2"


def test_a_name_the_folder_already_uses_is_never_taken_whatever_its_case() -> None:
    lay = layout(src([ROOT, f"{B}/Outside/x.beancount", "/elsewhere/y.beancount"], documents=[f"{B}/documents", "/d"]))
    assert place(lay, "/elsewhere/y.beancount") == "outside-2/y.beancount"
    assert place(lay, "/d/a.pdf") == "documents-2/a.pdf"
    assert place(lay, f"{B}/documents/z.pdf") == "documents/z.pdf", "a root under B stays where it is"


def test_a_documents_root_that_holds_the_ledgers_folder_never_moves_its_files() -> None:
    lay = layout(src(documents=["/data"]))
    assert place(lay, ROOT) == "main.beancount"
    assert place(lay, "/data/documents/x.pdf") == "documents/documents/x.pdf"


def test_a_glob_reaching_outside_counts_for_the_outside_folder() -> None:
    lay = layout(src(globs=["/data/ledgers/../shared/*.beancount"]))
    assert place(lay, "/data/shared/q.beancount") == "outside/q.beancount"


def test_back_maps_the_copys_paths_to_the_sources_in_one_pass() -> None:
    lay = layout(src([ROOT, "/data/shared/r.beancount"], documents=["/data/documents/b"]))
    pattern, table = back(lay, "/T/copy")

    def b(s: str) -> str:
        return pattern.sub(lambda m: table[m.group(0)], s)

    assert b("/T/copy/main.beancount") == ROOT
    assert b("/T/copy/outside/r.beancount") == "/data/shared/r.beancount"
    assert b('File "/T/copy/documents/A/x.pdf" does not exist') == 'File "/data/documents/b/A/x.pdf" does not exist'
    assert b("/T/copy/documents") == "/data/documents/b"
    assert b("/T/copy") == B
    assert b("/T/copy/documentsX/y") == f"{B}/documentsX/y", "a longer name is not the moved folder"
    assert b("/T/copy-2/main.beancount") == "/T/copy-2/main.beancount", "a sibling folder is not the copy"


# -- the rewrites -------------------------------------------------------------------------------------------------


def test_a_file_whose_lines_stay_is_not_rewritten_and_not_parsed() -> None:
    lay = layout(src([ROOT, f"{B}/p.beancount"]))
    parse, calls = parse_of(["p.beancount"])
    assert rewrite(lay, ROOT, b'include "./p.beancount"\n', is_root=True, parse=parse) is None
    assert calls == []


def test_an_include_reaching_outside_points_to_the_copy() -> None:
    lay = layout(src([ROOT, "/data/shared/r.beancount"]))
    data = b'2026-01-01 open Assets:Cash\ninclude "../shared/r.beancount" ; prices\n'
    parse, _ = parse_of(["../shared/r.beancount"])
    assert rewrite(lay, ROOT, data, is_root=True, parse=parse) == (
        b'2026-01-01 open Assets:Cash\ninclude "outside/r.beancount" ; prices\n'
    )


def test_an_absolute_include_becomes_relative() -> None:
    lay = layout(src([ROOT, f"{B}/sub/p.beancount"]))
    parse, _ = parse_of([f"{B}/sub/p.beancount"])
    assert rewrite(lay, ROOT, f'include "{B}/sub/p.beancount"\n'.encode(), is_root=True, parse=parse) == (
        b'include "sub/p.beancount"\n'
    )


def test_an_outside_file_that_includes_back_into_the_folder_is_rewritten() -> None:
    lay = layout(src([ROOT, "/data/shared/r.beancount", f"{B}/p.beancount"]))
    parse, _ = parse_of(["../ledgers/p.beancount"])
    out = rewrite(lay, "/data/shared/r.beancount", b'include "../ledgers/p.beancount"\n', is_root=False, parse=parse)
    assert out == b'include "../p.beancount"\n'


def test_a_glob_reaching_outside_keeps_its_wildcard() -> None:
    lay = layout(src(globs=[f"{B}/../shared/*.beancount"]))
    parse, _ = parse_of(["../shared/*.beancount"])
    out = rewrite(lay, ROOT, b'include "../shared/*.beancount"\n', is_root=True, parse=parse)
    assert out == b'include "outside/*.beancount"\n'


def test_the_v03_documents_option_comes_in_as_documents() -> None:
    lay = layout(src(documents=["/data/documents/books-1a2b"]))
    parse, _ = parse_of([], ["../documents/books-1a2b"])
    out = rewrite(lay, ROOT, b'option "documents" "../documents/books-1a2b"\n', is_root=True, parse=parse)
    assert out == b'option "documents" "documents"\n'


def test_a_documents_option_outside_the_root_file_is_never_rewritten() -> None:
    """Beancount reads `option "documents"` from the root only."""
    lay = layout(src(["/data/ledgers/main.beancount", f"{B}/inc.beancount"], documents=[]))
    parse, calls = parse_of([])
    assert rewrite(lay, f"{B}/inc.beancount", b'option "documents" "/x"\n', is_root=False, parse=parse) is None
    assert calls == []


def test_a_line_count_is_kept_by_every_rewrite() -> None:
    lay = layout(src([ROOT, "/x/r.beancount"]))
    data = b'a\r\ninclude "/x/r.beancount"\r\nb\n'
    parse, _ = parse_of(["/x/r.beancount"])
    out = rewrite(lay, ROOT, data, is_root=True, parse=parse)
    assert isinstance(out, bytes)
    assert out.splitlines() == [b"a", b'include "outside/r.beancount"', b"b"]


@pytest.mark.parametrize(
    ("data", "parsed"),
    [
        (b'include "/x/r.beancount"\n', ["/x/r.beancount", "/x/hidden.beancount"]),  # a line the shape misses
        (b'2026-01-01 note A "x\ninclude "/x/r.beancount"\n"\n', []),  # inside a multi-line string
        (b'include "/x/r\\"q.beancount"\n', ['/x/r"q.beancount']),  # an escaped quote
    ],
)
def test_lines_that_are_not_exactly_what_beancount_parsed_refuse(data: bytes, parsed: list[str]) -> None:
    lay = layout(src([ROOT, "/x/r.beancount", "/x/hidden.beancount"]))
    parse, _ = parse_of(parsed)
    assert rewrite(lay, ROOT, data, is_root=True, parse=parse) == Refusal(ROOT, 0)


def test_a_new_path_that_needs_a_quote_refuses_at_its_line() -> None:
    lay = layout(src([ROOT, '/x/a"b.beancount']))
    parse, _ = parse_of(["/x/a"])
    lay = Layout(B, (("/x", 'out"side'),))
    assert rewrite(lay, ROOT, b'\ninclude "/x/a"\n', is_root=True, parse=parse) == Refusal(ROOT, 2)


def test_an_include_with_no_place_in_the_copy_refuses() -> None:
    lay = Layout(B, ())
    parse, _ = parse_of(["/nowhere/a.beancount"])
    assert rewrite(lay, ROOT, b'include "/nowhere/a.beancount"\n', is_root=True, parse=parse) == Refusal(ROOT, 0)
