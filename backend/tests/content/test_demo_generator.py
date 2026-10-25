"""The demo generator skeleton (S0027; ADR-0022; review:22 §1; EP-10; INV-07; L10-18).

Spec lines checked here (the Beancount-side gates are in `scripts/oracle/tests/test_demo_generator.py`):
- ADR-0022 "Reproducible": the same anchor and size give the same bytes, whatever the clock or time zone of the
  machine; the anchor is an input (`SOURCE_DATE_EPOCH` or a date), never the build clock; another anchor moves
  the dates.
- ADR-0022 "Fragments are anchored at a date and seeded by name": adding a fragment does not reshuffle another
  one, and the order fragments are listed in does not matter.
- review:27 §1(d) "a size parameter (demo, 5k, 54k)": the household writes exactly the size's transactions,
  all inside the span that ends on the anchor (no entry after the anchor date).
"""

from __future__ import annotations

import decimal
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import override

import pytest

from app.content.demo import (
    GENERATOR_VERSION,
    Directive,
    Fragment,
    FragmentContext,
    LedgerView,
    Posting,
    SeededRng,
    Size,
    Span,
    anchor_from_epoch,
    anchor_from_iso,
    default_fragments,
    generate,
    span_for,
    transaction,
)

pytestmark = pytest.mark.cap("CAP-DATA-DEMO-GEN")

ANCHOR = date(2026, 10, 25)
TXN_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2}) [*!] ", re.MULTILINE)
DATED = re.compile(r"^(\d{4}-\d{2}-\d{2}) ", re.MULTILINE)


def text_of(anchor: date = ANCHOR, size: Size = Size.DEMO, fragments: Sequence[Fragment] | None = None) -> str:
    ledger = generate(anchor, size, default_fragments() if fragments is None else fragments)
    assert list(ledger.files) == ["main.beancount"]
    return ledger.files["main.beancount"]


def blocks(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if DATED.match(b)]


# --- Reproducible (ADR-0022, L10-18) ---


def test_same_inputs_give_the_same_bytes() -> None:
    assert text_of() == text_of()


def test_another_anchor_gives_other_dates() -> None:
    a, b = text_of(ANCHOR), text_of(date(2025, 3, 9))
    assert a != b
    assert "2025-03-01" <= max(DATED.findall(b)) <= "2025-03-09"
    assert min(DATED.findall(b)) == "2022-04-01"
    assert "2026-10-01" <= max(DATED.findall(a)) <= "2026-10-25"
    assert min(DATED.findall(a)) == "2023-11-01"


CLOCK_SHIM = """
import datetime as _dt, hashlib, sys, time as _t
FAKE = {fake}
_t.time = lambda: float(FAKE)
_t.time_ns = lambda: FAKE * 1_000_000_000
class _D(_dt.date):
    @classmethod
    def today(cls):
        return cls.fromtimestamp(FAKE)
class _DT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(FAKE, tz)
    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(FAKE)
    @classmethod
    def today(cls):
        return cls.fromtimestamp(FAKE)
_dt.date = _D
_dt.datetime = _DT
from app.content.demo import Size, anchor_from_iso, default_fragments, generate
text = generate(anchor_from_iso("2026-10-25"), Size.DEMO, default_fragments()).files["main.beancount"]
sys.stdout.write(hashlib.sha256(text.encode()).hexdigest())
"""


def digest_under_clock(fake_epoch: int, tz: str) -> str:
    env = {**os.environ, "TZ": tz}
    r = subprocess.run(  # noqa: S603 (fixed argv: this interpreter and a fixed program)
        [sys.executable, "-c", CLOCK_SHIM.format(fake=fake_epoch)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_machine_clock_and_time_zone_do_not_change_the_bytes() -> None:
    # Two builds of one tag, months apart and on opposite sides of the date line, give identical bytes (L10-18).
    expected = hashlib.sha256(text_of().encode()).hexdigest()
    assert digest_under_clock(946_684_800, "Pacific/Kiritimati") == expected  # 2000-01-01, UTC+14
    assert digest_under_clock(2_240_524_800, "America/Adak") == expected  # 2041-01-01, UTC-10


def test_the_clock_shim_really_moves_the_clock() -> None:
    # Must not pass vacuously: under the shim, a date taken from the clock is the fake one.
    probe = CLOCK_SHIM.split("from app.content")[0].format(fake=946_684_800) + (
        "import datetime\nsys.stdout.write(str(datetime.date.today()) + ' ' + str(int(_t.time())))\n"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)  # noqa: S603
    assert r.stdout in ("2000-01-01 946684800", "1999-12-31 946684800")


def test_a_caller_decimal_context_does_not_change_the_bytes() -> None:
    expected = text_of()
    with decimal.localcontext(prec=6, rounding=decimal.ROUND_DOWN):
        assert text_of() == expected


# S0027 verifier F2: GENERATOR_VERSION is "raised when the same inputs would give other bytes". This pins the
# bytes of each size at one anchor beside the version (a change detector, not a spec value: EP-29 exempts it as a
# characterization pin). Output changes on purpose -> bump GENERATOR_VERSION and update both lines together.
@dataclass(frozen=True)
class Pin:
    size: Size
    sha256: str


PINNED_VERSION = 1
PINNED = (
    Pin(Size.DEMO, sha256="a0dbf96d3b6fb0ee4d236b628b0a8def25fe161ce3cd813883ca222334b7d52c"),
    Pin(Size.K5, sha256="9c09fd86ed13d79e13aab0fa6efb028dd0e3b18617400e496a79bfae1fb706f7"),
    Pin(Size.K54, sha256="b2cc85a2696df1c216c0480aff7b09bf8d03b54fc30bacbdc473d44a027d8c51"),
)


def test_output_changes_only_with_a_new_generator_version() -> None:
    digests = tuple(Pin(size, hashlib.sha256(text_of(ANCHOR, size).encode()).hexdigest()) for size in Size)
    assert (GENERATOR_VERSION, digests) == (PINNED_VERSION, PINNED)


# --- The anchor is an explicit input (ADR-0022 "the anchor is the release tag's date (SOURCE_DATE_EPOCH)") ---


def test_source_date_epoch_is_read_as_a_utc_civil_date() -> None:
    # The reproducible-builds spec defines SOURCE_DATE_EPOCH as seconds since 1970-01-01 00:00 UTC.
    assert anchor_from_epoch("0") == date(1970, 1, 1)
    # (date(2026, 10, 25) - date(1970, 1, 1)).days * 86400 = 1792886400, the first second of that UTC day.
    assert anchor_from_epoch("1792886399") == date(2026, 10, 24)
    assert anchor_from_epoch("1792886400") == date(2026, 10, 25)
    assert anchor_from_epoch("1792972799") == date(2026, 10, 25)  # 2026-10-25 23:59:59 UTC
    assert anchor_from_epoch("1792972800") == date(2026, 10, 26)


@pytest.mark.parametrize("value", ["", " 1792972800", "1792972800 ", "-1", "+5", "1.5", "1e9", "0x10", "abc", "9" * 30])
def test_a_malformed_source_date_epoch_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        anchor_from_epoch(value)


def test_an_iso_anchor_is_read_strictly() -> None:
    assert anchor_from_iso("2026-10-25") == date(2026, 10, 25)
    for bad in ("2026-10-5", "20261025", "2026-02-30", "2026-10-25T00:00", " 2026-10-25", ""):
        with pytest.raises(ValueError, match="anchor"):
            anchor_from_iso(bad)


# --- Sizes and the span (review:27 §1(d); review:backend-survival §6.2 "5k and 54k transactions") ---


@pytest.mark.parametrize(("size", "count", "months"), [(Size.DEMO, 5_563, 36), (Size.K5, 5_000, 60)])
def test_each_size_writes_its_transaction_count_inside_the_span(size: Size, count: int, months: int) -> None:
    text = text_of(ANCHOR, size)
    dates = TXN_HEADER.findall(text)
    assert len(dates) == count
    span = span_for(ANCHOR, size)
    assert (span.end.year - span.start.year) * 12 + span.end.month - span.start.month + 1 == months
    assert all(span.start.isoformat() <= d <= ANCHOR.isoformat() for d in DATED.findall(text))
    assert min(DATED.findall(text)) == span.start.isoformat()


def test_the_span_ends_on_the_anchor_and_starts_on_a_month_start() -> None:
    assert span_for(date(2026, 10, 25), Size.DEMO) == Span(date(2023, 11, 1), date(2026, 10, 25))
    assert span_for(date(2026, 1, 31), Size.K5) == Span(date(2021, 2, 1), date(2026, 1, 31))
    assert span_for(date(2026, 10, 25), Size.K54) == Span(date(2006, 11, 1), date(2026, 10, 25))


def test_size_names_are_the_review_ones() -> None:
    assert [s.value for s in Size] == ["demo", "5k", "54k"]
    assert (Size.DEMO.transactions, Size.K5.transactions, Size.K54.transactions) == (5_563, 5_000, 54_000)


# --- Fragments: seeded by name, composition order-free, interface checks (ADR-0022) ---


@dataclass(frozen=True)
class Probe:
    """A test-only fragment that draws heavily from its own random stream."""

    name: str = "probe"
    assertion: str = "the probe writes one transaction"

    def build(self, ctx: FragmentContext) -> Sequence[Directive]:
        for _ in range(10_000):
            ctx.rng.next_u64()
        amount = Decimal(ctx.rng.between(100, 999)).scaleb(-2)
        return [
            transaction(
                ctx.span.start,
                "Probe",
                "probe",
                [Posting("Expenses:Probe", amount, "USD"), Posting("Assets:Bank:Checking", -amount, "USD")],
            )
        ]

    def check(self, view: LedgerView, span: Span, size: Size) -> list[str]:
        return []


def test_adding_a_fragment_does_not_reshuffle_another() -> None:
    alone = blocks(text_of(fragments=default_fragments()))
    together = blocks(text_of(fragments=(*default_fragments(), Probe())))
    assert [b for b in together if '"Probe"' not in b] == alone
    assert len(together) == len(alone) + 1


def test_the_order_fragments_are_listed_in_does_not_matter() -> None:
    assert text_of(fragments=(Probe(), *default_fragments())) == text_of(fragments=(*default_fragments(), Probe()))


def test_each_name_has_its_own_stream() -> None:
    a, b, a2 = SeededRng.for_name("household"), SeededRng.for_name("probe"), SeededRng.for_name("household")
    first = [a.next_u64() for _ in range(4)]
    assert first == [a2.next_u64() for _ in range(4)]
    assert first != [b.next_u64() for _ in range(4)]


@dataclass(frozen=True)
class Echo(Probe):
    """A test-only fragment that writes the first value of its stream as its narration."""

    @override
    def build(self, ctx: FragmentContext) -> Sequence[Directive]:
        one = Decimal("1.00")
        legs = [Posting("Expenses:Probe", one, "USD"), Posting("Assets:Bank:Checking", -one, "USD")]
        return [transaction(ctx.span.start, self.name, str(ctx.rng.next_u64()), legs)]


def test_each_fragment_draws_from_the_stream_of_its_own_name() -> None:
    text = text_of(fragments=(Echo(name="echo-a"), Echo(name="echo-b")))
    for name in ("echo-a", "echo-b"):
        assert f'"{name}" "{SeededRng.for_name(name).next_u64()}"' in text


def test_two_fragments_with_one_name_are_refused() -> None:
    # They would share one random stream, and their assertions could not be told apart.
    with pytest.raises(ValueError, match="probe"):
        generate(ANCHOR, Size.DEMO, (Probe(), Probe()))


@pytest.mark.parametrize("name", ["", "Household", "house hold", "household_", "-household", "a/b"])
def test_a_fragment_name_must_be_kebab_case(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        generate(ANCHOR, Size.DEMO, (Probe(name=name),))


def test_a_fragment_writing_after_the_anchor_is_refused() -> None:
    @dataclass(frozen=True)
    class Late(Probe):
        name: str = "late"

        @override
        def build(self, ctx: FragmentContext) -> Sequence[Directive]:
            d = date(ctx.span.end.year + 1, 1, 1)
            return [
                transaction(
                    d,
                    "Late",
                    "late",
                    [
                        Posting("Expenses:X", Decimal("1.00"), "USD"),
                        Posting("Assets:Bank:Checking", Decimal("-1.00"), "USD"),
                    ],
                )
            ]

    with pytest.raises(ValueError, match="late"):
        generate(ANCHOR, Size.DEMO, (Late(),))


def test_every_default_fragment_states_its_assertion() -> None:
    frags = default_fragments()
    assert [f.name for f in frags] == ["household"]
    assert all(f.assertion.strip() for f in frags)


def test_the_generated_for_record_names_the_inputs() -> None:
    ledger = generate(ANCHOR, Size.K5, default_fragments())
    assert ledger.generated_for_json() == (
        '{\n  "anchor": "2026-10-25",\n  "fragments": [\n    "household"\n  ],\n  "generator": 1,\n  "size": "5k"\n}\n'
    )


# --- The text builder refuses what Beancount would refuse (the generator's own guard) ---


def test_an_unbalanced_transaction_is_refused() -> None:
    with pytest.raises(ValueError, match="balance"):
        transaction(
            ANCHOR,
            "P",
            "n",
            [Posting("Expenses:A", Decimal("1.00"), "USD"), Posting("Assets:B", Decimal("-0.99"), "USD")],
        )


def test_a_long_account_and_a_long_amount_stay_separated() -> None:
    # S0027 verifier F1: a 44-character account beside a 12-character amount merged into one token.
    account = "Assets:Investments:Brokerage:Retirement:Roth"
    text = transaction(
        ANCHOR,
        "P",
        "n",
        [Posting(account, Decimal("-12345678.90"), "USD"), Posting("Assets:B", Decimal("12345678.90"), "USD")],
    ).text
    assert f"  {account}  -12345678.90 USD" in text.splitlines()


@pytest.mark.parametrize(
    "posting",
    [
        Posting("Expenses:A", Decimal("1.001"), "USD"),
        Posting("Expenses:A", Decimal(1), "USD"),
        Posting("expenses:A", Decimal("1.00"), "USD"),
        Posting("Expenses", Decimal("1.00"), "USD"),
        Posting("Expenses:a", Decimal("1.00"), "USD"),
        Posting("Expenses:A", Decimal("1.00"), "usd"),
    ],
)
def test_a_malformed_posting_is_refused(posting: Posting) -> None:
    other = Posting("Assets:B", -posting.amount, posting.currency)
    with pytest.raises(ValueError, match="posting"):
        transaction(ANCHOR, "P", "n", [posting, other])


@pytest.mark.parametrize(("payee", "narration"), [('A "quoted" shop', "n"), ("P", "two\nlines"), ("P", "back\\slash")])
def test_text_that_would_break_the_string_is_refused(payee: str, narration: str) -> None:
    with pytest.raises(ValueError, match="text"):
        transaction(
            ANCHOR,
            payee,
            narration,
            [Posting("Expenses:A", Decimal("1.00"), "USD"), Posting("Assets:B", Decimal("-1.00"), "USD")],
        )


# --- The seeded generator (EP-10): a known-answer test and unbiased bounds ---


def test_splitmix64_matches_the_published_reference() -> None:
    # Reference values of SplitMix64 (Steele, Lea and Flood 2014; Vigna's splitmix64.c) for seed 1234567.
    rng = SeededRng(1234567)
    assert [rng.next_u64() for _ in range(3)] == [6457827717110365317, 3203168211198807973, 9817491932198370423]


def test_bounded_draws_stay_in_their_bounds() -> None:
    rng = SeededRng.for_name("bounds")
    seen = {rng.between(3, 5) for _ in range(500)}
    assert seen == {3, 4, 5}
    assert all(0 <= rng.below(7) < 7 for _ in range(500))
    with pytest.raises(ValueError, match="bound"):
        rng.below(0)
