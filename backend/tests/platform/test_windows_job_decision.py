""" "No live member" on Windows as a pure decision (S0129, BL-0281; `design/specs/engine-jobs.md` §5.2), run on every
OS; the real Job Object is `tests/platform/test_engine_windows.py`'s, on the Windows runner.

Written from the spec: "'No live member', per OS (so that no member still holds a file ...)": on Windows a member we
hold counts until its process object is signalled (its handles are closed by then); a failed listing, or a listed
member that could not be judged, counts as live: "the drain never reaps on a guess".
"""

from __future__ import annotations

import pytest

from app.platform.adapters.engine_group import live_member_in_job


@pytest.mark.parametrize(
    ("listed", "judged", "ended", "live"),
    [
        ([], True, {}, False),  # nothing listed, nothing held: the drain ends
        ([], True, {7: True, 9: True}, False),  # every member we held has ended
        ([], True, {7: True, 9: False}, True),  # gone from the list, still exiting: its handles may be open
        ([7], True, {7: False}, True),  # listed and running
        ([7], True, {7: True}, False),  # listed for a moment more, but ended
        (None, True, {}, True),  # the listing failed
        ([7], False, {}, True),  # a listed member could not be opened or judged
        ([7], False, {7: True}, True),
    ],
)
def test_a_held_member_counts_until_it_has_ended_and_a_guess_counts_as_live(
    listed: list[int] | None, judged: bool, ended: dict[int, bool], live: bool
) -> None:
    assert live_member_in_job(listed, judged, ended) is live
