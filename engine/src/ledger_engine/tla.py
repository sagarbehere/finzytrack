"""The TLA+ model's code map, the engine's side: a no-op marker (ADR-0050; ARCHITECTURE.md §8.3 "Code mapping"; S0064).

`@tla_action("S_Swap")` on a function says that it carries out that action of `formal/tla/engine-jobs/EngineJobs.tla`
(or the engine's part of it); `@tla_action("P_Swap", model="mirror-publish")` names an action of
`formal/tla/mirror-publish/MirrorPublish.tla` (S0087). The marker changes nothing at run time and registers nothing:
it returns the function unchanged. `backend/tests/architecture/test_tla_mapping.py` (and `_mirror_publish.py`) read
the markers from the source (never importing the engine, ADR-0054) and check each map both ways against its model's
`Next` relation. An engine-jobs marker is placed only on an action a conformance replay walks through the code
(`tests/ledger/test_save_conformance.py`, the save; `tests/ledger/test_create_conformance.py`, the create and the
copy, S0084; `tests/ledger/test_recover_conformance.py`, recover-all, S0091; S0060's rule). Since S0091 (BL-0210) the
mirror-publish model's markers follow the same rule: `tests/mirror/test_publish_conformance.py` replays its traces
through the rebuild and the publish; the sidecar's reads are walked by its own tests.
"""

from __future__ import annotations

from collections.abc import Callable


def tla_action[F: Callable[..., object]](*actions: str, model: str = "engine-jobs") -> Callable[[F], F]:
    """Mark a function as the code of these model actions (`actions` is read only by the map's check). A no-op.
    `model`: the model the actions are of (S0087: `"mirror-publish"`, `formal/tla/mirror-publish/MirrorPublish.tla`,
    checked by `backend/tests/architecture/test_tla_mapping_mirror_publish.py`)."""
    del actions, model

    def mark(f: F) -> F:
        return f

    return mark
