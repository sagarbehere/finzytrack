"""Where a copy puts each source file and which lines it rewrites (`ledger_engine.ledger.core.copy_layout`;
`design/specs/engine-jobs.md` §8.2, the copy; S0081). Written from the owner's decision 3 of 2026-11-07: "Copy every
file the ledger loads into the new workspace folder. Rewrite only the include lines and the documents option that
point outside, so they point to the copies. ADR-0017 already says copied workspaces write
`option "documents" "documents"`, so a v0.3 ledger's `../documents/<stem>-<hash>` comes in as `documents/`."
"""

from __future__ import annotations

from collections.abc import Sequence

from ledger_engine.ledger.copy import parsed_from
from ledger_engine.ledger.core.copy_layout import (
    Layout,
    Parsed,
    Refusal,
    Rewritten,
    SourceSet,
    back,
    layout,
    lines_of,
    norm_path,
    place,
    rewrite,
)

B = "/data/ledgers"
ROOT = f"{B}/main.beancount"


def src(
    files: Sequence[str] = (ROOT,),
    *,
    missing: Sequence[str] = (),
    globs: Sequence[str] = (),
    documents: Sequence[str] = (),
    document_files: Sequence[str] = (),
    listed: Sequence[str] = (),
) -> SourceSet:
    return SourceSet(
        ROOT, tuple(files), tuple(missing), tuple(globs), tuple(documents), tuple(document_files), tuple(listed)
    )


def rw(lay: Layout, file: str, data: bytes, *, root: bool = True, parsed: Parsed | None = None) -> object:
    """`rewrite` with what Beancount's own parser reads from `data` (or `parsed`, a made-up reading)."""
    if parsed is None:
        parsed = parsed_from(data, file)
        if not root:
            parsed = Parsed(parsed.includes, (), parsed.document_lines)
    return rewrite(lay, file, data, parsed)


def text(out: object) -> bytes:
    assert isinstance(out, Rewritten), out
    return out.data


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
    assert rw(lay, ROOT, b'include "./p.beancount"\n') is None


def test_an_include_reaching_outside_points_to_the_copy() -> None:
    lay = layout(src([ROOT, "/data/shared/r.beancount"]))
    data = b'2026-01-01 open Assets:Cash\ninclude "../shared/r.beancount" ; prices\n'
    out = rw(lay, ROOT, data)
    assert text(out) == b'2026-01-01 open Assets:Cash\ninclude "outside/r.beancount" ; prices\n'
    assert isinstance(out, Rewritten)
    assert out.quoted == (("outside/r.beancount", "../shared/r.beancount"),), "for the error a missing one gives"


def test_an_absolute_include_becomes_relative() -> None:
    lay = layout(src([ROOT, f"{B}/sub/p.beancount"]))
    assert text(rw(lay, ROOT, f'include "{B}/sub/p.beancount"\n'.encode())) == b'include "sub/p.beancount"\n'


def test_an_outside_file_that_includes_back_into_the_folder_is_rewritten() -> None:
    lay = layout(src([ROOT, "/data/shared/r.beancount", f"{B}/p.beancount"]))
    out = rw(lay, "/data/shared/r.beancount", b'include "../ledgers/p.beancount"\n', root=False)
    assert text(out) == b'include "../p.beancount"\n'


def test_a_glob_reaching_outside_keeps_its_wildcard() -> None:
    lay = layout(src(globs=[f"{B}/../shared/*.beancount"]))
    out = rw(lay, ROOT, b'include "../shared/*.beancount"\n')
    assert text(out) == b'include "outside/*.beancount"\n'


def test_the_v03_documents_option_comes_in_as_documents() -> None:
    lay = layout(src(documents=["/data/documents/books-1a2b"]))
    out = rw(lay, ROOT, b'option "documents" "../documents/books-1a2b"\n')
    assert text(out) == b'option "documents" "documents"\n'


def test_a_documents_option_outside_the_root_file_is_never_rewritten() -> None:
    """Beancount reads `option "documents"` from the root only."""
    lay = layout(src(["/data/ledgers/main.beancount", f"{B}/inc.beancount"], documents=[]))
    assert rw(lay, f"{B}/inc.beancount", b'option "documents" "/x"\n', root=False) is None


def test_a_line_count_is_kept_by_every_rewrite() -> None:
    lay = layout(src([ROOT, "/x/r.beancount"]))
    data = b'a\r\ninclude "/x/r.beancount"\r\nb\n'
    out = rw(lay, ROOT, data, parsed=Parsed(["/x/r.beancount"], (), ()))
    assert text(out).splitlines() == [b"a", b'include "outside/r.beancount"', b"b"]


def test_lines_that_are_not_exactly_what_beancount_parsed_refuse() -> None:
    """A line Beancount reads that the shape does not find (here made up) refuses: never a half-rewritten file."""
    lay = layout(src([ROOT, "/x/r.beancount", "/x/hidden.beancount"]))
    made_up = Parsed(["/x/r.beancount", "/x/hidden.beancount"], (), ())
    assert rw(lay, ROOT, b'include "/x/r.beancount"\n', parsed=made_up) == Refusal(ROOT, 0)


def test_an_escaped_quote_in_a_moved_include_refuses() -> None:
    lay = layout(src([ROOT, '/x/r"q.beancount']))
    assert rw(lay, ROOT, b'include "/x/r\\"q.beancount"\n') == Refusal(ROOT, 0)


def test_an_include_inside_a_multi_line_string_is_not_an_include_and_stays() -> None:
    lay = layout(src([ROOT, "/x/r.beancount"]))
    data = b'2026-01-01 note Assets:A "x\ninclude "/x/r.beancount"\n'
    assert parsed_from(data, ROOT).includes == []
    assert rw(lay, ROOT, data) is None


def test_an_indented_include_beancount_reads_is_rewritten() -> None:
    """r1 verifier F3: Beancount reads an indented `include`; the shape finds it too."""
    lay = layout(src([ROOT, "/data/gone.beancount"]))
    data = b'2026-01-01 open Assets:Cash EUR\n  include "../gone.beancount"\n'
    assert parsed_from(data, ROOT).includes == ["../gone.beancount"]
    assert text(rw(lay, ROOT, data)) == b'2026-01-01 open Assets:Cash EUR\n  include "outside/gone.beancount"\n'


def test_a_document_directive_outside_is_pointed_at_its_copy() -> None:
    """r1 F3, owner's decision 3: the copy is self-contained; a v0.3 ledger-relative `document` path moves in."""
    doc = "/data/documents/books-1a2b/Assets/Bank/s.pdf"
    lay = layout(src(documents=["/data/documents/books-1a2b"], document_files=[doc, "/data/r/x.pdf"]))
    data = (
        b"2026-01-01 open Assets:Bank\n"
        b'2026-01-07 document Assets:Bank "../documents/books-1a2b/Assets/Bank/s.pdf" #t\n'
        b'2026-01-08 document Assets:Bank "/data/r/x.pdf"\n'
    )
    assert text(rw(lay, ROOT, data)) == (
        b"2026-01-01 open Assets:Bank\n"
        b'2026-01-07 document Assets:Bank "documents/Assets/Bank/s.pdf" #t\n'
        b'2026-01-08 document Assets:Bank "outside/x.pdf"\n'
    )


def test_an_absolute_document_path_inside_the_folder_becomes_relative() -> None:
    lay = layout(src(document_files=[f"{B}/r/x.pdf"]))
    data = f'2026-01-01 open Assets:Bank\n2026-01-08 document Assets:Bank "{B}/r/x.pdf"\n'.encode()
    assert text(rw(lay, ROOT, data)) == b'2026-01-01 open Assets:Bank\n2026-01-08 document Assets:Bank "r/x.pdf"\n'


def test_a_document_line_whose_text_does_not_name_beancounts_file_refuses() -> None:
    lay = layout(src(document_files=["/x/a.pdf"]))
    made_up = Parsed((), (), ((1, "/x/b.pdf"),))
    assert rw(lay, ROOT, b'2026-01-01 document A:B "/x/a.pdf"\n', parsed=made_up) == Refusal(ROOT, 1)


def test_a_new_path_that_needs_a_quote_refuses_at_its_line() -> None:
    lay = Layout(B, (("/x", 'out"side'),))
    assert rw(lay, ROOT, b'\ninclude "/x/a"\n', parsed=Parsed(["/x/a"], (), ())) == Refusal(ROOT, 2)


def test_an_include_with_no_place_in_the_copy_refuses() -> None:
    lay = Layout(B, ())
    assert rw(lay, ROOT, b'include "/nowhere/a.beancount"\n') == Refusal(ROOT, 0)


def test_a_documents_root_at_the_ledgers_folder_reserves_every_name_it_holds() -> None:
    """r1 F2: the listing's own names are never taken for `outside` or `documents`."""
    lay = layout(src([ROOT, "/data/shared/s.beancount"], documents=[B], listed=[f"{B}/outside/statement.pdf"]))
    assert place(lay, "/data/shared/s.beancount") == "outside-2/s.beancount"
    assert place(lay, f"{B}/outside/statement.pdf") == "outside/statement.pdf"


def test_lines_end_at_a_newline_only_as_beancount_counts_them() -> None:
    """r2 F4: a lone `\r` is no line break to Beancount's lexer."""
    assert lines_of(b"a\rb\nc\r\n\nd") == [b"a\rb\n", b"c\r\n", b"\n", b"d"]
    assert lines_of(b"x\n") == [b"x\n"]
    assert lines_of(b"") == []


def test_a_lone_carriage_return_does_not_shift_a_document_line() -> None:
    lay = layout(src(document_files=["/data/r/r.pdf"]))
    data = b'2026-01-01 open Assets:Bank\n; a\rb\n2026-01-08 document Assets:Bank "../r/r.pdf"\n'
    assert (
        text(rw(lay, ROOT, data))
        == b'2026-01-01 open Assets:Bank\n; a\rb\n2026-01-08 document Assets:Bank "outside/r.pdf"\n'
    )


def test_one_file_has_one_name_whatever_its_absolute_spelling() -> None:
    """r2 F3: Beancount keeps an absolute `document` path as written."""
    assert norm_path("/x/rec/../rec/r.pdf") == "/x/rec/r.pdf"
    assert norm_path("//x/r.pdf") == "/x/r.pdf"
    assert norm_path("///x//r.pdf") == "/x/r.pdf"
    lay = layout(src(document_files=["/x/rec/r.pdf"]))
    data = b'2026-01-08 document A:B "/x/rec/../rec/r.pdf"\n2026-01-09 document A:B "//x/rec/r.pdf"\n'
    assert (
        text(rw(lay, ROOT, data))
        == b'2026-01-08 document A:B "outside/r.pdf"\n2026-01-09 document A:B "outside/r.pdf"\n'
    )
