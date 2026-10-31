"""The TLA+ model's code map, the engine's side: a no-op marker (ADR-0050; ARCHITECTURE.md §8.3 "Code mapping"; S0064).

`@tla_action("S_Swap")` on a function says that it carries out that action of `formal/tla/engine-jobs/EngineJobs.tla`
(or the engine's part of it). The marker changes nothing at run time and registers nothing: it returns the function
unchanged. `backend/tests/architecture/test_tla_mapping.py` reads the markers from the source (never importing the
engine, ADR-0054) and checks the map both ways against the model's `Next` relation; a marker is placed only on an
action the conformance replay walks through the code (`tests/ledger/test_save_conformance.py`; S0060's rule).
"""

from __future__ import annotations

from collections.abc import Callable


def tla_action[F: Callable[..., object]](*actions: str) -> Callable[[F], F]:
    """Mark a function as the code of these model actions (`actions` is read only by the map's check). A no-op."""
    del actions

    def mark(f: F) -> F:
        return f

    return mark
