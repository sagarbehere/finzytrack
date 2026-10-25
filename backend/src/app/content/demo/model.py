"""The fragment interface of the demo generator (ADR-0022; review:22 §1).

A fragment is one scenario (the background household, or one capability's showcase). It is given the span that
ends on the anchor date, the size and its own random stream seeded by its name, and returns dated directives.
It also states one assertion: a fact about the generated ledger, checked on Beancount's booked output
(`scripts/oracle/tests/test_demo_generator.py`) through the Beancount-free `LedgerView` below (ADR-0054).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.content.demo.ledger_text import Directive
from app.content.demo.rng import SeededRng


class Size(StrEnum):
    """How big the generated ledger is: the number of transactions the background household writes.

    `demo` matches the legacy demo the owner's write target was set on (INV-20 "at most 1 s at demo size"):
    5,441 transactions and 5,581 entries (review:28 §1, review:D1 §9). 5,563 household transactions plus the 18
    commodity and open directives give 5,581 entries, at least as many as the legacy demo on both counts.
    `5k` and `54k` are the test and performance corpora (review:22 §1 item 5); spike S4's 54k corpus had 54,415
    transactions (review:spikes/s04-overlay-54k).
    """

    DEMO = "demo"
    K5 = "5k"
    K54 = "54k"

    @property
    def transactions(self) -> int:
        match self:
            case Size.DEMO:
                return 5_563
            case Size.K5:
                return 5_000
            case Size.K54:
                return 54_000

    @property
    def months(self) -> int:
        match self:
            case Size.DEMO:
                return 36
            case Size.K5:
                return 60
            case Size.K54:
                return 240


@dataclass(frozen=True, slots=True)
class Span:
    """The generated dates: from `start` (a month's first day) to `end` (the anchor), both inclusive."""

    start: date
    end: date


def span_for(anchor: date, size: Size) -> Span:
    """The size's months, ending on the anchor date (the anchor's own month counts as the last one)."""
    index = anchor.year * 12 + anchor.month - 1 - (size.months - 1)
    return Span(date(index // 12, index % 12 + 1, 1), anchor)


@dataclass(frozen=True, slots=True)
class FragmentContext:
    span: Span
    size: Size
    rng: SeededRng


@dataclass(frozen=True, slots=True)
class BookedPosting:
    account: str
    number: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class BookedTxn:
    date: date
    payee: str
    narration: str
    postings: tuple[BookedPosting, ...]


@dataclass(frozen=True, slots=True)
class LedgerView:
    """The transactions of a loaded ledger as Beancount booked them, in Beancount's order."""

    transactions: tuple[BookedTxn, ...]


class Fragment(Protocol):
    @property
    def name(self) -> str:
        """Kebab-case and unique in a composition; it seeds the fragment's random stream."""
        ...

    @property
    def assertion(self) -> str:
        """The fact `check` verifies, in one sentence."""
        ...

    def build(self, ctx: FragmentContext) -> Sequence[Directive]:
        """The fragment's directives, every one dated inside `ctx.span`."""
        ...

    def check(self, view: LedgerView, span: Span, size: Size) -> list[str]:
        """The problems with the assertion on a loaded ledger; empty when it holds."""
        ...
