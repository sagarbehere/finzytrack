"""Properties of the splicing primitives over generated ledgers (S0115; ADR-0009; WS-WRITE acceptance; INV-01).

The generator writes one or two files from blocks it knows the exact place of: entries (transactions with comments
between postings, inline comments that name an account, posting metadata, elided amounts; `open`; `balance`),
column-0 comments, org headings, blank and whitespace-only lines, `pushtag`/`poptag` and `pushmeta`/`popmeta` regions,
an optional Fava `insert-entry` option, LF or CRLF per file, a missing final newline, tab or space indents and
non-ASCII payees. It then picks non-overlapping edits (replace, delete, one account token, inserts) and computes the
expected bytes of every file itself, from the blocks it wrote: that model never calls the module under test.

Properties: (1) the plan's result is exactly the model's bytes, so every byte outside the owned spans is identical
and each owned span holds only its planned text; (2) the same edits in any input order give the same bytes
(planned from one snapshot, applied bottom-up); (3) `revert` of the applied plan returns the original bytes; and
`locate` finds exactly the generator's entry block.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field

import pytest
from beancount.parser import parser
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ledger_engine.ledger import grammar
from ledger_engine.ledger.core import source_edit as se

pytestmark = pytest.mark.inv("INV-01")

ACCOUNTS = ("Assets:Cash", "Assets:CashBox", "Assets:Bank:Checking", "Expenses:Food", "Expenses:Coffee")
PAYEES = ("Shop", "Café Zürich", "日本の店", "Bäckerei €", "Plain")
OPTION_PATTERN = "Expenses:Food"
OPTION_DATE = datetime.date(2000, 1, 1)


@dataclass
class Entry:
    path: str
    lineno: int
    date: datetime.date
    keyword: se.Keyword
    start: int  # char offset of the block in the final text
    end: int
    tokens: list[tuple[int, int, int, str]]  # (lineno, start, end, account): the one account token of that line


@dataclass
class File:
    path: str
    newline: str
    final_newline: bool
    text: str = ""
    entries: list[Entry] = field(default_factory=lambda: list[Entry]())
    option_offset: int | None = None  # the insert-entry option line's char offset


@st.composite
def entry_lines(draw: st.DrawFn, date: datetime.date) -> tuple[se.Keyword, list[tuple[str, str | None]]]:
    """An entry as (line, account on that line or None) pairs, in LF form without the newline."""
    indent = draw(st.sampled_from(["  ", "\t", "    "]))
    kind = draw(st.sampled_from(["txn", "open", "balance"]))
    if kind == "open":
        account = draw(st.sampled_from(ACCOUNTS))
        lines: list[tuple[str, str | None]] = [(f"{date} open {account} USD", account)]
        if draw(st.booleans()):
            lines.append((f'{indent}note: "{draw(st.sampled_from(PAYEES))}"', None))
        return "open", lines
    if kind == "balance":
        account = draw(st.sampled_from(ACCOUNTS))
        return "balance", [(f"{date} balance {account}  1.00 USD", account)]
    flag = draw(st.sampled_from(["*", "!", "txn"]))
    comment = f" ; was {draw(st.sampled_from(ACCOUNTS))}" if draw(st.booleans()) else ""
    lines = [(f'{date} {flag} "{draw(st.sampled_from(PAYEES))}" "n"{comment}', None)]
    postings = draw(st.lists(st.sampled_from(ACCOUNTS), min_size=1, max_size=3))
    for i, account in enumerate(postings):
        if draw(st.booleans()):
            lines.append((f"{indent}; about {draw(st.sampled_from(ACCOUNTS))}", None))
        elided = i == len(postings) - 1 and draw(st.booleans())
        inline = f" ; {draw(st.sampled_from(ACCOUNTS))}" if draw(st.booleans()) else ""
        lines.append((f"{indent}{account}" + ("" if elided else f"  {i + 1}.00 USD") + inline, account))
        if draw(st.booleans()):
            lines.append((f'{indent}{indent}memo: "{draw(st.sampled_from(PAYEES))}"', None))
    if draw(st.booleans()):
        lines.append((f"{indent}; after the last posting", None))  # still the entry's span (Fava; verifier F2)
    return "txn", lines


@st.composite
def ledger_file(draw: st.DrawFn, path: str, with_option: bool) -> File:
    f = File(path, draw(st.sampled_from(["\n", "\r\n"])), draw(st.booleans()))
    parts: list[str] = []
    lineno = 1
    offset = 0

    def put(line: str) -> int:
        nonlocal lineno, offset
        at = offset
        parts.append(line + f.newline)
        offset += len(line) + len(f.newline)
        lineno += 1
        return at

    put(f"; {path}")
    pushed: list[str] = []
    option_at = 0 if with_option else -1  # before any push: an option in a region is its own test
    for i in range(draw(st.integers(1, 7))):
        if i == option_at and not pushed:
            f.option_offset = put(f'{OPTION_DATE} custom "fava-option" "insert-entry" "{OPTION_PATTERN}"')
        sep = draw(st.sampled_from(["", "blank", "space", "comment", "heading", "push", "pop"]))
        if sep == "blank":
            put("")
        elif sep == "space":
            put(" \t")
        elif sep == "comment":
            put(f"; about {draw(st.sampled_from(ACCOUNTS))}")
        elif sep == "heading":
            put("* Heading")
        elif sep == "push" and not pushed:
            which = draw(st.sampled_from(["tag", "meta"]))
            put("pushtag #trip" if which == "tag" else 'pushmeta trip: "x"')
            pushed.append(which)
        elif sep == "pop" and pushed:
            put("poptag #trip" if pushed.pop() == "tag" else "popmeta trip:")
        date = datetime.date(2024, 1, 1) + datetime.timedelta(days=draw(st.integers(0, 300)))
        keyword, lines = draw(entry_lines(date))
        e = Entry(path, lineno, date, keyword, offset, 0, [])
        for line, account in lines:
            at = put(line)
            if account is not None:
                col = line.index(account, len(line) - len(line.lstrip()) if line[0] in " \t" else 11)
                e.tokens.append((lineno - 1, at + col, at + col + len(account), account))
        e.end = offset
        f.entries.append(e)
    while pushed:
        put("poptag #trip" if pushed.pop() == "tag" else "popmeta trip:")
    f.text = "".join(parts)
    if not f.final_newline:
        f.text = f.text.removesuffix(f.newline)
        last = f.entries[-1]
        last.end = min(last.end, len(f.text))
    return f


@st.composite
def new_entry(draw: st.DrawFn) -> str:
    date = datetime.date(2024, 1, 1) + datetime.timedelta(days=draw(st.integers(0, 300)))
    if draw(st.booleans()):
        return f"{date} open {draw(st.sampled_from(ACCOUNTS))} USD\n"
    first = draw(st.sampled_from((*ACCOUNTS, OPTION_PATTERN, OPTION_PATTERN)))
    return f'{date} * "{draw(st.sampled_from(PAYEES))}" "new"\n  {first}  2.00 USD\n  Assets:Cash\n'


type Token = tuple[int, int, int, str]


@dataclass
class Op:
    op: str  # replace, delete or token
    file: int
    entry: int
    text: str = ""  # a replace's new text
    token: Token = (0, 0, 0, "")  # a token edit's (lineno, start, end, account)


@dataclass
class Case:
    files: list[File]
    ops: list[Op]
    inserts: list[str]


@st.composite
def cases(draw: st.DrawFn) -> Case:
    two = draw(st.booleans())
    files = [draw(ledger_file("main.bean", with_option=False))]
    if two:
        files.append(draw(ledger_file("other.bean", with_option=True)))
    ops: list[Op] = []
    for fi, f in enumerate(files):
        for ei, e in enumerate(f.entries):
            op = draw(st.sampled_from(["keep", "keep", "replace", "delete", "token"]))
            if op == "replace":
                ops.append(Op("replace", fi, ei, text=draw(new_entry())))
            elif op == "delete":
                ops.append(Op("delete", fi, ei))
            elif op == "token" and e.tokens:
                ops.append(Op("token", fi, ei, token=draw(st.sampled_from(e.tokens))))
    inserts = draw(st.lists(new_entry(), max_size=4))
    return Case(files, ops, inserts)


def lf_to(text: str, newline: str) -> str:
    return text.replace("\n", newline)


def rank(text: str) -> tuple[str, int, str]:
    return (text[:10], -2 if " open " in text.split("\n", 1)[0] else 0, text)


def model(case: Case) -> dict[str, bytes]:
    """The expected bytes, from the generator's own knowledge of every block."""
    out: dict[str, bytes] = {}
    option_file = next((f for f in case.files if f.option_offset is not None), None)
    pieces: dict[str, list[tuple[int, int, str]]] = {f.path: [] for f in case.files}
    for o in case.ops:
        f = case.files[o.file]
        e = f.entries[o.entry]
        if o.op == "delete":
            pieces[f.path].append((e.start, e.end, ""))
        elif o.op == "replace":
            new = lf_to(o.text, f.newline)
            if e.end == len(f.text) and not f.final_newline:
                new = new.removesuffix(f.newline)
            pieces[f.path].append((e.start, e.end, new))
        else:
            pieces[f.path].append((o.token[1], o.token[2], "Assets:Moved"))
    routed: list[str] = []
    appended: list[str] = []
    for text in case.inserts:
        date = datetime.date.fromisoformat(text[:10])
        accounts = [line.split()[0] for line in text.split("\n")[1:] if line.strip()][::-1] or [text.split()[2]]
        hit = option_file is not None and date > OPTION_DATE and any(a.startswith(OPTION_PATTERN) for a in accounts)
        (routed if hit else appended).append(text)
    if routed:
        assert option_file is not None
        assert option_file.option_offset is not None
        nl = option_file.newline
        data = "".join(lf_to(t, nl) + nl for t in sorted(routed, key=rank))
        pieces[option_file.path].append((option_file.option_offset, option_file.option_offset, data))
    if appended:
        f = case.files[0]
        nl = f.newline
        data = "".join(nl + lf_to(t, nl) for t in sorted(appended, key=rank))
        if not f.final_newline:
            data = nl + data.removesuffix(nl)
        pieces[f.path].append((len(f.text), len(f.text), data))
    for f in case.files:
        if not pieces[f.path]:
            continue
        text, pos, parts = f.text, 0, list[str]()
        for start, end, new in sorted(pieces[f.path]):
            parts.append(text[pos:start] + new)
            pos = end
        out[f.path] = "".join([*parts, text[pos:]]).encode()
    return out


def plan_of(case: Case, s: se.Snapshot) -> list[se.Edit]:
    edits: list[se.Edit] = []
    for o in case.ops:
        e = case.files[o.file].entries[o.entry]
        span = se.locate(s, se.Target(e.path, e.lineno, e.date, e.keyword))
        assert isinstance(span, se.Span), span
        assert (span.start, span.end) == (e.start, e.end)  # Fava's span is the generator's block
        if o.op == "delete":
            edits.append(se.Delete(span))
        elif o.op == "replace":
            edits.append(se.Replace(span, o.text))
        else:
            lineno, _, _, account = o.token
            edit = se.replace_token(s, span, lineno, grammar.AccountName(account), grammar.AccountName("Assets:Moved"))
            assert isinstance(edit, se.ReplaceToken), edit
            edits.append(edit)
    for text in case.inserts:
        ins = se.route_insert(s, text, "main.bean")
        assert isinstance(ins, se.Insert), ins
        edits.append(ins)
    return edits


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(case=cases(), order=st.randoms(use_true_random=False))
def test_a_plan_changes_only_its_spans_in_any_order_and_reverts(case: Case, order: random.Random) -> None:
    for f in case.files:
        _, errors, _ = parser.parse_string(f.text, f.path)
        assert errors == [], (f.text, errors)  # the generator writes valid ledgers
    current = {f.path: f.text.encode() for f in case.files}
    s = se.Snapshot(current)
    edits = plan_of(case, s)
    paths = {f.path for f in case.files}
    result = se.apply_edits(current, se.EditPlan.of(s, paths, edits))
    assert isinstance(result, se.Applied), result
    assert dict(result.files) == model(case)
    shuffled = list(edits)
    order.shuffle(shuffled)
    again = se.apply_edits(current, se.EditPlan.of(s, paths, shuffled))
    assert isinstance(again, se.Applied)
    assert dict(again.files) == dict(result.files)
    back = se.revert(dict(result.files), result)
    assert back == {p: current[p] for p in result.files}
