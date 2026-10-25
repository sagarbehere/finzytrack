"""P1 integrity tests for the import pipeline (review:27 §1(d), Appendix B: M6): I5, L4-01, L4-02, L4-03, L4-06.

Spec: ADR-0027 (statement lines are anchor postings, the other side an open slot; only open slots are suggestion
targets; matching runs at parse and again at commit, duplicates are skipped and reported with an explicit override
`~dup<n>`; one tabular reader on the kernel parsers), ADR-0026 (`import-key` elements `v<N>:<kind>:<ref>[~dup<n>]` on
every anchor posting; `content_hash` is never written; entry meta only from the contract list), INV-14 (nothing
silently dropped, guessed or defaulted; re-import idempotent), review:L4 §1.4 to §1.9 (the Draft contract, the
`debit_credit_columns` convention, `match`, the commit) and review:L8 §1.3 acceptance tests 3 and 6.

Interface: `importing/` with `core/` and `usecases.py` (ARCHITECTURE.md §2.2, §9.3). The data contract (`Draft`,
`DraftPosting`, `StatementLine`, `Provenance`, `Match`) is review:L4 §1.4's, which ADR-0027 adopts ("one Draft
contract"). Assumed, not decided: the module `app.importing.core.model` for it; `suggest.targets(drafts)` and
`suggest.apply(drafts, suggestions)`; `match.match(drafts, index)`; `commit.plan_commit(drafts, decisions, index)`
returning `.inserts` (entry texts) and `.skipped` (draft keys); `sources.adapt_tabular(grid, rule)` returning a
ParseReport (`.lines`, `.rejected`) with `TabularRule`; a ledger index built by `match.index_from_text(text)`.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.integrity.planned import planned

MODEL = "app.importing.core.model"


def _amount(number: str, currency: str) -> Any:
    return planned("app.domain", "Amount")(Decimal(number), currency)


def _posting(account: str | None, number: str | None = None, currency: str = "USD", **kw: Any) -> Any:
    units = _amount(number, currency) if number is not None else None
    return planned(MODEL, "DraftPosting")(account=account, units=units, **kw)


def _draft(key: str, postings: list[Any], *, anchor: int | None = 0, source: str = "ofx", **kw: Any) -> Any:
    provenance = planned(MODEL, "Provenance")(source=source, file_sha256=None, locator=key)
    fields: dict[str, Any] = {
        "key": key,
        "date": date(2026, 3, 4),
        "flag": "*",
        "payee": "Payee",
        "narration": "",
        "tags": frozenset[str](),
        "links": frozenset[str](),
        "meta": dict[str, Any](),
        "postings": tuple(postings),
        "anchor": anchor,
        "provenance": provenance,
    } | kw
    return planned(MODEL, "Draft")(**fields)


@pytest.mark.fixme(id="L4-01", due="M6", reason="capability not built")
def test_autocategorize_fills_only_open_slots() -> None:
    targets = planned("app.importing.core.suggest", "targets")
    apply = planned("app.importing.core.suggest", "apply")
    suggestion = planned(MODEL, "Suggestion")
    statement = _draft("s1", [_posting("Liabilities:Card", "-12.00"), _posting(None)])
    buy = _draft(
        "b1",
        [_posting("Assets:Broker:AAPL", "2", "AAPL"), _posting("Assets:Broker:Cash", "-380.00")],
        anchor=None,
        source="investment",
    )
    transfer = _draft(
        "t1",
        [_posting("Assets:Savings", "500.00"), _posting("Assets:Checking", "-500.00")],
        anchor=None,
        source="manual",
    )
    three = _draft(
        "m1",
        [_posting("Assets:Checking", "-30.00"), _posting("Expenses:Food", "20.00"), _posting(None)],
        source="tabular",
    )
    drafts = [statement, buy, transfer, three]

    # Only None slots are targets; a draft with no open slot is never sent (review:L4 §1.7).
    assert sorted(targets(drafts)) == [("m1", 2), ("s1", 1)]
    guess = {
        d.key: (suggestion(slot=i, account="Expenses:Shopping", confidence=0.9, engine="classifier", reason="t"),)
        for d in drafts
        for i in range(len(d.postings))
    }
    after = {d.key: d for d in apply(drafts, guess)}
    assert after["s1"].postings[1].account == "Expenses:Shopping"
    assert after["m1"].postings[2].account == "Expenses:Shopping"
    for key in ("b1", "t1"):  # the cash leg of a buy and the savings leg of a transfer are untouched
        assert after[key].postings == {d.key: d for d in drafts}[key].postings
    assert after["m1"].postings[:2] == three.postings[:2]


LEDGER_WITH_LINE = """2026-01-01 open Liabilities:Card USD
2026-01-01 open Expenses:Food USD
2026-03-04 * "Payee" ""
  Liabilities:Card  -12.00 USD
    import-key: "v1:ofx:FIT123"
  Expenses:Food
"""


@pytest.mark.fixme(id="L4-02", due="M6", reason="capability not built")
def test_commit_rematches_skips_and_reports_duplicates_with_an_explicit_override() -> None:
    match = planned("app.importing.core.match", "match")
    index_from_text = planned("app.importing.core.match", "index_from_text")
    plan_commit = planned("app.importing.core.commit", "plan_commit")
    line = _draft(
        "s1",
        [_posting("Liabilities:Card", "-12.00", meta={"import-key": "v1:ofx:FIT123"}), _posting("Expenses:Food")],
    )

    # At parse the ledger did not have it yet ...
    assert match([line], index_from_text("2026-01-01 open Liabilities:Card USD\n"))["s1"].kind == "new"
    # ... but by commit time it does (a retried POST, a second tab): commit re-matches and skips, whatever the decision.
    current = index_from_text(LEDGER_WITH_LINE)
    plan = plan_commit([line], {"s1": "create"}, current)
    assert plan.inserts == ()
    assert list(plan.skipped) == ["s1"]
    # The explicit override writes it as a confirmed second occurrence (ADR-0026: `~dup<n>`).
    plan = plan_commit([line], {"s1": "create-duplicate"}, current)
    assert len(plan.inserts) == 1
    assert re.search(r'import-key: "v\d+:ofx:FIT123~dup1"', plan.inserts[0])


@pytest.mark.fixme(id="I5", due="M6", reason="capability not built")
def test_duplicate_detection_never_uses_the_legacy_content_hash() -> None:
    """Legacy `content_hash` values are tolerated (read, passed through), never matched and never written.

    The behavioural half shows a legacy entry carrying a `content_hash` but nothing else in common is not a match;
    the source half shows the importing package does not even name the key, so no tier can compare it."""
    match = planned("app.importing.core.match", "match")
    index_from_text = planned("app.importing.core.match", "index_from_text")
    plan_commit = planned("app.importing.core.commit", "plan_commit")
    legacy = """2026-01-01 open Liabilities:Card USD
2025-06-01 * "Other shop" ""
  content_hash: "sha256-of-legacy-input"
  Liabilities:Card  -99.00 USD
  Expenses:Misc
"""
    line = _draft("s1", [_posting("Liabilities:Card", "-12.00"), _posting("Expenses:Food")])

    found = match([line], index_from_text(legacy))["s1"]
    assert found.kind == "new"
    assert not any("content" in r for r in found.reasons)
    assert "content_hash" not in plan_commit([line], {"s1": "create"}, index_from_text(legacy)).inserts[0]
    importing = planned("app.importing", "__file__")
    assert [p for p in Path(importing).parent.rglob("*.py") if "content_hash" in p.read_text()] == []


@pytest.mark.fixme(id="L4-03", due="M6", reason="capability not built")
def test_split_debit_credit_columns_parse_every_row() -> None:
    adapt_tabular = planned("app.importing.core.sources", "adapt_tabular")
    tabular_rule = planned("app.importing.core.sources", "TabularRule")
    rule = tabular_rule(
        date_column=0,
        payee_column=1,
        debit_column=2,
        credit_column=3,
        sign="debit_credit_columns",
        date_format="%d/%m/%Y",
        currency="INR",
        header_rows=1,
    )
    grid = [
        ["Date", "Narration", "Withdrawal", "Deposit"],
        ["01/03/2026", "RENT", "25000.00", "0.00"],  # review:L4 demo D9: legacy CSV gave RENT 0
        ["02/03/2026", "SWIGGY", "450.00", "-"],  # legacy CSV dropped this row
        ["03/03/2026", "SALARY", "", "90000.00"],
        ["04/03/2026", "BOTH", "10.00", "10.00"],  # two present non-zero cells: rejected, never guessed
        ["05/03/2026", "NEITHER", " ", "0"],  # no present cell: rejected, never a zero-amount line
    ]

    report = adapt_tabular(grid, rule)

    # A debit is money out of the statement holder's account: negative on the anchor (review:L4 §1.5).
    assert [(ln.payee, ln.amount) for ln in report.lines] == [
        ("RENT", Decimal("-25000.00")),
        ("SWIGGY", Decimal("-450.00")),
        ("SALARY", Decimal("90000.00")),
    ]
    assert sorted(r.provenance.locator for r in report.rejected) == ["row 5", "row 6"]


@pytest.mark.fixme(id="L4-06", due="M6", reason="capability not built")
def test_tags_and_links_are_kept_on_commit() -> None:
    index_from_text = planned("app.importing.core.match", "index_from_text")
    plan_commit = planned("app.importing.core.commit", "plan_commit")
    line = _draft(
        "s1",
        [_posting("Liabilities:Card", "-12.00"), _posting("Expenses:Travel")],
        tags=frozenset({"trip-goa", "tax"}),
        links=frozenset({"invoice-42"}),
    )

    text = plan_commit([line], {"s1": "create"}, index_from_text("2026-01-01 open Liabilities:Card USD\n")).inserts[0]

    header = text.splitlines()[0]
    assert {"#trip-goa", "#tax", "^invoice-42"} <= set(header.split())
