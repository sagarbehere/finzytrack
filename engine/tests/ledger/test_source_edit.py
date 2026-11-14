"""The splicing primitives (S0115; ADR-0009; INV-01, INV-03): locate, route_insert, replace_token, apply_edits.

Each refusal of the ownership rule (journal S0115, Brief) has a must-not-pass test here; the D1 write corpus
(`corpora/ledgers/write/`) is spliced entry by entry at the end, and a few cases get their own test for the trap they
hold (globs, plugins, push blocks, `{}`, elided amounts, comments, CRLF, no final newline, tabs, non-ASCII text).
The generated-ledger properties are `test_source_edit_properties.py`; Fava's routing vectors `test_insert_routing.py`.
"""

from __future__ import annotations

import datetime
import hashlib
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from beancount.core import data
from beancount.parser import parser

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import grammar, overlay
from ledger_engine.ledger.core import source_edit as se

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-03")]

CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "write"
D = datetime.date

KEYWORDS: dict[type, se.Keyword] = {
    data.Transaction: "txn",
    data.Open: "open",
    data.Close: "close",
    data.Balance: "balance",
    data.Pad: "pad",
    data.Note: "note",
    data.Document: "document",
    data.Price: "price",
    data.Event: "event",
    data.Query: "query",
    data.Custom: "custom",
    data.Commodity: "commodity",
}

LEDGER = (
    "; a ledger\n"
    "2024-01-01 open Assets:Cash USD\n"
    "2024-01-01 open Expenses:Food USD\n"
    "\n"
    '2024-01-05 * "Shop" "lunch" ; Assets:Cash in a comment\n'
    "  Expenses:Food  10.50 USD\n"
    "  ; between postings\n"
    "  Assets:Cash  -10.50 USD\n"
    "; after the entry\n"
    '2024-01-06 ! "Shop" "dinner"\n'
    "  Expenses:Food  4.00 USD\n"
    "  Assets:Cash\n"
)


def snap(files: Mapping[str, str | bytes]) -> se.Snapshot:
    return se.Snapshot({p: (t.encode() if isinstance(t, str) else t) for p, t in files.items()})


def located(s: se.Snapshot, path: str, lineno: int, date: datetime.date, keyword: se.Keyword) -> se.Span:
    span = se.locate(s, se.Target(path, lineno, date, keyword))
    assert isinstance(span, se.Span), span
    return span


def applied(current: Mapping[str, bytes], plan: se.EditPlan) -> se.Applied:
    result = se.apply_edits(current, plan)
    assert isinstance(result, se.Applied), result
    return result


def refused(result: object, reason: se.Reason) -> se.Refusal:
    assert isinstance(result, se.Refusal), result
    assert result.reason is reason, result
    return result


def files_of(s: se.Snapshot) -> dict[str, bytes]:
    return dict(s.files)


# ---- locate: Fava's span, from the parser's positions in the current text ------------------------------------------


def test_a_span_is_the_entry_s_lines_up_to_a_column_0_line() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    text = LEDGER
    assert text[span.start : span.end] == (
        '2024-01-05 * "Shop" "lunch" ; Assets:Cash in a comment\n'
        "  Expenses:Food  10.50 USD\n"
        "  ; between postings\n"
        "  Assets:Cash  -10.50 USD\n"
    )
    assert span.sha256 == hashlib.sha256(LEDGER.encode()).hexdigest()


def test_a_span_ends_at_a_blank_line_and_at_the_end_of_the_file() -> None:
    s = snap({"m.bean": LEDGER})
    first = located(s, "m.bean", 3, D(2024, 1, 1), "open")
    assert LEDGER[first.start : first.end] == "2024-01-01 open Expenses:Food USD\n"
    last = located(s, "m.bean", 10, D(2024, 1, 6), "txn")
    assert last.end == len(LEDGER)


def test_trailing_indented_lines_belong_to_the_span_and_go_with_a_delete() -> None:
    """Fava's `find_entry_lines` keeps every indented line after the entry, comments and metadata alike (S0115
    verifier F2): the expected bytes are written from that rule here, not from `locate`."""
    text = (
        "2024-01-01 open Assets:Cash USD\n"
        "\n"
        '2024-01-02 * "x"\n'
        "  Assets:Cash  1 USD\n"
        "  Assets:Bank\n"
        '    memo: "last posting\'s"\n'
        "  ; a trailing note\n"
        "\n"
        "2024-01-03 open Assets:Food USD\n"
        '  note: "meta"\n'
        "    ; deeper note\n"
        "2024-01-04 open Assets:Bank USD\n"
    )
    s = snap({"m.bean": text})
    txn = located(s, "m.bean", 3, D(2024, 1, 2), "txn")
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Delete(txn)]))
    assert out.files["m.bean"] == text.replace(text[text.index("2024-01-02") : text.index("\n2024-01-03")], "").encode()
    opened = located(s, "m.bean", 9, D(2024, 1, 3), "open")
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Delete(opened)]))
    assert (
        out.files["m.bean"]
        == text.replace('2024-01-03 open Assets:Food USD\n  note: "meta"\n    ; deeper note\n', "").encode()
    )


def test_a_whitespace_only_line_ends_a_span_as_in_fava() -> None:
    text = "2024-01-01 open Assets:Cash USD\n  \t\n2024-01-02 open Assets:Bank USD\n"
    span = located(snap({"m.bean": text}), "m.bean", 1, D(2024, 1, 1), "open")
    assert text[span.start : span.end] == "2024-01-01 open Assets:Cash USD\n"


def test_locate_refuses_a_line_where_the_parser_found_no_entry() -> None:
    s = snap({"m.bean": LEDGER})
    for lineno in (1, 4, 6, 9, 99, 0):
        refused(se.locate(s, se.Target("m.bean", lineno, D(2024, 1, 5), "txn")), se.Reason.NO_ENTRY)


def test_locate_refuses_a_first_line_without_the_entry_s_date() -> None:
    s = snap({"m.bean": LEDGER})
    refused(se.locate(s, se.Target("m.bean", 5, D(2024, 1, 4), "txn")), se.Reason.FIRST_LINE)


def test_locate_refuses_a_first_line_without_the_entry_s_keyword() -> None:
    s = snap({"m.bean": LEDGER})
    refused(se.locate(s, se.Target("m.bean", 5, D(2024, 1, 5), "open")), se.Reason.FIRST_LINE)
    refused(se.locate(s, se.Target("m.bean", 2, D(2024, 1, 1), "close")), se.Reason.FIRST_LINE)


def test_every_transaction_flag_shows_the_txn_keyword() -> None:
    text = "".join(f'2024-01-0{i + 1} {flag} "x"\n' for i, flag in enumerate(["*", "!", "txn", "P", "#"]))
    s = snap({"m.bean": text})
    for lineno in range(1, 6):
        located(s, "m.bean", lineno, D(2024, 1, lineno), "txn")


def test_locate_refuses_an_entry_that_runs_past_fava_s_span() -> None:
    """A multi-line string with a line at column 0: Fava's span would stop inside the entry (S0115 Decisions)."""
    text = '2024-01-05 * "Shop" "two\nlines"\n  Expenses:Food  1 USD\n  Assets:Cash\n'
    s = snap({"m.bean": text})
    refused(se.locate(s, se.Target("m.bean", 1, D(2024, 1, 5), "txn")), se.Reason.NOT_WHOLE)


@pytest.mark.parametrize(
    "text",
    [
        '2024-01-01 * "x"\n  Assets:Cash  1 USD\n  Assets:Bank\n  2024-01-02 open Assets:Food\n',
        "2024-01-01 open Assets:Cash USD\n  2024-01-02 open Assets:Food\n",
    ],
)
def test_locate_refuses_an_indented_directive_beancount_kept_after_an_error(text: str) -> None:
    """Beancount drops the entry above and keeps the indented `open`: its line does not start with its date, and its
    lines alone parse with an error, so it is not a whole entry Finzytrack may own."""
    s = snap({"m.bean": text})
    lineno = text.count("\n")
    refused(se.locate(s, se.Target("m.bean", lineno, D(2024, 1, 2), "open")), se.Reason.NOT_WHOLE)


def test_locate_refuses_an_encrypted_file_by_its_name() -> None:
    for name in ("secret.beancount.gpg", "s.ASC", "notes.beancount.asc", "x.GpG"):
        s = snap({name: "2024-01-01 open Assets:Cash USD\n"})
        refused(se.locate(s, se.Target(name, 1, D(2024, 1, 1), "open")), se.Reason.ENCRYPTED)


def test_locate_refuses_a_file_that_is_not_utf8() -> None:
    s = snap({"m.bean": b'2024-01-01 open Assets:Cash USD\n2024-01-02 note Assets:Cash "caf\xe9"\n'})
    refused(se.locate(s, se.Target("m.bean", 1, D(2024, 1, 1), "open")), se.Reason.NOT_UTF8)


def test_locate_refuses_a_file_the_snapshot_does_not_hold() -> None:
    s = snap({"m.bean": LEDGER})
    refused(se.locate(s, se.Target("other.bean", 2, D(2024, 1, 1), "open")), se.Reason.MISSING_FILE)


def test_a_plugin_generated_entry_cannot_be_targeted(tmp_path: Path) -> None:
    """`implicit_prices` makes a Price whose position is its transaction's line: the parser of the text found a
    Transaction there, so the Price is refused (INV-03: the parser of the current text, never the loader's output)."""
    text = (CORPUS / "implicit-prices" / "main.beancount").read_text("utf-8")
    (tmp_path / "main.beancount").write_text(text, "utf-8")  # a copy: a load may write beside the file
    entries, _, _ = port.real_load(str(tmp_path / "main.beancount"))
    prices = [e for e in entries if isinstance(e, data.Price) and e.meta.get("lineno")]
    lines = text.split("\n")
    generated = [p for p in prices if " price " not in lines[int(p.meta["lineno"]) - 1]]
    assert generated, "the case must hold a generated price"
    s = snap({"main.beancount": text})
    for p in generated:
        target = se.Target("main.beancount", p.meta["lineno"], p.date, "price")
        refused(se.locate(s, target), se.Reason.FIRST_LINE)


# ---- apply_edits: replace and delete ------------------------------------------------------------------------------


def test_a_replace_changes_only_the_span() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    new = '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  11.00 USD\n  Assets:Cash\n'
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Replace(span, new)]))
    assert out.files["m.bean"] == (LEDGER[: span.start] + new + LEDGER[span.end :]).encode()


def test_a_delete_removes_only_the_span_and_keeps_the_blank_lines_after_it() -> None:
    text = "2024-01-01 open Assets:Cash USD\n\n\n2024-01-02 open Assets:Bank USD\n"
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 1, D(2024, 1, 1), "open")
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Delete(span)]))
    assert out.files["m.bean"] == b"\n\n2024-01-02 open Assets:Bank USD\n"


def test_apply_refuses_a_stale_snapshot() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    plan = se.EditPlan.of(s, {"m.bean"}, [se.Delete(span)])
    changed = (LEDGER + "; one more line\n").encode()
    refused(se.apply_edits({"m.bean": changed}, plan), se.Reason.STALE)
    refused(se.apply_edits({}, plan), se.Reason.STALE)


def test_apply_refuses_a_span_planned_from_another_text() -> None:
    """A span located in one text, carried into a plan of another: its hash is not the plan's."""
    old = snap({"m.bean": LEDGER})
    span = located(old, "m.bean", 5, D(2024, 1, 5), "txn")
    edited = LEDGER.replace("lunch", "brunch")
    s = snap({"m.bean": edited})
    plan = se.EditPlan.of(s, {"m.bean"}, [se.Delete(span)])
    refused(se.apply_edits(files_of(s), plan), se.Reason.STALE)


def test_apply_refuses_a_span_that_is_not_the_located_one() -> None:
    """A hand-made span (one line short) is not what `locate` finds in the current text."""
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    short = se.Span(span.target, span.sha256, span.first, span.stop - 1, span.start, span.end - 26)
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Delete(short)])), se.Reason.NOT_LOCATED)


def test_apply_refuses_a_file_outside_the_row_s_files() -> None:
    s = snap({"m.bean": LEDGER, "other.bean": "2024-01-01 open Assets:Bank USD\n"})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"other.bean"}, [se.Delete(span)])), se.Reason.NOT_ALLOWED)


def test_apply_refuses_overlapping_spans() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    token = se.replace_token(s, span, 6, grammar.AccountName("Expenses:Food"), grammar.AccountName("Expenses:Dining"))
    assert isinstance(token, se.ReplaceToken)
    for edits in (
        [se.Delete(span), se.Delete(span)],
        [se.Delete(span), token],
        [se.Replace(span, '2024-01-05 * "x"\n'), token],
    ):
        refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, edits)), se.Reason.OVERLAP)


def test_apply_refuses_an_insert_at_an_option_line_whose_entry_it_also_replaces() -> None:
    text = '2000-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\n'
    s = snap({"m.bean": text})
    ins = se.route_insert(s, '2024-01-05 * "x"\n  Expenses:Food  1 USD\n  Assets:Cash\n', "m.bean")
    assert isinstance(ins, se.Insert)
    assert ins.line == 0
    span = located(s, "m.bean", 1, D(2000, 1, 1), "custom")
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, [ins, se.Delete(span)])), se.Reason.OVERLAP)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('2024-01-05 * "x"\n  Expenses:Food  1 USD\n  Assets:Cash\ninclude "other.bean"\n', se.Reason.FORBIDDEN_LINE),
        ('option "title" "x"\n2024-01-05 * "x"\n', se.Reason.FORBIDDEN_LINE),
        ('plugin "beancount.plugins.auto"\n2024-01-05 * "x"\n', se.Reason.FORBIDDEN_LINE),
        ('pushtag #trip\n2024-01-05 * "x"\n', se.Reason.FORBIDDEN_LINE),
        ('2024-01-05 * "x"\npoptag #trip\n', se.Reason.FORBIDDEN_LINE),
        ('pushmeta k: "v"\n2024-01-05 * "x"\n', se.Reason.FORBIDDEN_LINE),
        ('2024-01-05 * "x"\npopmeta k:\n', se.Reason.FORBIDDEN_LINE),
        ('2024-01-05 * "x"\n  include "y"\n', se.Reason.FORBIDDEN_LINE),
        ('2024-01-05 * "x"', se.Reason.BAD_TEXT),  # not complete lines
        ('2024-01-05 * "x"\r\n', se.Reason.BAD_TEXT),  # line ends are the file's, never the text's
        ("", se.Reason.BAD_TEXT),
        ('2024-01-05 * "x"\n2024-01-06 * "y"\n', se.Reason.BAD_TEXT),  # two entries
        ("; only a comment\n", se.Reason.BAD_TEXT),  # no entry
        ('2024-01-05 * "x\n', se.Reason.BAD_TEXT),  # a parser error
        ('  ; leading indent\n2024-01-05 * "x"\n', se.Reason.BAD_TEXT),  # would join the line above
        ('\n2024-01-05 * "x"\n', se.Reason.BAD_TEXT),  # a leading blank line: not at column 0 (verifier F3)
        ('\t2024-01-05 * "x"\n', se.Reason.BAD_TEXT),  # an indented first line
    ],
)
def test_new_text_is_one_entry_and_never_a_file_level_line(text: str, reason: se.Reason) -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Replace(span, text)])), reason)
    refused(se.route_insert(s, text, "m.bean"), reason)


# ---- replace_token ------------------------------------------------------------------------------------------------


def test_replace_token_changes_exactly_the_account_on_the_posting_line() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    edit = se.replace_token(s, span, 8, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [edit]))
    assert out.files["m.bean"] == LEDGER.replace("  Assets:Cash  -10.50", "  Assets:Wallet  -10.50").encode()


def test_replace_token_skips_the_account_written_in_a_comment() -> None:
    s = snap({"m.bean": "2024-01-01 open Assets:Cash USD ; Assets:Cash here\n"})
    span = located(s, "m.bean", 1, D(2024, 1, 1), "open")
    edit = se.replace_token(s, span, 1, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [edit]))
    assert out.files["m.bean"] == b"2024-01-01 open Assets:Wallet USD ; Assets:Cash here\n"


def test_replace_token_skips_a_longer_account_that_starts_with_the_same_text() -> None:
    text = "2024-01-01 pad Assets:CashBox Assets:Cash\n"
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 1, D(2024, 1, 1), "pad")
    edit = se.replace_token(s, span, 1, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [edit]))
    assert out.files["m.bean"] == b"2024-01-01 pad Assets:CashBox Assets:Wallet\n"


@pytest.mark.parametrize(
    ("text", "lineno", "old", "new"),
    [
        ("2024-01-01 pad Assets:Cash Assets:Cash\n", 1, "Assets:Cash", "Assets:Wallet"),  # two tokens
        ('2024-01-01 note Assets:Bank "Assets:Cash"\n', 1, "Assets:Cash", "Assets:Wallet"),  # only in a string
        ("2024-01-01 open Assets:Bank USD ; Assets:Cash\n", 1, "Assets:Cash", "Assets:Wallet"),  # only in a comment
        ("2024-01-01 open Assets:CashBox USD\n", 1, "Assets:Cash", "Assets:Wallet"),  # only a prefix
        ("2024-01-01 open Assets:Cash USD\n", 1, "Assets:Cash", "assets:wallet"),  # new is not an account token
        ("2024-01-01 open Assets:Cash USD\n", 1, "Assets:Cash", "Assets:Wallet USD"),  # new is two tokens
        ("2024-01-01 open Assets:Cash USD\n", 1, "Assets:Cash", "Assets:Cash"),  # nothing to replace
    ],
)
def test_replace_token_refuses_anything_but_exactly_one_account_token(
    text: str, lineno: int, old: str, new: str
) -> None:
    s = snap({"m.bean": text})
    span = located(
        s, "m.bean", 1, D(2024, 1, 1), "pad" if " pad " in text else ("note" if " note " in text else "open")
    )
    refused(
        se.replace_token(s, span, lineno, grammar.AccountName(old), grammar.AccountName(new)), se.Reason.NOT_ONE_ACCOUNT
    )


def test_replace_token_refuses_a_line_that_is_not_the_entry_s_or_a_posting_s() -> None:
    text = '2024-01-05 * "x"\n  ; Assets:Cash\n  Expenses:Food  1 USD\n    note: "Assets:Cash"\n  Assets:Cash\n'
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 1, D(2024, 1, 5), "txn")
    for lineno in (2, 4, 6, 0):
        result = se.replace_token(s, span, lineno, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:W"))
        refused(result, se.Reason.NOT_ENTRY_LINE)


def test_apply_refuses_a_hand_made_token_edit() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    edit = se.replace_token(s, span, 8, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    moved = se.ReplaceToken(edit.span, edit.lineno, edit.old, edit.new, edit.start + 1, edit.end + 1)
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, [moved])), se.Reason.NOT_LOCATED)


def test_replace_token_refuses_a_span_that_is_not_the_located_one() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    short = se.Span(span.target, span.sha256, span.first, span.stop - 1, span.start, span.end - 26)
    result = se.replace_token(s, short, 6, grammar.AccountName("Expenses:Food"), grammar.AccountName("Expenses:X"))
    refused(result, se.Reason.NOT_LOCATED)


def test_grammar_finds_the_one_account_token_with_beancount_s_lexer() -> None:
    hit = grammar.account_token("  Assets:Cash  1 USD ; Assets:Cash", "Assets:Cash", "Assets:Wallet")
    assert hit == grammar.TokenAt(2, 13)
    assert isinstance(grammar.account_token("  Assets:Cash Assets:Cash", "Assets:Cash", "Assets:W"), grammar.Invalid)


# ---- route_insert and the insert edit -----------------------------------------------------------------------------

ROUTED = {
    "main.bean": 'option "title" "x"\ninclude "food.bean"\n2020-01-01 open Assets:Cash USD\n',
    "food.bean": (
        '2024-01-05 * "Shop"\n'
        "  Expenses:Food  10.50 USD\n"
        "  Assets:Cash\n"
        '2020-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\n'
        '2024-01-06 * "Shop"\n'
        "  Expenses:Food  4.00 USD\n"
        "  Assets:Cash\n"
    ),
}
FOOD = '2024-02-01 * "Shop"\n  Expenses:Food  1.00 USD\n  Assets:Cash\n'


def test_an_insert_goes_before_the_matching_option_line() -> None:
    s = snap(ROUTED)
    ins = se.route_insert(s, FOOD, "main.bean")
    assert isinstance(ins, se.Insert)
    assert (ins.path, ins.line) == ("food.bean", 3)
    out = applied(files_of(s), se.EditPlan.of(s, {"food.bean"}, [ins]))
    lines = ROUTED["food.bean"].splitlines(keepends=True)
    assert out.files["food.bean"] == ("".join(lines[:3]) + FOOD + "\n" + "".join(lines[3:])).encode()


def test_an_insert_no_option_matches_is_appended_to_the_default_file_after_a_blank_line() -> None:
    s = snap(ROUTED)
    text = '2024-02-01 * "Cafe"\n  Expenses:Coffee  1.00 USD\n  Assets:Cash\n'
    ins = se.route_insert(s, text, "main.bean")
    assert isinstance(ins, se.Insert)
    assert (ins.path, ins.line) == ("main.bean", None)
    out = applied(files_of(s), se.EditPlan.of(s, {"main.bean"}, [ins]))
    assert out.files["main.bean"] == (ROUTED["main.bean"] + "\n" + text).encode()


def test_an_option_dated_on_the_entry_s_day_does_not_route_it() -> None:
    s = snap(ROUTED)
    ins = se.route_insert(s, '2020-01-01 * "Shop"\n  Expenses:Food  1.00 USD\n  Assets:Cash\n', "main.bean")
    assert isinstance(ins, se.Insert)
    assert (ins.path, ins.line) == ("main.bean", None)


def test_apply_refuses_an_insert_that_is_not_where_routing_puts_it() -> None:
    s = snap(ROUTED)
    ins = se.route_insert(s, FOOD, "main.bean")
    assert isinstance(ins, se.Insert)
    for moved in (
        se.Insert(ins.path, 0, ins.text, ins.default_file, ins.sha256),
        se.Insert("main.bean", None, ins.text, ins.default_file, s.hashes["main.bean"]),
    ):
        refused(
            se.apply_edits(files_of(s), se.EditPlan.of(s, {"food.bean", "main.bean"}, [moved])), se.Reason.NOT_ROUTED
        )


def test_apply_refuses_an_insert_into_a_file_outside_the_row_s_files() -> None:
    s = snap(ROUTED)
    ins = se.route_insert(s, FOOD, "main.bean")
    assert isinstance(ins, se.Insert)
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"main.bean"}, [ins])), se.Reason.NOT_ALLOWED)


def test_inserts_at_one_point_are_ordered_by_date_then_kind_then_text() -> None:
    s = snap({"m.bean": "2020-01-01 open Assets:Cash USD\n"})
    texts = [
        '2024-02-02 * "b"\n  Expenses:Food  1 USD\n  Assets:Cash\n',
        "2024-02-02 open Expenses:Food USD\n",
        '2024-02-01 * "a"\n  Expenses:Food  1 USD\n  Assets:Cash\n',
    ]
    edits = [se.route_insert(s, t, "m.bean") for t in texts]
    assert all(isinstance(e, se.Insert) for e in edits)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [e for e in edits if isinstance(e, se.Insert)]))
    expected = "2020-01-01 open Assets:Cash USD\n" + "".join("\n" + texts[i] for i in (2, 1, 0))
    assert out.files["m.bean"] == expected.encode()


PUSHED = (
    "2020-01-01 open Assets:Cash USD\n"
    "pushtag #trip\n"
    '2020-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\n'
    "poptag #trip\n"
)


@pytest.mark.parametrize(
    "text",
    [
        PUSHED,  # an option inside a pushtag region
        PUSHED.replace("pushtag #trip", 'pushmeta trip: "x"').replace("poptag #trip", "popmeta trip:"),
    ],
)
def test_an_insert_routed_into_a_push_region_by_an_option_is_refused(text: str) -> None:
    refused(se.route_insert(snap({"m.bean": text}), FOOD, "m.bean"), se.Reason.PUSH_REGION)


@pytest.mark.parametrize(
    "text",
    [
        "2020-01-01 open Assets:Cash USD\npushtag #trip\n",
        '2020-01-01 open Assets:Cash USD\npushmeta trip: "x"\n',
        "2020-01-01 open Assets:Cash USD\npushtag #a\npushtag #b\npoptag #a\n",
    ],
)
def test_an_insert_appended_inside_an_open_push_region_is_refused(text: str) -> None:
    refused(se.route_insert(snap({"m.bean": text}), FOOD, "m.bean"), se.Reason.PUSH_REGION)


def test_an_insert_after_a_closed_push_region_is_placed() -> None:
    text = "2020-01-01 open Assets:Cash USD\npushtag #a\npoptag #a\n"
    assert isinstance(se.route_insert(snap({"m.bean": text}), FOOD, "m.bean"), se.Insert)


def test_an_insert_the_parser_cannot_place_is_refused() -> None:
    """The default file ends inside an open string: a probe placed there is read as part of it, so where the entry
    would land, and whether a push region holds it, cannot be told."""
    refused(
        se.route_insert(snap({"m.bean": '2024-01-01 note Assets:Cash "open\n'}), FOOD, "m.bean"), se.Reason.PUSH_REGION
    )


OPTION = '2019-01-01 custom "fava-option" "insert-entry" "Assets:Cash"\n'


@pytest.mark.parametrize(
    "text",
    [
        '2020-01-01 * "prev"\n  Assets:Cash  1 USD\n  Assets:Bank\n  ' + OPTION,
        "2020-01-01 open Assets:Cash USD\n " + OPTION,
    ],
)
def test_an_insert_is_never_routed_by_an_option_that_is_not_a_whole_column_0_entry(text: str) -> None:
    """Beancount keeps the indented option after an error and drops the entry above; an insert before the option's
    line would split that entry's text and bring it back (S0115 verifier F1). Refused, as `locate` refuses it."""
    s = snap({"m.bean": text})
    new = '2021-01-01 * "new"\n  Assets:Cash  5 USD\n  Assets:Bank\n'
    refused(se.route_insert(s, new, "m.bean"), se.Reason.NOT_WHOLE)


def test_an_option_that_is_not_a_whole_entry_is_refused_in_any_file_of_the_set() -> None:
    """The option check holds wherever the option is, not only in the default file (S0115 round 2, G1)."""
    option = '2019-01-01 custom "fava-option" "insert-entry" "Assets:Cash"\n'
    s = snap(
        {
            "main.bean": "2018-01-01 open Assets:Cash USD\n",
            "o.bean": '2018-06-01 * "p"\n  Assets:Cash 1 USD\n  Assets:Bank\n  ' + option,
        }
    )
    new = '2021-01-01 * "new"\n  Assets:Cash  5 USD\n  Assets:Bank\n'
    assert refused(se.route_insert(s, new, "main.bean"), se.Reason.NOT_WHOLE).path == "o.bean"


def test_text_that_cannot_be_utf8_is_refused_not_raised() -> None:
    """A lone surrogate in new text or in a new account name (S0115 verifier F4)."""
    s = snap({"m.bean": LEDGER})
    refused(se.route_insert(s, '2024-02-01 note Assets:Cash "\udc80"\n', "m.bean"), se.Reason.NOT_UTF8)
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    replace = se.Replace(span, '2024-01-05 * "\udc80"\n')
    refused(se.apply_edits(files_of(s), se.EditPlan.of(s, {"m.bean"}, [replace])), se.Reason.NOT_UTF8)
    bad = grammar.AccountName("Assets:\udc80")
    refused(se.replace_token(s, span, 8, grammar.AccountName("Assets:Cash"), bad), se.Reason.NOT_ONE_ACCOUNT)


def test_route_insert_refuses_a_default_file_the_snapshot_does_not_hold() -> None:
    refused(se.route_insert(snap(ROUTED), FOOD, "nowhere.bean"), se.Reason.MISSING_FILE)


def test_route_insert_refuses_when_a_file_of_the_set_cannot_be_read() -> None:
    """Routing reads every file's options; an encrypted or non-UTF-8 file hides them."""
    refused(se.route_insert(snap({**ROUTED, "s.gpg": b"\x85\x02"}), FOOD, "main.bean"), se.Reason.ENCRYPTED)
    refused(se.route_insert(snap({**ROUTED, "b.bean": b"\xff"}), FOOD, "main.bean"), se.Reason.NOT_UTF8)


# ---- line endings and the final newline, per file -----------------------------------------------------------------


def test_a_crlf_file_gets_crlf_text_and_keeps_every_other_byte() -> None:
    text = LEDGER.replace("\n", "\r\n")
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    new = '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  11.00 USD\n  Assets:Cash\n'
    ins = se.route_insert(s, FOOD, "m.bean")
    assert isinstance(ins, se.Insert)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Replace(span, new), ins]))
    expected = text[: span.start] + new.replace("\n", "\r\n") + text[span.end :] + "\r\n" + FOOD.replace("\n", "\r\n")
    assert out.files["m.bean"] == expected.encode()


def test_a_file_without_a_final_newline_keeps_it_missing() -> None:
    text = LEDGER.rstrip("\n")
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 10, D(2024, 1, 6), "txn")
    new = '2024-01-06 * "Shop"\n  Expenses:Food  5.00 USD\n  Assets:Cash\n'
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Replace(span, new)]))
    assert out.files["m.bean"] == (text[: span.start] + new.rstrip("\n")).encode()
    ins = se.route_insert(s, FOOD, "m.bean")
    assert isinstance(ins, se.Insert)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [ins]))
    assert out.files["m.bean"] == (text + "\n\n" + FOOD.rstrip("\n")).encode()


def test_an_insert_into_an_empty_default_file_is_the_entry_alone() -> None:
    s = snap({"m.bean": ""})
    ins = se.route_insert(s, FOOD, "m.bean")
    assert isinstance(ins, se.Insert)
    assert applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [ins])).files["m.bean"] == FOOD.encode()


# ---- revert: the inverse of an applied plan -----------------------------------------------------------------------


def test_revert_returns_the_original_bytes_and_refuses_a_changed_file() -> None:
    s = snap({"m.bean": LEDGER})
    span = located(s, "m.bean", 5, D(2024, 1, 5), "txn")
    ins = se.route_insert(s, FOOD, "m.bean")
    assert isinstance(ins, se.Insert)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [se.Delete(span), ins]))
    assert se.revert(dict(out.files), out) == {"m.bean": LEDGER.encode()}
    refused(se.revert({"m.bean": out.files["m.bean"] + b"\n"}, out), se.Reason.STALE)


# ---- the D1 write corpus ------------------------------------------------------------------------------------------

CASES = tomllib.loads((CORPUS / "cases.toml").read_text("utf-8"))
LOAD_CASES = sorted(k for k, v in CASES.items() if v["expect"] == "load")
SUFFIXES = (".beancount", ".bean", ".asc", ".gpg")


def corpus_files(case: str) -> dict[str, bytes]:
    root = CORPUS / case
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name.endswith(SUFFIXES) and "__pycache__" not in p.parts
    }


def parsed_targets(path: str, text: bytes) -> list[se.Target]:
    entries, _, _ = parser.parse_string(text.decode("utf-8"), path)
    return [se.Target(path, e.meta["lineno"], e.date, KEYWORDS[type(e)]) for e in entries]


def replacement(target: se.Target) -> str:
    return f'{target.date.isoformat()} note Assets:Cash "replaced"\n'


@pytest.mark.parametrize("case", LOAD_CASES)
def test_every_corpus_entry_is_replaced_and_deleted_alone(case: str) -> None:
    """Each entry the parser finds in each ledger file of the case: located, then replaced and deleted on its own;
    the file is the old text with exactly that span changed, no other file changes, and revert gives it back."""
    files = corpus_files(case)
    s = se.Snapshot(files)
    count = 0
    for path, raw in files.items():
        if path.lower().endswith((".gpg", ".asc")):
            continue
        text = raw.decode("utf-8")
        newline = "\r\n" if text.split("\n", 1)[0].endswith("\r") else "\n"
        for target in parsed_targets(path, raw):
            span = se.locate(s, target)
            assert isinstance(span, se.Span), (case, target, span)
            for edit in (se.Replace(span, replacement(target)), se.Delete(span)):
                out = applied(files, se.EditPlan.of(s, {path}, [edit]))
                assert set(out.files) == {path}
                new = "" if isinstance(edit, se.Delete) else edit.text.replace("\n", newline)
                if isinstance(edit, se.Replace) and span.end == len(text) and not text.endswith("\n"):
                    new = new.removesuffix(newline)
                assert out.files[path] == (text[: span.start] + new + text[span.end :]).encode(), (case, target)
                assert se.revert(dict(out.files), out) == {path: raw}
            count += 1
    assert count > 0


def test_the_corpus_entry_count_is_the_one_recorded_in_the_journal() -> None:
    """S0115 Hand-off records the population: every load case, every ledger file, every parsed entry."""
    total = 0
    for case in LOAD_CASES:
        for path, raw in corpus_files(case).items():
            if not path.lower().endswith((".gpg", ".asc")):
                total += len(parsed_targets(path, raw))
    assert (len(LOAD_CASES), total) == (17, 133)


def test_a_glob_included_file_is_edited_in_place_and_the_root_is_untouched() -> None:
    files = corpus_files("includes")
    s = se.Snapshot(files)
    span = located(s, "years/2024.beancount", 2, D(2024, 3, 1), "txn")
    out = applied(files, se.EditPlan.of(s, {"years/2024.beancount"}, [se.Delete(span)]))
    assert set(out.files) == {"years/2024.beancount"}
    assert out.files["years/2024.beancount"] == b"; included by the glob\n"


def test_a_plugin_s_change_never_reaches_the_text() -> None:
    """corpus_tagger adds `#seen` at load; the span is the text as written, and a replace writes only the new text."""
    files = corpus_files("pythonpath-plugin")
    s = se.Snapshot(files)
    span = located(s, "main.beancount", 12, D(2024, 1, 5), "txn")
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [se.Replace(span, '2024-01-05 * "Shop" "lunch"\n')]))
    assert b"#seen" not in out.files["main.beancount"]
    assert out.files["main.beancount"].startswith(files["main.beancount"][: span.start])


def test_entries_inside_a_push_block_are_edited_but_never_inserted_into() -> None:
    files = corpus_files("push-blocks")
    s = se.Snapshot(files)
    span = located(s, "main.beancount", 15, D(2024, 1, 6), "txn")
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [se.Delete(span)]))
    assert b"pushtag #trip\npushmeta" in out.files["main.beancount"]
    assert b"popmeta location:\npoptag #trip\n" in out.files["main.beancount"]
    assert isinstance(se.route_insert(s, FOOD, "main.beancount"), se.Insert)  # after the block: placed
    inside = files["main.beancount"].replace(
        b"popmeta location:\n", b'2020-01-01 custom "fava-option" "insert-entry" "Expenses:Food"\npopmeta location:\n'
    )
    refused(se.route_insert(se.Snapshot({"main.beancount": inside}), FOOD, "main.beancount"), se.Reason.PUSH_REGION)


def test_elided_amounts_and_empty_costs_stay_as_written() -> None:
    files = corpus_files("elided-and-reductions")
    s = se.Snapshot(files)
    span = located(s, "main.beancount", 15, D(2024, 1, 6), "txn")
    edit = se.replace_token(
        s, span, 17, grammar.AccountName("Assets:Bank:Checking"), grammar.AccountName("Assets:Cash")
    )
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [edit]))
    new = out.files["main.beancount"]
    assert b"  Assets:Broker:Fund  -1 FUND {} @ 6.00 USD\n" in new
    assert b"  Assets:Cash\n2024-01-06" in new  # the elided amount of the entry above
    assert b"  Income:Gains" in new
    assert new.count(b"\n") == files["main.beancount"].count(b"\n")


def test_comments_around_a_replaced_entry_stay() -> None:
    files = corpus_files("comments")
    s = se.Snapshot(files)
    span = located(s, "main.beancount", 12, D(2024, 1, 5), "txn")
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [se.Replace(span, '2024-01-05 * "Shop" "lunch"\n')]))
    assert out.files["main.beancount"].endswith(b'2024-01-05 * "Shop" "lunch"\n; a comment after the entry\n')
    assert b"; a comment between directives\n\n" in out.files["main.beancount"]


def test_the_crlf_case_stays_crlf() -> None:
    files = corpus_files("crlf")
    s = se.Snapshot(files)
    ins = se.route_insert(s, FOOD, "main.beancount")
    assert isinstance(ins, se.Insert)
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [ins]))
    assert out.files["main.beancount"].count(b"\n") == out.files["main.beancount"].count(b"\r\n")


def test_the_no_final_newline_case_keeps_it_missing() -> None:
    files = corpus_files("no-final-newline")
    s = se.Snapshot(files)
    ins = se.route_insert(s, FOOD, "main.beancount")
    assert isinstance(ins, se.Insert)
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [ins]))
    assert not out.files["main.beancount"].endswith(b"\n")
    assert out.files["main.beancount"].startswith(files["main.beancount"])


def test_a_tab_indented_posting_s_account_is_replaced() -> None:
    files = corpus_files("tabs")
    s = se.Snapshot(files)
    text = files["main.beancount"].decode()
    lineno = next(i for i, line in enumerate(text.split("\n"), 1) if line.startswith("\tAssets:Cash"))
    entry = max(
        (t for t in parsed_targets("main.beancount", files["main.beancount"]) if t.lineno < lineno),
        key=lambda t: t.lineno,
    )
    span = located(s, "main.beancount", entry.lineno, entry.date, entry.keyword)
    edit = se.replace_token(s, span, lineno, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files, se.EditPlan.of(s, {"main.beancount"}, [edit]))
    assert out.files["main.beancount"].count(b"\tAssets:Wallet") == 1


def test_non_ascii_text_before_a_token_keeps_its_bytes() -> None:
    text = '2024-01-05 * "Caf\u00e9 \u65e5\u672c" "\u20ac"\n  Expenses:Food  1 USD ; \u00fc\n  Assets:Cash\n'
    s = snap({"m.bean": text})
    span = located(s, "m.bean", 1, D(2024, 1, 5), "txn")
    edit = se.replace_token(s, span, 3, grammar.AccountName("Assets:Cash"), grammar.AccountName("Assets:Wallet"))
    assert isinstance(edit, se.ReplaceToken)
    out = applied(files_of(s), se.EditPlan.of(s, {"m.bean"}, [edit]))
    assert out.files["m.bean"] == text.replace("  Assets:Cash\n", "  Assets:Wallet\n").encode()


def test_the_insert_entry_case_routes_food_into_its_own_file() -> None:
    files = corpus_files("insert-entry")
    s = se.Snapshot(files)
    ins = se.route_insert(s, FOOD, "main.beancount")
    assert isinstance(ins, se.Insert)
    assert (ins.path, ins.line) == ("food.beancount", 4)


@pytest.mark.parametrize("case", ["encrypted-gpg", "encrypted-asc", "asc-plain"])
def test_an_encrypted_corpus_file_is_never_edited(case: str) -> None:
    """Refused by the name, as the save's step 0 refuses it (ADR-0059): `asc-plain` too."""
    files = corpus_files(case)
    s = se.Snapshot(files)
    name = next(p for p in files if p.lower().endswith((".gpg", ".asc")))
    refused(se.locate(s, se.Target(name, 1, D(2024, 1, 1), "txn")), se.Reason.ENCRYPTED)


def test_the_encrypted_names_are_the_overlay_s() -> None:
    """One rule for what is encrypted (ADR-0059): the overlay's candidate rule, which the save's step 0 uses too."""
    assert se._ENCRYPTED == overlay.ENCRYPTED_SUFFIXES  # pyright: ignore[reportPrivateUsage]
