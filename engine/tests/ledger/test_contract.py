"""The write-safety contract's prepare (S0116; ADR-0008 steps 1, 2, 2b, 3 as amended by ADR-0067 and ADR-0068;
ADR-0010; INV-01, INV-22): an edit plan and its intended change checked into a write plan, without writing.

Each step's refusal has a must-not-pass test, and each step fails closed: a fault injected into it (an exception, an
undecidable result) gives `Refused` naming the step, with nothing on disk changed. A plan whose result is not the
intended change is made here by doctoring what `apply_edits` returns (as a bug in the primitives would), since the
primitives themselves refuse such text before the contract sees it. The D1 write corpus runs the test-only producer's
edits through prepare at the end, against an oracle independent of prepare: Beancount's own loader on a copy of the
case with the candidate texts written.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import shutil
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from beancount import loader
from beancount.core import data
from beancount.parser import parser

from ledger_engine.ledger import contract, overlay
from ledger_engine.ledger import fingerprint as fp
from ledger_engine.ledger.core import entry_keys, ledger_set
from ledger_engine.ledger.core import semantic_diff as sd
from ledger_engine.ledger.core import source_edit as se
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror.core import rows
from tests.ledger import producer as pr

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22"), pytest.mark.inv("INV-03")]

D = datetime.date
CORPUS = Path(__file__).resolve().parents[3] / "corpora" / "ledgers" / "write"

MAIN = "".join(
    [
        'option "operating_currency" "USD"\n',  # 1
        "2024-01-01 open Assets:Cash USD\n",  # 2
        "2024-01-01 open Expenses:Food USD\n",  # 3
        "2024-01-01 open Assets:Bank USD\n",  # 4
        "\n",  # 5
        '2024-01-05 * "Shop" "lunch"\n',  # 6
        "  Expenses:Food  10.50 USD\n",  # 7
        "  Assets:Cash  -10.50 USD\n",  # 8
        "\n",  # 9
        '2024-01-06 * "Shop" "dinner"\n',  # 10
        "  Expenses:Food  4.00 USD\n",  # 11
        "  Assets:Cash\n",  # 12
        "\n",  # 13
        "; a comment\n",  # 14
        "2024-01-10 balance Assets:Cash  -14.50 USD\n",  # 15
    ]
)
LUNCH = se.Target("", 6, D(2024, 1, 5), "txn")
DINNER = se.Target("", 10, D(2024, 1, 6), "txn")


def at(target: se.Target, root: str) -> se.Target:
    return dataclasses.replace(target, path=root)


def ledger(tmp_path: Path, files: Mapping[str, str] | None = None) -> tuple[str, dict[str, bytes]]:
    """The ledger's files written under `tmp_path`, the root `main.beancount`; the snapshot of every file, by its
    absolute path, root first."""
    folder = tmp_path / "L"
    folder.mkdir()
    texts = {"main.beancount": MAIN, **(files or {})}
    for name, text in texts.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(text.encode())
    root = str(folder / "main.beancount")
    return root, {str(folder / n): (folder / n).read_bytes() for n in texts}


def baseline(root: str) -> Counter[str]:
    """The error keys of the disk as it is, as the published mirror holds them (`rows.error_rows` of the load)."""
    loaded = overlay.load(root, {}, DESKTOP)
    assert isinstance(loaded, overlay.Loaded), loaded
    return entry_keys.error_keys(rows.error_rows(loaded.reported))


def tree(folder: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def produced(current: Mapping[str, bytes], requests: list[pr.Request]) -> sd.Produced:
    out = pr.produce(se.Snapshot(current), requests)
    assert isinstance(out, sd.Produced), out
    return out


def prep(
    root: str,
    current: Mapping[str, bytes],
    requests: list[pr.Request] | sd.Produced,
    base: Counter[str] | None = None,
    **kw: Any,
) -> contract.Prepared | contract.Refused:
    p = requests if isinstance(requests, sd.Produced) else produced(current, requests)
    return contract.prepare(current, p, root, baseline(root) if base is None else base, DESKTOP, **kw)


def ok(result: contract.Prepared | contract.Refused) -> contract.Prepared:
    assert isinstance(result, contract.Prepared), result
    return result


def refused(result: contract.Prepared | contract.Refused, step: contract.Step, reason: str) -> contract.Refused:
    assert isinstance(result, contract.Refused), result
    assert (result.step, result.reason) == (step, reason), result
    return result


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- a plan that passes ---------------------------------------------------------------------------------------------


def test_a_replace_is_prepared_with_its_texts_hashes_and_the_fingerprint_the_save_checks(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    before = tree(tmp_path)
    new = '2024-01-05 * "Shop" "lunch"\n  memo: "s0116"\n  Expenses:Food  10.50 USD\n  Assets:Cash  -10.50 USD\n'
    out = ok(prep(root, current, [pr.ReplaceReq(at(LUNCH, root), new)]))
    expected = MAIN.replace(
        '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  10.50 USD\n  Assets:Cash  -10.50 USD\n', new
    ).encode()
    assert out.files == (contract.PlannedFile(root, sha(current[root]), sha(expected), expected.decode()),)
    assert out.new_errors == ()
    assert out.errors == 0
    # The plan's fingerprint is `as_before` of the load's: the disk as the load read it, each candidate at its pre
    # bytes (engine-jobs §6.1 step 4), so the disk as it is now observes equal.
    assert dict(out.fingerprint.files)[root] == ledger_set.Content(sha(current[root]))
    assert fp.observe(out.fingerprint.ledger_set) == out.fingerprint
    assert tree(tmp_path) == before  # nothing written


def test_an_insert_a_delete_and_a_token_edit_in_one_plan_are_prepared(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    requests: list[pr.Request] = [
        pr.InsertReq('2024-02-01 * "Bank" "fee"\n  Expenses:Food  1.00 USD\n  Assets:Bank\n', root),
        pr.DeleteReq(at(DINNER, root)),
        pr.TokenReq(at(LUNCH, root), 8, "Assets:Cash", "Assets:Bank"),
    ]
    out = ok(prep(root, current, requests))
    text = out.files[0].text
    assert '"dinner"' not in text
    assert "  Assets:Bank  -10.50 USD\n" in text
    assert text.endswith('\n2024-02-01 * "Bank" "fee"\n  Expenses:Food  1.00 USD\n  Assets:Bank\n')
    # The balance of Assets:Cash is now 0, not -14.50: an accounting error the preview lists, never a refusal.
    assert [e.type for e in out.new_errors] == ["BalanceError"]


# ---- every step fails closed (INV-22's per-step fault test) ---------------------------------------------------------


def _boom(*args: object, **kwargs: object) -> Any:
    msg = "injected"
    raise RuntimeError(msg)


def _patch(target: object, name: str) -> Callable[[pytest.MonkeyPatch], dict[str, Any]]:
    def inject(mp: pytest.MonkeyPatch) -> dict[str, Any]:
        mp.setattr(target, name, _boom)
        return {}

    return inject


def _load_fault(mp: pytest.MonkeyPatch) -> dict[str, Any]:
    return {"load": _boom}


FAULTS: dict[contract.Step, Callable[[pytest.MonkeyPatch], dict[str, Any]]] = {
    "apply": _patch(se, "apply_edits"),
    "parse": _patch(sd, "parse"),
    "diff": _patch(sd, "diff"),
    "spans": _patch(sd, "outside_spans"),
    "load": _load_fault,
    "errors": _patch(ledger_set, "as_before"),
}


def test_every_step_has_a_fault_test() -> None:
    assert set(FAULTS) == set(contract.STEPS) - {"plan"}  # the producer's step is the job's (`preview`)


@pytest.mark.parametrize("step", sorted(FAULTS))
def test_a_fault_in_any_step_refuses_naming_the_step_and_writes_nothing(
    step: contract.Step, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, current = ledger(tmp_path)
    base = baseline(root)
    p = produced(
        current,
        [pr.ReplaceReq(at(LUNCH, root), '2024-01-05 * "Shop" "lunch"\n  Expenses:Food  10.50 USD\n  Assets:Cash\n')],
    )
    before = tree(tmp_path)
    kw = FAULTS[step](monkeypatch)
    out = refused(contract.prepare(current, p, root, base, DESKTOP, **kw), step, "fault")
    assert out.detail == "RuntimeError"
    assert tree(tmp_path) == before


def test_a_load_that_gives_no_decidable_result_refuses(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    before = tree(tmp_path)

    def undecided(root: str, overlay_: Mapping[str, bytes], policy: object) -> Any:
        return None

    refused(prep(root, current, [pr.DeleteReq(at(DINNER, root))], load=undecided), "load", "undecidable")
    assert tree(tmp_path) == before


# ---- step 2: the semantic diff is the intended change --------------------------------------------------------------


def doctor(monkeypatch: pytest.MonkeyPatch, change: Callable[[str], str]) -> None:
    """`apply_edits` as a bug in it would answer: the planned result with `change` made to each new text, the splices
    as recorded (so the hashes agree with the bytes; only the text differs from what the edits make)."""
    real = se.apply_edits

    def doctored(snap: se.Snapshot, plan: se.EditPlan) -> se.Applied | se.Refusal:
        a = real(snap, plan)
        assert isinstance(a, se.Applied), a
        files = {p: change(b.decode()).encode() for p, b in a.files.items()}
        return se.Applied(files, a.before, {p: sha(b) for p, b in files.items()}, a.splices)

    monkeypatch.setattr(se, "apply_edits", doctored)


def DELETE_DINNER(root: str) -> list[pr.Request]:
    return [pr.DeleteReq(at(DINNER, root))]


NOT_INTENDED: list[tuple[Callable[[str], str], str]] = [
    # An untargeted entry changed (the lunch's amount), an untargeted entry removed, one added, two swapped.
    (lambda t: t.replace("10.50 USD", "10.51 USD"), "not_intended_change"),
    (lambda t: t.replace("2024-01-01 open Assets:Bank USD\n", ""), "not_intended_change"),
    (lambda t: t + "2024-03-01 open Assets:Extra\n", "not_intended_change"),
    (
        lambda t: t.replace(
            "2024-01-01 open Assets:Cash USD\n2024-01-01 open Expenses:Food USD\n",
            "2024-01-01 open Expenses:Food USD\n2024-01-01 open Assets:Cash USD\n",
        ),
        "not_intended_change",
    ),
    # A change of an untargeted entry that keeps its value but not its decimals: 4.00 is not 4.0.
    (lambda t: t.replace("-14.50 USD", "-14.5 USD"), "not_intended_change"),
    # Options, plugins and includes are never changed.
    (lambda t: 'option "title" "Mine"\n' + t, "options_changed"),
    (lambda t: t + 'plugin "beancount.plugins.implicit_prices"\n', "options_changed"),
    (lambda t: t + 'include "other.beancount"\n', "options_changed"),
    # A parser error in the text Finzytrack wrote blocks (step 1; ADR-0067's one exception).
    (lambda t: t + '2024-03-01 * "unterminated\n', "parse_error"),
    (lambda t: t + "2024-03-01 open nope\n", "parse_error"),
]


@pytest.mark.parametrize(("change", "reason"), NOT_INTENDED)
def test_a_result_that_is_not_the_intended_change_is_refused(
    change: Callable[[str], str], reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, current = ledger(tmp_path)
    base = baseline(root)
    p = produced(current, DELETE_DINNER(root))
    doctor(monkeypatch, change)
    step: contract.Step = "parse" if reason == "parse_error" else "diff"
    refused(contract.prepare(current, p, root, base, DESKTOP), step, reason)


def test_a_targeted_set_that_differs_from_the_intent_is_refused(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    base = baseline(root)
    fee = '2024-02-01 * "Bank" "fee"\n  Expenses:Food  1.00 USD\n  Assets:Bank\n'
    p = produced(current, [pr.InsertReq(fee, root), pr.DeleteReq(at(DINNER, root))])
    intent = p.intent
    other = entry_keys.key_of_text('2024-02-01 * "Bank" "fee"\n  Expenses:Food  2.00 USD\n  Assets:Bank\n', root)
    assert other is not None
    cases: dict[str, sd.IntendedChange] = {
        # An extra entry in the result: the intent inserts nothing.
        "extra": dataclasses.replace(intent, inserts=()),
        # A missing entry: the intent inserts two, the plan one.
        "missing": dataclasses.replace(intent, inserts=intent.inserts * 2),
        # A different entry: the intent inserts the fee at 2.00, the plan at 1.00.
        "different": dataclasses.replace(intent, inserts=(sd.Insertion(root, other),)),
        # The intent removes nothing; the plan deletes the dinner.
        "untargeted delete": dataclasses.replace(intent, removes=()),
    }
    for name, changed in cases.items():
        out = contract.prepare(current, sd.Produced(p.plan, changed), root, base, DESKTOP)
        assert isinstance(out, contract.Refused), name
        assert out.step == "diff", (name, out)
        assert out.reason in ("not_intended_change", "edits_not_intent"), (name, out)


OTHER = "2024-01-01 open Assets:Other\n\n; the other file\n2024-01-02 open Assets:More\n"


def two_files(tmp_path: Path) -> tuple[str, str, dict[str, bytes]]:
    root, current = ledger(tmp_path, {"main.beancount": MAIN + 'include "other.beancount"\n', "other.beancount": OTHER})
    other = str(Path(root).parent / "other.beancount")
    return root, other, current


def test_an_intended_change_in_a_file_the_plan_does_not_change_is_refused(tmp_path: Path) -> None:
    """The intent inserts into the root (the plan does) and into the other file (the plan does not)."""
    root, other, current = two_files(tmp_path)
    p = produced(current, [pr.InsertReq("2024-03-01 open Assets:New\n", root)])
    also = entry_keys.key_of_text("2024-03-01 open Assets:Elsewhere\n", other)
    assert also is not None
    intent = dataclasses.replace(p.intent, inserts=(*p.intent.inserts, sd.Insertion(other, also)))
    out = contract.prepare(current, sd.Produced(p.plan, intent), root, baseline(root), DESKTOP)
    refused(out, "diff", "not_intended_change")


def test_an_intent_that_names_one_entry_twice_is_refused(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    p = produced(current, DELETE_DINNER(root))
    twice = dataclasses.replace(p.intent, removes=p.intent.removes * 2)
    refused(
        contract.prepare(current, sd.Produced(p.plan, twice), root, baseline(root), DESKTOP), "diff", "edits_not_intent"
    )


def test_a_second_matching_entry_is_not_the_target(tmp_path: Path) -> None:
    """Two identical entries; the intent removes the first, the plan deletes the second: refused, though the entries
    left are the same as the intended result's."""
    twice = '2024-01-20 * "Shop" "tea"\n  Expenses:Food  1.00 USD\n  Assets:Cash\n'
    root, current = ledger(tmp_path, {})
    text = MAIN + "\n" + twice + "\n" + twice
    Path(root).write_text(text)
    current = {root: text.encode()}
    first, second = se.Target(root, 17, D(2024, 1, 20), "txn"), se.Target(root, 21, D(2024, 1, 20), "txn")
    p_second = produced(current, [pr.DeleteReq(second)])
    p_first = produced(current, [pr.DeleteReq(first)])
    out = contract.prepare(current, sd.Produced(p_second.plan, p_first.intent), root, baseline(root), DESKTOP)
    refused(out, "diff", "edits_not_intent")
    ok(contract.prepare(current, p_first, root, baseline(root), DESKTOP))  # the same intent with its own plan passes


def test_a_target_that_is_not_the_intended_entry_is_refused(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    p = produced(current, DELETE_DINNER(root))
    lunch = parser.parse_string(current[root].decode(), root)[0][3]
    assert isinstance(lunch, data.Transaction)
    wrong = sd.IntendedChange((), (), (sd.Removal(at(DINNER, root), entry_keys.entry_key(lunch)),))
    refused(
        contract.prepare(current, sd.Produced(p.plan, wrong), root, baseline(root), DESKTOP),
        "diff",
        "target_not_intended",
    )


def test_a_plugin_generated_or_glob_included_entry_in_the_intent_is_refused(tmp_path: Path) -> None:
    """The intent names an entry the text does not hold at that place: a price `implicit_prices` makes at load, or an
    entry of a file a glob includes, named as if it were in the root."""
    case = tmp_path / "implicit-prices"
    shutil.copytree(CORPUS / "implicit-prices", case)
    root = str(case / "main.beancount")
    current = {root: Path(root).read_bytes()}
    loaded = overlay.load(root, {}, DESKTOP)
    assert isinstance(loaded, overlay.Loaded)
    generated = next(e for e in loaded.entries if isinstance(e, data.Price) and e.meta["filename"] == root)
    txn = next(e for e in parser.parse_string(current[root].decode(), root)[0] if isinstance(e, data.Transaction))
    target = se.Target(root, txn.meta["lineno"], txn.date, "txn")
    p = produced(current, [pr.DeleteReq(target)])
    as_price = sd.IntendedChange((), (), (sd.Removal(target, entry_keys.entry_key(generated)),))
    refused(
        contract.prepare(current, sd.Produced(p.plan, as_price), root, baseline(root), DESKTOP),
        "diff",
        "target_not_intended",
    )

    case = tmp_path / "includes"
    shutil.copytree(CORPUS / "includes", case)
    root = str(case / "main.beancount")
    included = str(case / "years" / "2024.beancount")
    current = {root: Path(root).read_bytes(), included: Path(included).read_bytes()}
    entry = parser.parse_string(current[included].decode(), included)[0][0]
    target = se.Target(included, entry.meta["lineno"], entry.date, "txn")
    p = produced(current, [pr.DeleteReq(target)])
    in_root = sd.IntendedChange(
        (), (), (sd.Removal(dataclasses.replace(target, path=root), entry_keys.entry_key(entry)),)
    )
    out = contract.prepare(current, sd.Produced(p.plan, in_root), root, baseline(root), DESKTOP)
    assert isinstance(out, contract.Refused)
    assert (out.step, out.reason) in (("diff", "edits_not_intent"), ("diff", "not_intended_change")), out


# ---- step 2b: bytes outside the planned spans ----------------------------------------------------------------------


OUTSIDE: list[Callable[[str], str]] = [
    lambda t: t.replace("; a comment\n", "; a comment, changed\n"),  # a comment
    lambda t: t.replace("\n\n; a comment", "\n  \n; a comment"),  # whitespace on a blank line
    lambda t: t.replace("; a comment\n", "; a comment\r\n"),  # a line ending
    lambda t: t.replace("open Assets:Bank USD\n", "open Assets:Bank   USD\n"),  # spaces inside an entry
]


@pytest.mark.parametrize("change", OUTSIDE)
def test_a_candidate_that_differs_outside_the_planned_spans_is_refused(
    change: Callable[[str], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, current = ledger(tmp_path)
    base = baseline(root)
    p = produced(current, DELETE_DINNER(root))
    doctor(monkeypatch, change)
    refused(contract.prepare(current, p, root, base, DESKTOP), "spans", "outside_spans_changed")


def test_a_candidate_for_a_file_the_plan_does_not_edit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_edits` answers a second file whose entries are the same and whose comment changed: step 2 sees no entry
    change there, step 2b refuses it."""
    root, other, current = two_files(tmp_path)
    base = baseline(root)
    p = produced(current, DELETE_DINNER(root))
    real = se.apply_edits

    def extra(snap: se.Snapshot, plan: se.EditPlan) -> se.Applied | se.Refusal:
        a = real(snap, plan)
        assert isinstance(a, se.Applied)
        changed = current[other].replace(b"; the other file", b"; changed").decode().encode()
        files = {**a.files, other: changed}
        return se.Applied(files, {**a.before, other: sha(current[other])}, {**a.after, other: sha(changed)}, a.splices)

    monkeypatch.setattr(se, "apply_edits", extra)
    refused(contract.prepare(current, p, root, base, DESKTOP), "spans", "outside_spans_changed")


def test_a_splice_outside_the_planned_spans_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The splices `apply_edits` reports are held to the plan's own spans, not trusted."""
    root, current = ledger(tmp_path)
    base = baseline(root)
    p = produced(current, DELETE_DINNER(root))
    real = se.apply_edits

    def shifted(snap: se.Snapshot, plan: se.EditPlan) -> se.Applied | se.Refusal:
        a = real(snap, plan)
        assert isinstance(a, se.Applied)
        s = a.splices[root][0]
        moved = se.Splice(s.start - 1, s.end, current[root].decode()[s.start - 1 : s.end], "\n", s.at - 1)
        return se.Applied(a.files, a.before, a.after, {root: (moved,)})

    monkeypatch.setattr(se, "apply_edits", shifted)
    refused(contract.prepare(current, p, root, base, DESKTOP), "spans", "outside_spans_changed")


# ---- step 3: the overlay load and the error keys --------------------------------------------------------------------


def test_a_failing_balance_assertion_is_listed_with_its_key_and_never_refuses(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    out = ok(prep(root, current, [pr.InsertReq("2024-01-20 balance Assets:Cash  0.00 USD\n", root)]))
    assert [(e.type, e.path, e.line) for e in out.new_errors] == [("BalanceError", root, 17)]
    assert "Balance failed for 'Assets:Cash'" in out.new_errors[0].message
    assert len(out.new_errors[0].key) == 64
    assert out.errors == 1


BROKER = "".join(
    [
        "2024-01-01 open Assets:Broker\n",
        "2024-01-01 open Assets:Pay USD\n",
        '2024-01-02 * "buy"\n',
        "  Assets:Broker  1 HOO {10 USD}\n",
        "  Assets:Pay\n",
    ]
)
SELL = '2024-01-03 * "sell"\n  Assets:Broker  -1 HOO {11 USD}\n  Assets:Pay\n'


def test_a_booking_error_is_listed_and_never_refuses(tmp_path: Path) -> None:
    root, current = ledger(tmp_path, {"main.beancount": BROKER})
    out = ok(prep(root, current, [pr.InsertReq(SELL, root)]))
    assert [e.type for e in out.new_errors] == ["ReductionError"]


def test_a_plugin_error_is_listed_and_never_refuses(tmp_path: Path) -> None:
    text = 'plugin "beancount.plugins.check_commodity"\n2024-01-01 commodity USD\n' + MAIN.split("\n", 1)[1]
    root, current = ledger(tmp_path, {"main.beancount": text})
    out = ok(
        prep(root, current, [pr.InsertReq('2024-02-01 * "x"\n  Assets:Cash  1 EUR\n  Assets:Bank  -1 EUR\n', root)])
    )
    assert "CheckCommodityError" in [e.type for e in out.new_errors]


def test_an_old_error_is_not_new_after_a_splice_shifts_its_line(tmp_path: Path) -> None:
    """A failing assertion and a booking error (whose message holds the posting's line) are in the baseline; a replace
    above them adds a line, so both move down one line, and neither is new."""
    text = BROKER + SELL + "2024-01-10 balance Assets:Pay  5 USD\n"
    root, current = ledger(tmp_path, {"main.beancount": text})
    base = baseline(root)
    assert sorted(k.split('"')[1] for k in base) == ["BalanceError", "ReductionError"]
    buy = se.Target(root, 3, D(2024, 1, 2), "txn")
    out = ok(
        prep(
            root,
            current,
            [pr.ReplaceReq(buy, '2024-01-02 * "buy"\n  memo: "x"\n  Assets:Broker  1 HOO {10 USD}\n  Assets:Pay\n')],
            base,
        )
    )
    assert out.new_errors == ()
    assert out.errors == 2


def test_a_second_copy_of_an_old_error_is_new(tmp_path: Path) -> None:
    text = MAIN + "2024-01-11 balance Assets:Cash  1.00 USD\n"
    root, current = ledger(tmp_path, {"main.beancount": text})
    out = ok(prep(root, current, [pr.InsertReq("2024-01-11 balance Assets:Cash  1.00 USD\n", root)]))
    assert [e.type for e in out.new_errors] == ["BalanceError"]


def test_a_load_that_cannot_run_refuses(tmp_path: Path) -> None:
    root, current = ledger(tmp_path, {"main.beancount": MAIN + 'include "secret.beancount.gpg"\n'})
    (Path(root).parent / "secret.beancount.gpg").write_bytes(b"not read: an encrypted file is never decrypted\n")
    out = prep(root, current, DELETE_DINNER(root), base=Counter())
    refused(out, "load", "encrypted")


def test_a_path_that_is_not_absolute_and_normal_is_refused(tmp_path: Path) -> None:
    root, current = ledger(tmp_path)
    odd = root.replace("/L/", "/L/./")
    p = produced({odd: current[root]}, [pr.DeleteReq(at(DINNER, odd))])
    refused(contract.prepare({odd: current[root]}, p, root, baseline(root), DESKTOP), "apply", "path_not_normal")


# ---- the D1 write corpus --------------------------------------------------------------------------------------------

CASES: dict[str, Any] = tomllib.loads((CORPUS / "cases.toml").read_text("utf-8"))
LOAD_CASES = sorted(k for k, v in CASES.items() if v["expect"] == "load")
SUFFIXES = (".beancount", ".bean")


def corpus_requests(case_dir: Path, current: Mapping[str, bytes]) -> list[tuple[str, list[pr.Request]]]:
    """Per case, three producer runs: a note inserted on the first account opened in the root (routed as Fava would,
    else at the root's end), the last entry of the last file deleted, and the first transaction's first posting
    renamed to its second posting's account (where the case has a transaction with two postings)."""
    root = str(case_dir / "main.beancount")
    entries = {p: parser.parse_string(b.decode(), p)[0] for p, b in current.items()}
    out: list[tuple[str, list[pr.Request]]] = []
    opened = next(e for es in entries.values() for e in es if isinstance(e, data.Open))
    out.append(("insert", [pr.InsertReq(f'2099-01-01 note {opened.account} "s0116"\n', root)]))
    last_file = next(p for p in reversed(list(entries)) if entries[p])
    last = entries[last_file][-1]
    assert last.meta is not None
    out.append(("delete", [pr.DeleteReq(se.Target(last_file, int(last.meta["lineno"]), last.date, pr_keyword(last)))]))
    for path, es in entries.items():
        txn = next((e for e in es if isinstance(e, data.Transaction) and len(e.postings) >= 2), None)
        if txn is None or txn.postings[0].account == txn.postings[1].account:
            continue
        first = txn.postings[0]
        assert first.meta is not None
        target = se.Target(path, int(txn.meta["lineno"]), txn.date, "txn")
        line = int(first.meta["lineno"])
        out.append(("token", [pr.TokenReq(target, line, first.account, txn.postings[1].account)]))
        break
    return out


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


def pr_keyword(entry: data.Directive) -> se.Keyword:
    return KEYWORDS[type(entry)]


def forget_plugins(folder: Path) -> None:
    """As a fresh engine would start (engine/CLAUDE.md: one load with plugins per process)."""
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if isinstance(file, str) and Path(file).resolve().is_relative_to(folder.resolve()):
            del sys.modules[name]


def oracle_new_errors(case_dir: Path, written: Path, files: Mapping[str, str]) -> Counter[str]:
    """Independent of prepare: the case copied again, the candidate texts written into the copy, Beancount's own
    loader on it, its errors keyed and compared with Beancount's own loader on the case as it is."""
    shutil.copytree(case_dir, written)
    for path, text in files.items():
        (written / Path(path).relative_to(case_dir)).write_bytes(text.encode())
    forget_plugins(case_dir)
    old = loader.load_file(str(case_dir / "main.beancount"))
    forget_plugins(case_dir)
    new = loader.load_file(str(written / "main.beancount"))
    forget_plugins(written)

    # The copy's paths are another folder's: keyed as if they were the case's own; the entries left out (a `document`
    # entry holds the copy's path).
    def plain(r: rows.Row) -> rows.Row:
        _, path, line, message, _, kind = r
        here = None if path is None else str(path).replace(str(written), str(case_dir))
        return (0, here, line, str(message).replace(str(written), str(case_dir)), None, kind)

    after = entry_keys.error_keys(plain(r) for r in rows.error_rows(new[1]))
    return after - entry_keys.error_keys(plain(r) for r in rows.error_rows(old[1]))


def strip_entries(errors: tuple[contract.NewError, ...]) -> Counter[str]:
    """The new errors keyed as `oracle_new_errors` keys them (the entry left out: the oracle's copy holds other
    paths in its `document` entries)."""
    return Counter(entry_keys.error_key((0, e.path, e.line, e.message, None, e.type)) for e in errors)


def corpus_case(case: str, tmp_path: Path) -> int:
    case_dir = tmp_path / "case"
    shutil.copytree(CORPUS / case, case_dir, ignore=shutil.ignore_patterns("__pycache__"))
    current = {str(p): p.read_bytes() for p in sorted(case_dir.rglob("*")) if p.is_file() and p.name.endswith(SUFFIXES)}
    root = str(case_dir / "main.beancount")
    count = 0
    for name, requests in corpus_requests(case_dir, current):
        forget_plugins(case_dir)
        base = baseline(root)
        forget_plugins(case_dir)
        out = prep(root, current, requests, base)
        assert isinstance(out, contract.Prepared), (case, name, out)
        expected = oracle_new_errors(case_dir, tmp_path / f"{name}-written", {f.path: f.text for f in out.files})
        assert strip_entries(out.new_errors) == expected, (case, name)
        count += 1
    return count


def test_the_d1_corpus_edits_are_prepared_with_the_new_errors_beancount_reports(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every `load` case of the D1 write corpus (no parametrize: a case cannot be switched off by an empty list); the
    counts are the population recorded in the S0116 journal's Hand-off."""
    failures: list[str] = []
    runs = 0
    for case in LOAD_CASES:
        try:
            runs += corpus_case(case, tmp_path_factory.mktemp(case))
        except AssertionError as e:
            failures.append(f"{case}: {e}")
    assert failures == []
    assert (len(LOAD_CASES), runs) == (17, 48)
