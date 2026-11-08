"""The mirror-publish TLA+ model's code map, the sidecar's side: a no-op marker (ARCHITECTURE.md §8.3; S0087).

`@tla_action("R_Resolve", model="mirror-publish")` on a function says that it carries out that action of
`formal/tla/mirror-publish/MirrorPublish.tla` (the reads of the screens and of outside tools, the sidecar's side of the
publish). It changes nothing at run time and registers nothing. `tests/architecture/test_tla_mapping_mirror_publish.py`
reads the markers from the source of both packages and checks the map both ways. The engine has its own copy
(`ledger_engine.tla`): the two processes share no code (ADR-0054).
"""

from __future__ import annotations

from collections.abc import Callable


def tla_action[F: Callable[..., object]](*actions: str, model: str) -> Callable[[F], F]:
    """Mark a function as the code of these actions of `model` (read only by the map's check). A no-op."""
    del actions, model

    def mark(f: F) -> F:
        return f

    return mark
