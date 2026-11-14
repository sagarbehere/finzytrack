"""The `PlanStore` and the preview hash (`app.platform.plans`; S0117; ARCHITECTURE.md §2.5's state table, §4.2,
§9.2 steps 3-4; ADR-0008, ADR-0063; INV-10, INV-11). Written from the owner's ownership rule of 2026-11-14 (the S0117
brief): a plan is held in the app's store, never sent back by the client, and holds texts and hashes, never entries;
it is bound to its workspace and to the ledger state it was made on; it expires (TTL), is used once, and a switch
drops it. The store is bounded by a count as well as the TTL (INV-10).
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.platform.plans import (
    PlanId,
    PlannedFile,
    PlanPolicy,
    PlanStore,
    WritePlan,
    new_plan,
    preview_hash,
)

T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
FILE = PlannedFile(path="/L/main.bean", pre="a" * 64, post="b" * 64, text="2026-01-01 open Assets:Cash\n")


class MovingClock:
    """A clock a test moves (EP-28: no real waits)."""

    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def plan(n: int = 1, workspace: str = "ws-1", at: datetime = T0, **kw: object) -> WritePlan:
    fields: dict[str, object] = {
        "plan_id": PlanId(f"p{n}"),
        "workspace": workspace,
        "root": "/L/main.bean",
        "mutator": "test.edits",
        "generation": 3,
        "fingerprint": '{"v":2}',
        "files": (FILE,),
        "new_error_keys": (),
        "made_at": at,
    }
    fields.update(kw)
    return new_plan(**fields)  # pyright: ignore[reportArgumentType] (a test's fields)


def store(clock: MovingClock | None = None, **policy: object) -> PlanStore:
    return PlanStore(clock or MovingClock(), PlanPolicy(**policy))  # pyright: ignore[reportArgumentType]


# -- the preview hash -------------------------------------------------------------------------------------------------


def test_the_preview_hash_is_the_plans_own_and_stable() -> None:
    a, b = plan(1), plan(2)
    assert a.preview_hash == b.preview_hash  # the id is not part of what the user saw
    assert len(a.preview_hash) == 64
    assert a.preview_hash == preview_hash(
        a.workspace, a.root, a.mutator, a.generation, a.fingerprint, a.files, a.new_error_keys
    )


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"workspace": "ws-2"}, id="workspace"),
        pytest.param({"root": "/L/other.bean"}, id="root"),
        pytest.param({"mutator": "account.create"}, id="mutator"),
        pytest.param({"generation": 4}, id="generation"),
        pytest.param({"fingerprint": '{"v":3}'}, id="fingerprint"),
        pytest.param({"files": (dataclasses.replace(FILE, path="/L/b.bean"),)}, id="a file's path"),
        pytest.param({"files": (dataclasses.replace(FILE, pre="c" * 64),)}, id="a file's pre hash"),
        pytest.param({"files": (dataclasses.replace(FILE, post="c" * 64),)}, id="a file's post hash"),
        pytest.param({"files": (FILE, FILE)}, id="a file twice"),
        pytest.param({"new_error_keys": ("k1",)}, id="an error key"),
    ],
)
def test_the_preview_hash_changes_with_everything_the_user_saw(change: dict[str, object]) -> None:
    assert plan(**change).preview_hash != plan().preview_hash  # pyright: ignore[reportArgumentType] (a test's fields)


def test_the_preview_hash_covers_the_text_through_its_post_hash_only() -> None:
    """The text is what the save writes; the plan's `post` is its SHA-256, which the confirm checks against the text
    (`app.workspaces.writes.plan_problems`), so the hash need not read the text twice."""
    assert plan(files=(dataclasses.replace(FILE, text="x\n"),)).preview_hash == plan().preview_hash


# -- the store --------------------------------------------------------------------------------------------------------


def test_a_plan_is_found_in_its_workspace_and_nowhere_else() -> None:
    s = store()
    p = plan()
    s.add(p)
    assert s.find("ws-1", "p1") is p
    assert s.find("ws-2", "p1") is None
    assert s.find("ws-1", "p2") is None


def test_a_plan_is_taken_once() -> None:
    s = store()
    s.add(plan())
    assert s.take("ws-1", "p1") is not None
    assert s.take("ws-1", "p1") is None
    assert s.find("ws-1", "p1") is None


def test_a_plan_of_another_workspace_is_never_taken() -> None:
    s = store()
    s.add(plan())
    assert s.take("ws-2", "p1") is None
    assert s.find("ws-1", "p1") is not None  # and the other workspace's attempt did not use it up


def test_a_plan_expires_after_its_ttl() -> None:
    clock = MovingClock()
    s = store(clock, ttl_seconds=15 * 60)
    s.add(plan())
    clock.at = T0 + timedelta(minutes=15) - timedelta(microseconds=1)
    assert s.find("ws-1", "p1") is not None
    clock.at = T0 + timedelta(minutes=15)
    assert s.find("ws-1", "p1") is None
    assert s.take("ws-1", "p1") is None
    assert len(s) == 0  # an expired plan is let go, not kept until the count pushes it out


def test_an_expired_plan_is_not_taken_even_when_it_was_found_before() -> None:
    clock = MovingClock()
    s = store(clock, ttl_seconds=60)
    s.add(plan())
    assert s.find("ws-1", "p1") is not None
    clock.at = T0 + timedelta(minutes=2)
    assert s.take("ws-1", "p1") is None


def test_the_store_keeps_at_most_max_plans_and_lets_the_oldest_go() -> None:
    s = store(max_plans=2)
    for n in (1, 2, 3):
        s.add(plan(n))
    assert len(s) == 2
    assert s.find("ws-1", "p1") is None
    assert s.find("ws-1", "p2") is not None
    assert s.find("ws-1", "p3") is not None


def test_opening_a_workspace_drops_every_plan_of_another() -> None:
    """ADR-0063 decision 1 step 3: a switch discards the closed workspace's unconfirmed previews."""
    s = store()
    s.add(plan(1, "ws-1"))
    s.add(plan(2, "ws-2"))
    s.workspace_opened("ws-2")
    assert s.find("ws-1", "p1") is None
    assert s.find("ws-2", "p2") is not None


def test_closing_the_workspace_drops_every_plan() -> None:
    s = store()
    s.add(plan(1, "ws-1"))
    s.workspace_closed()
    assert s.find("ws-1", "p1") is None
    assert len(s) == 0


def test_a_plan_id_used_twice_is_refused() -> None:
    s = store()
    s.add(plan())
    with pytest.raises(ValueError, match="p1"):
        s.add(plan())


def test_two_threads_taking_one_plan_get_it_once() -> None:
    s = store()
    s.add(plan())
    got: list[WritePlan | None] = []
    start = threading.Barrier(8)

    def take() -> None:
        start.wait()
        got.append(s.take("ws-1", "p1"))

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(g is not None for g in got) == 1


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param({"ttl_seconds": 0}, id="no time"),
        pytest.param({"ttl_seconds": -1}, id="negative time"),
        pytest.param({"max_plans": 0}, id="no plan"),
    ],
)
def test_a_policy_with_no_room_is_refused(policy: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="plan"):
        PlanPolicy(**policy)  # pyright: ignore[reportArgumentType]


def test_a_plan_holds_texts_and_hashes_never_entries() -> None:
    """INV-10 (ARCHITECTURE §2.5: "each plan holds texts and hashes, never entries"): every field of a plan and of its
    files is a string, an int, an instant or a tuple of those."""
    for f in dataclasses.fields(WritePlan):
        assert f.type in {
            "PlanId",
            "PreviewHash",
            "str",
            "int",
            "datetime",
            "tuple[PlannedFile, ...]",
            "tuple[str, ...]",
        }
    for f in dataclasses.fields(PlannedFile):
        assert f.type == "str"
