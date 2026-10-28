"""The real `Ids` adapter (ARCHITECTURE.md §2.3: uuid7 via `uuid-utils`; EP-10; S0048).

uuid7 ids are unique and ordered by the time they were made, so request ids sort with the log. The text is the
canonical lower-case form (hex digits and hyphens), safe to log.
"""

from __future__ import annotations

import uuid_utils


class Uuid7Ids:
    def new_id(self) -> str:
        return str(uuid_utils.uuid7())
