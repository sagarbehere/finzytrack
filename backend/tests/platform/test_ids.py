"""The Ids port's real adapter (ARCHITECTURE.md §2.3: `Ids`, adapter uuid7 via `uuid-utils`; EP-10; S0048).

The contract (`app.platform.ids.Ids.new_id`): unique for the process's life and safe to log (letters, digits and
hyphens only). uuid7 also orders ids by the time they were made, so request ids sort with the log.
"""

from __future__ import annotations

import re
import uuid

from app.platform.adapters.ids import Uuid7Ids
from app.platform.ids import Ids

SAFE = re.compile(r"[A-Za-z0-9-]+")


def test_ids_are_uuid7_unique_and_safe_to_log() -> None:
    ids: Ids = Uuid7Ids()
    made = [ids.new_id() for _ in range(2000)]
    assert len(set(made)) == len(made)
    for text in made:
        assert SAFE.fullmatch(text), text
        parsed = uuid.UUID(text)
        assert parsed.version == 7
        assert str(parsed) == text  # the canonical lower-case form, one spelling per id


def test_ids_made_later_sort_later() -> None:
    ids = Uuid7Ids()
    made = [ids.new_id() for _ in range(2000)]
    assert made == sorted(made)
