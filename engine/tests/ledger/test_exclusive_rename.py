"""S0125 round 1 (owner, 2026-11-17: "A2,B2,C1"), over the in-memory file system:

- **A2, the exclusive rename everywhere** (red team F1, class (a)): a created file, a kept copy and a manifest are
  put at their name by the exclusive rename wherever the volume really has it; a volume whose answer is learned only at
  first use (Linux: `renameat2(RENAME_NOREPLACE)` refused `EINVAL`, `ENOSYS` or `EOPNOTSUPP`) raises `NotOffered` with
  nothing done, and only then does §6.8's check-then-rename apply, so a file another program put at the name before the
  check is never replaced (`changed`, kept).
- **C1, a named refusal** where the app's data folder offers no exclusive rename: `unsafe_app_folder`, before any
  ledger byte changes, not a generic `io_error`.
"""

from __future__ import annotations

import pytest

from ledger_engine.ledger.restore import Stopped, recover_journal
from ledger_engine.ledger.writer import Landed, NotSaved, Reason, Unfinished, durable_commit
from tests.fakes.memfs import Crash, MemFS
from tests.ledger.worlds import BD, JD, NAME, World, world

pytestmark = [pytest.mark.inv("INV-01"), pytest.mark.inv("INV-22")]

FOREIGN = b"; another program's file\n"
CREATED = "/w/L/prices.bean"


def _before_effect(fs: MemFS, starts: str, act: object) -> None:
    """Run `act` right before the first effect whose label starts with `starts`."""
    done = False

    def hook(_: int, seen: str) -> None:
        nonlocal done
        if not done and seen.startswith(starts):
            done = True
            act()  # type: ignore[operator]

    fs.on_effect = hook


def _plain(unknown: bool) -> World:
    """The test world with `/w/L` on a volume without the swap and without the exclusive rename; `unknown`: one whose
    answer the adapter learns only at the first exclusive rename (Linux), else one that says so when asked (macOS)."""
    w = world(volume="plain")
    if unknown:
        w.fs.outside_excl_refused_at_use("/w/L")
    return w


@pytest.mark.parametrize("unknown", [False, True])
def test_A2_a_file_another_program_put_at_a_created_path_before_its_rename_is_kept(unknown: bool) -> None:
    """Must not pass (red team F1): on a volume without the exclusive rename the created file was put at its name by a
    plain rename, which replaced a file another program had made there after the gate. Now the name is checked before
    the plain rename: `changed`, the other program's file kept, the ledger as before."""
    w = _plain(unknown)
    _before_effect(w.fs, "create /w/L:.prices.bean.ft-", lambda: w.fs.outside_write(CREATED, FOREIGN))
    out = durable_commit(w.fs, w.commit)
    # the created file comes after `inc/a.bean` in the plan, so the undo meets the other program's file at a path the
    # save was creating and stops there, never removing it (owner, 2026-10-30): unfinished, `created_path_taken`
    assert isinstance(out, Unfinished), out
    assert (out.reason, out.cause, out.path) == (Reason.CHANGED, "created_path_taken", CREATED)
    assert w.fs.file(CREATED) == FOREIGN
    assert w.fs.file("/w/L/main.bean") == w.targets[2].pre, "the root, last in the plan, was never replaced"


def test_A2_a_volume_that_refuses_the_exclusive_rename_at_first_use_takes_the_best_effort_mode_and_lands() -> None:
    """The first exclusive rename is the probe: refused, nothing done (`NotOffered`), the volume then says it has none
    (`caps().excl` False for the rest of the job), and the created file is put by the check-then-rename."""
    w = _plain(unknown=True)
    with w.fs.open_dir("/w/L") as d:
        assert d.caps().excl, "not known before the first use"
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, Landed), out
    assert w.fs.file(CREATED) == w.targets[1].post
    with w.fs.open_dir("/w/L") as d:
        assert not d.caps().excl, "known after the first use"


def test_A2_two_outside_entries_kept_where_the_exclusive_rename_is_refused_at_first_use_each_get_their_own_name() -> (
    None
):
    """Keep's own kept names (S0071 red team F1) on a volume learned at first use: the refused exclusive rename falls
    back to the check, so the second entry never replaces the first kept copy."""
    w = _plain(unknown=True)
    put = next(e for e in _effects_of_a_clean_plain_save() if e.startswith("move /w/L:.main.bean"))
    w.fs.inject(_effects_of_a_clean_plain_save().index(put) + 1, crash=True)
    with pytest.raises(Crash):
        durable_commit(w.fs, w.commit)
    w.fs.outside_excl_refused_at_use("/w/L")  # a fresh job: the volume's answer is learned again
    x, y = b"; X\n", b"; Y\n"
    w.fs.outside_write("/w/L/.main.bean.ft-s1-j7.tmp", x)
    w.fs.outside_write("/w/L/.main.bean.ft-restore-s1-j7.tmp", y)
    out = recover_journal(w.fs, JD, NAME)
    assert isinstance(out, Stopped), out
    kept = sorted(w.fs.file(f"/w/L/{n}") or b"" for n in w.fs.names_in("/w/L") if ".kept-" in n)
    assert kept == [x, y], kept


def _effects_of_a_clean_plain_save() -> tuple[str, ...]:
    w = _plain(unknown=False)
    assert isinstance(durable_commit(w.fs, w.commit), Landed)
    return tuple(w.fs.effects)


@pytest.mark.parametrize("unknown", [False, True])
def test_C1_a_journal_folder_without_the_exclusive_rename_is_a_named_refusal_and_no_ledger_byte_changes(
    unknown: bool,
) -> None:
    """Must not pass (verifier F3; owner: "C1"): the app's data folder on a file system without the exclusive rename
    (ecryptfs, an NFS home, overlay) failed every save with a generic `io_error` at the journal. It is now refused as
    `unsafe_app_folder`, naming the folder, before the record is `.open` and before any ledger file is replaced."""
    w = world()
    if unknown:
        w.fs.outside_excl_refused_at_use(JD)
    else:
        w.fs.outside_no_swap(JD)
    out = durable_commit(w.fs, w.commit)
    assert isinstance(out, NotSaved), out
    assert (out.reason, out.unchanged) == (Reason.UNSAFE_APP_FOLDER, True)
    assert out.entry == JD
    for t in w.targets:
        assert w.fs.file(t.path) == t.pre
    if not unknown:  # the volume said so when asked: refused before the backups and the record were written
        assert w.fs.names_in(JD) == []
        assert w.fs.names_in(BD) == []
