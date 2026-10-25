"""Compose fragments into one demo ledger (ADR-0022): deterministic in (anchor, size, fragment names)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, localcontext

from app.content.demo.household import Household
from app.content.demo.ledger_text import Directive
from app.content.demo.model import Fragment, FragmentContext, Size, span_for
from app.content.demo.rng import SeededRng

# Raised when the same inputs would give other bytes, with the new version's pins added to
# tests/content/demo_pins.toml in the same commit (append-only; tests/content/test_demo_pins.py).
GENERATOR_VERSION = 1
ROOT_FILE = "main.beancount"
_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def default_fragments() -> tuple[Fragment, ...]:
    """Every fragment of the shipped demo. Each milestone adds the fragments of its capabilities (ADR-0022)."""
    return (Household(),)


@dataclass(frozen=True, slots=True)
class DemoLedger:
    """The generated files (path relative to the ledger folder → text) and the inputs they were made for."""

    files: Mapping[str, str]
    anchor: date
    size: Size
    fragments: tuple[str, ...]

    def generated_for_json(self) -> str:
        """The `generated-for` record written beside the files (review:L10 §1.5; L10-18)."""
        record = {
            "anchor": self.anchor.isoformat(),
            "fragments": list(self.fragments),
            "generator": GENERATOR_VERSION,
            "size": self.size.value,
        }
        return json.dumps(record, indent=2, sort_keys=True) + "\n"


def generate(anchor: date, size: Size, fragments: Sequence[Fragment]) -> DemoLedger:
    """The demo ledger for *anchor* and *size*. Each fragment gets its own stream, seeded by its name only.

    Decimal arithmetic runs in a fixed context, so a caller's `decimal` context cannot change the bytes.
    """
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return _generate(anchor, size, fragments)


def _generate(anchor: date, size: Size, fragments: Sequence[Fragment]) -> DemoLedger:
    names = [f.name for f in fragments]
    for name in names:
        if not _NAME.fullmatch(name):
            msg = f"fragment name {name!r} must be kebab-case (lower-case letters, digits and single hyphens)"
            raise ValueError(msg)
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        msg = f"two fragments are named {', '.join(dupes)}; each name seeds one stream"
        raise ValueError(msg)
    span = span_for(anchor, size)
    keyed: list[tuple[date, int, str, int, Directive]] = []
    for fragment in fragments:
        ctx = FragmentContext(span, size, SeededRng.for_name(fragment.name))
        for seq, d in enumerate(fragment.build(ctx)):
            if not span.start <= d.date <= span.end:
                msg = f"fragment {fragment.name!r} wrote {d.date}, outside {span.start}..{span.end}"
                raise ValueError(msg)
            keyed.append((d.date, d.rank, fragment.name, seq, d))
    keyed.sort(key=lambda k: k[:4])
    header = (
        f"; Finzytrack demo ledger: synthetic data, generated for {anchor.isoformat()} (size {size.value}).\n"
        "; Made by app.content.demo (backend/scripts/gen_demo.py); regenerate it instead of editing it.\n"
        "\n"
        'option "title" "Finzytrack demo"\n'
        'option "operating_currency" "USD"'
    )
    text = "\n\n".join([header, *(k[4].text for k in keyed)]) + "\n"
    return DemoLedger({ROOT_FILE: text}, anchor, size, tuple(sorted(names)))
