"""The `PlanStore` and the preview hash (`app.platform.plans`; S0117; ARCHITECTURE.md §2.5's state table, §4.2,
§9.2 steps 3-4; ADR-0008, ADR-0063; INV-10, INV-11). Written from the owner's ownership rule of 2026-11-14 (the S0117
brief): a plan is held in the app's store, never sent back by the client, and holds texts and hashes, never entries;
it is bound to its workspace and to the ledger state it was made on; it expires (TTL), is used once, and a switch
drops it. The store is bounded by a count and (S0120) the bytes of its texts as well as the TTL (INV-10).
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.platform.plans import (
    LARGE_FILE_BYTES,
    PlanId,
    PlannedFile,
    PlanPolicy,
    PlanStore,
    WritePlan,
    is_large,
    new_plan,
    preview_hash,
    text_bytes,
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
        pytest.param({"max_text_bytes": 0}, id="no bytes"),
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


# -- S0117 round 1 -----------------------------------------------------------------------------------------------------


def test_a_taken_plan_is_remembered_as_used_in_its_workspace_only() -> None:
    """Red team RT-F2: a second confirm of a confirmed plan is told so."""
    s = store()
    s.add(plan())
    assert not s.used("ws-1", "p1")
    assert s.take("ws-1", "p1") is not None
    assert s.used("ws-1", "p1")
    assert not s.used("ws-2", "p1")
    assert not s.used("ws-1", "p2")


def test_a_plan_found_or_dropped_is_not_used() -> None:
    s = store()
    s.add(plan())
    assert s.find("ws-1", "p1") is not None
    s.workspace_closed()
    assert not s.used("ws-1", "p1")


def test_used_ids_survive_a_switch_and_are_bounded() -> None:
    s = store(max_used=2)
    for n in (1, 2, 3):
        s.add(plan(n))
        assert s.take("ws-1", f"p{n}") is not None
    s.workspace_closed()
    s.workspace_opened("ws-2")
    assert [s.used("ws-1", f"p{n}") for n in (1, 2, 3)] == [False, True, True]


def test_a_clock_set_back_before_the_plan_was_made_expires_it() -> None:
    """Red team RT-F5, verifier V1: a negative age never lengthens a plan's life."""
    clock = MovingClock()
    s = store(clock)
    s.add(plan())
    clock.at = T0 - timedelta(microseconds=1)
    assert s.find("ws-1", "p1") is None
    assert len(s) == 0


def test_a_policy_that_remembers_no_used_id_is_refused() -> None:
    with pytest.raises(ValueError, match="plan"):
        PlanPolicy(max_used=0)


# -- S0117 round 3 (verifier V-R3-5) --------------------------------------------------------------------------------


def test_the_fate_of_a_plan_tells_used_let_go_and_never_known_apart() -> None:
    clock = MovingClock()
    s = store(clock, max_plans=2)
    for n in (1, 2, 3):  # p1 pushed out by p3
        s.add(plan(n))
    assert s.take("ws-1", "p2") is not None
    s.add(plan(4, "ws-2"))
    assert s.fate("ws-1", "p1") == "gone"  # pushed out by newer previews
    assert s.fate("ws-1", "p2") == "used"
    assert s.fate("ws-1", "p4") == "gone"  # held for another workspace
    assert s.fate("ws-2", "p2") == "gone"  # used in another workspace: not saved in this one
    assert s.fate("ws-1", "p9") == "unknown"
    clock.at = T0 + timedelta(hours=1)
    assert s.find("ws-1", "p3") is None
    assert s.fate("ws-1", "p3") == "gone"  # expired
    s.add(plan(5))
    s.workspace_closed()
    assert s.fate("ws-1", "p5") == "gone"  # dropped by a switch


def test_the_memory_of_plans_let_go_is_bounded() -> None:
    s = store(max_plans=1, max_used=2)
    for n in (1, 2, 3, 4):
        s.add(plan(n))
    assert [s.fate("ws-1", f"p{n}") for n in (1, 2, 3)] == ["unknown", "gone", "gone"]


# -- S0120: the byte budget (INV-10; ADR-0008 amendment of 2026-11-15) -------------------------------------------------


def sized(n: int, size: int, workspace: str = "ws-1") -> WritePlan:
    """A plan whose one file's new text is `size` bytes of UTF-8."""
    text = "x" * size
    return plan(n, workspace, files=(dataclasses.replace(FILE, path=f"/L/{n}.bean", text=text),))


def test_the_default_budget_is_128_mb_over_the_plans_texts() -> None:
    """S0120: the store's bound was `max_plans` x the line limit (4 x 16 MiB); with a 256 MiB guard that would be
    about 1 GB, so the texts have their own budget."""
    assert PlanPolicy().max_text_bytes == 128_000_000


def test_a_plan_s_bytes_are_its_texts_in_utf_8_over_all_its_files() -> None:
    two = plan(1, files=(dataclasses.replace(FILE, text="é" * 10), dataclasses.replace(FILE, path="/L/b", text="ab")))
    assert text_bytes(two) == 22  # 10 characters of two bytes each, and two of one


def test_the_store_lets_the_oldest_go_until_the_texts_fit_the_budget() -> None:
    """Must not pass: a store over its byte budget while it holds more than the newest plan."""
    s = store(max_text_bytes=100)
    s.add(sized(1, 40))
    s.add(sized(2, 40))
    assert len(s) == 2  # 80 bytes: both fit
    s.add(sized(3, 40))  # 120: the oldest goes
    assert [s.find("ws-1", f"p{n}") is not None for n in (1, 2, 3)] == [False, True, True]
    assert s.fate("ws-1", "p1") == "gone"
    s.add(sized(4, 100))  # 180, then 140: both older ones go, the newest alone fits exactly
    assert [s.find("ws-1", f"p{n}") is not None for n in (2, 3, 4)] == [False, False, True]


def test_one_byte_over_the_budget_lets_the_oldest_go() -> None:
    """The boundary (mutant `s-budget-off-by-one`): exactly at the budget both stay; one byte over, the oldest goes."""
    s = store(max_text_bytes=100)
    s.add(sized(1, 40))
    s.add(sized(2, 60))
    assert len(s) == 2
    s = store(max_text_bytes=100)
    s.add(sized(1, 40))
    s.add(sized(2, 61))
    assert [s.find("ws-1", f"p{n}") is not None for n in (1, 2)] == [False, True]


def test_the_newest_plan_is_always_kept_even_alone_over_the_budget() -> None:
    """S0120: a file of any size can be previewed and confirmed; the plan let go for it answers `PLAN_EXPIRED`."""
    s = store(max_text_bytes=100)
    s.add(sized(1, 40))
    s.add(sized(2, 101))
    assert s.find("ws-1", "p1") is None
    kept = s.find("ws-1", "p2")
    assert kept is not None
    assert s.take("ws-1", "p2") is kept
    s.add(sized(3, 250))
    s.add(sized(4, 10))  # a newer plan: the large one goes
    assert s.find("ws-1", "p3") is None
    assert s.fate("ws-1", "p3") == "gone"
    assert s.find("ws-1", "p4") is not None


def test_the_budget_counts_bytes_not_characters() -> None:
    s = store(max_text_bytes=100)
    s.add(plan(1, files=(dataclasses.replace(FILE, text="é" * 30),)))  # 60 bytes
    s.add(plan(2, files=(dataclasses.replace(FILE, path="/L/b", text="é" * 30),)))  # 120 in all
    assert s.find("ws-1", "p1") is None
    assert s.find("ws-1", "p2") is not None


def test_a_taken_or_let_go_plan_frees_its_bytes() -> None:
    s = store(max_text_bytes=100)
    s.add(sized(1, 60))
    assert s.take("ws-1", "p1") is not None
    s.add(sized(2, 60))
    s.add(sized(3, 40))  # 100: fits, p1's bytes are not counted any more
    assert len(s) == 2
    s.workspace_closed()
    s.add(sized(4, 100))
    assert len(s) == 1


# -- S0120: the large-file tip's threshold -----------------------------------------------------------------------------


def test_the_large_file_threshold_is_15_mb() -> None:
    assert LARGE_FILE_BYTES == 15_000_000


@pytest.mark.parametrize(
    ("pre_size", "text", "large"),
    [
        pytest.param(15_000_000, "x" * 15_000_000, False, id="both at the threshold"),
        pytest.param(15_000_001, "x", True, id="the bytes now over it"),
        pytest.param(10, "x" * 15_000_001, True, id="the candidate over it"),
        pytest.param(10, "é" * 7_500_001, True, id="the candidate over it in bytes, not in characters"),
        pytest.param(0, "", False, id="empty"),
    ],
)
def test_a_file_is_large_when_its_bytes_now_or_its_new_text_pass_15_mb(pre_size: int, text: str, large: bool) -> None:
    """S0120 (ADR-0008 amendment of 2026-11-15): larger than 15 MB (15,000,000 bytes), its bytes now or the
    candidate's in UTF-8; at the threshold it is not large."""
    assert is_large(pre_size, text) is large
