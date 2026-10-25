"""The deterministic demo and corpus generator (ADR-0022; ARCHITECTURE.md §2.3 `demo.generate`).

`generate(anchor, size, fragments)` returns ledger text for the span of `size.months` months ending on `anchor`.
The same inputs give the same bytes; nothing reads a clock, the environment or ambient randomness (EP-10).
"""

from app.content.demo.anchor import anchor_from_epoch, anchor_from_iso
from app.content.demo.compose import GENERATOR_VERSION, ROOT_FILE, DemoLedger, default_fragments, generate
from app.content.demo.household import Household
from app.content.demo.ledger_text import Directive, Posting, commodity, open_account, transaction
from app.content.demo.model import (
    BookedPosting,
    BookedTxn,
    Fragment,
    FragmentContext,
    LedgerView,
    Size,
    Span,
    span_for,
)
from app.content.demo.rng import SeededRng

__all__ = [
    "GENERATOR_VERSION",
    "ROOT_FILE",
    "BookedPosting",
    "BookedTxn",
    "DemoLedger",
    "Directive",
    "Fragment",
    "FragmentContext",
    "Household",
    "LedgerView",
    "Posting",
    "SeededRng",
    "Size",
    "Span",
    "anchor_from_epoch",
    "anchor_from_iso",
    "commodity",
    "default_fragments",
    "generate",
    "open_account",
    "span_for",
    "transaction",
]
