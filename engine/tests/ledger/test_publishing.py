"""`ledger_engine.ledger.publishing.within`, clause by clause (S0088 round 1, verifier F2; BL-0212 V1): a hint lies
within a stamp only when the stamp names every file, `include` pattern, documents root, `document` file and module
lookup (S0099) the hint names. Each clause has a case that only it refuses (the mutants `within-ignores-files`,
`-globs`, `-documents`, `-document-files`, `-modules`)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ledger_engine.ledger.core.ledger_set import LedgerSet
from ledger_engine.ledger.publishing import within

STAMP = LedgerSet(
    "/L/main.bean",
    ("/L/inc.bean", "/L/main.bean"),
    ("/L/inc.bean",),
    ("/L/docs",),
    ("/L/docs/receipt.pdf",),
    (("plug", ("/L", "/py")),),
)


def test_a_stamp_is_within_itself_and_an_empty_hint_within_it() -> None:
    assert within(STAMP, STAMP)
    assert within(LedgerSet("/L/main.bean", ("/L/main.bean",), (), (), ()), STAMP)


@pytest.mark.parametrize(
    "hint",
    [
        replace(STAMP, files=(*STAMP.files, "/L/other.bean")),  # a file only
        replace(STAMP, globs=(*STAMP.globs, "/L/extra/*.bean")),  # a pattern only
        replace(STAMP, documents=(*STAMP.documents, "/L/more-docs")),  # a documents root only
        replace(STAMP, document_files=(*STAMP.document_files, "/L/docs/other.pdf")),  # a `document` file only
        replace(STAMP, modules=(*STAMP.modules, ("plug.sub", ("/L/plug",)))),  # a module lookup only (S0099)
    ],
    ids=["file", "glob", "documents_root", "document_file", "module"],
)
def test_a_hint_naming_one_thing_the_stamp_lacks_is_not_within(hint: LedgerSet) -> None:
    assert not within(hint, STAMP)
