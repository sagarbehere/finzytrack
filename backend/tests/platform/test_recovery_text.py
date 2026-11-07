"""What the user reads about recover-all (`app.platform.engine_jobs.recovery_text`; `design/specs/engine-jobs.md`
§8.1 "Afterwards", §11's blocked-screen rows, §15; S0062).

Written from §11: each blocking reason and cause has its row's text and actions; the kept-copy row names the copy by
its real name and says when the keep happened; the created-path row is the owner's text with Show it and Delete it;
the ACL row names the folder and the entry; `failed` names the file, the entry that failed and the system's answer,
and says when the drive is read-only (red team r8 F1); the Keep confirmation says Retry may still undo it for
`cancelled`, `failed` and `not_tried`, and that a kept copy must be deleted or moved; the Reopen text after a stop
at a created path does not promise an undo (r8 overflow 1).
"""

from __future__ import annotations

import errno
import os
from dataclasses import replace

import pytest

from app.platform.engine import ActOutcome, EntrySeen, InspectedFile, JournalFile, KeptCopySeen
from app.platform.engine_jobs.recovery import ENGINE_STOPPED, Blocking, Journal, NotRecovered
from app.platform.engine_jobs.recovery_text import (
    REOPEN,
    action_text,
    blocked_screen,
    delete_created_confirmation,
    delete_kept_confirmation,
    keep_confirmation,
    not_recovered_screen,
    reopen_after_created,
    undone_notice,
)

WS = "Personal"
J = Journal(
    "/app/local/k1/journal",
    "s1-j7",
    True,
    "k1",
    "lin1",
    (JournalFile("/L/accounts.beancount", "/l/accounts.beancount", False), JournalFile("/L/prices.bean", "/l/p", True)),
)
SEEN = EntrySeen("file", "0" * 64, 10)


def kept_inspection(*names: str) -> tuple[InspectedFile, ...]:
    kept = tuple(KeptCopySeen(n, f"/L/{n}", SEEN) for n in names)
    return (InspectedFile("/L/accounts.beancount", False, True, SEEN, kept, None),)


def test_the_undo_notice() -> None:
    assert undone_notice(WS) == "Finzytrack undid an interrupted save in Personal."


@pytest.mark.parametrize(
    ("cause", "when"),
    [
        ("kept_copy", "saving or undoing a change"),
        ("displaced", "undoing an unfinished change"),
        ("outside_save_in_temp", "undoing an unfinished change"),
    ],
)
def test_the_kept_copy_row_names_the_real_copy_and_when_it_was_kept(cause: str, when: str) -> None:
    b = Blocking(
        J,
        "stopped",
        cause,
        "/L/accounts.beancount",
        inspection=kept_inspection("accounts.beancount.kept-s1-j7-2"),
    )
    screen = blocked_screen(b, WS)
    assert f"while Finzytrack was {when}" in screen.message
    assert "/L/accounts.beancount.kept-s1-j7-2" in screen.message, "the copy's real name (a `-2` variant)"
    assert "delete the kept copy and press Retry" in screen.message
    assert "delete or move the kept copy before you use the ledger" in screen.message
    assert screen.actions == ("retry", "stay_or_back", "keep", "show_both", "delete_kept")


def test_the_created_path_row_is_the_owners_text() -> None:
    b = Blocking(J, "stopped", "created_path_taken", "/L/prices.bean")
    screen = blocked_screen(b, WS)
    assert screen.message == (
        "Finzytrack was creating prices.bean when the save was interrupted. It cannot tell whether this file holds "
        "that unfinished change or another program's file, so it left it alone. Look at it; if you don't want it, "
        "delete it and press Retry, or choose Keep the files as they are now."
    )
    assert screen.actions == ("show_it", "delete_created", "retry", "stay_or_back", "keep")


def test_the_acl_row_names_the_folder_and_the_entry() -> None:
    b = Blocking(J, "stopped", "permission", "/L/accounts.beancount", "/L", "deny delete_child,directory_inherit")
    message = blocked_screen(b, WS).message
    assert "The folder /L has a permission entry (ACL)" in message
    assert "deny delete_child,directory_inherit" in message
    assert "Remove that entry, then press Retry." in message


def test_a_permission_stop_on_a_file_says_how_to_remove_its_entry() -> None:
    """§15: an entry added during a save may be inherited by a hidden file of ours; the screen names that file."""
    b = Blocking(J, "stopped", "permission", "/L/accounts.beancount", "/L/.accounts.beancount.ft-s1-j7.tmp")
    message = blocked_screen(b, WS).message
    assert 'chmod -N "/L/.accounts.beancount.ft-s1-j7.tmp"' in message


def test_a_journal_that_could_not_be_undone_has_adr_0063s_text() -> None:
    b = Blocking(J, "stopped", "foreign", "/L/accounts.beancount")
    screen = blocked_screen(b, WS)
    assert screen.message == "accounts.beancount has an unfinished save from Personal that could not be undone."
    assert screen.actions == ("retry", "stay_or_back", "keep")


def test_a_record_that_cannot_be_read_blocks_every_open_and_says_so() -> None:
    b = Blocking(Journal("/app/local/k1/journal", "s9-j9", False, None, None, ()), "stopped", "unreadable")
    screen = blocked_screen(b, WS)
    assert "cannot read the record" in screen.message
    assert "No workspace opens" in screen.message
    assert screen.actions == ("retry", "stay_or_back", "keep"), "§11: Retry, Stay or Back, Keep (verifier r1 O4)"


def test_failed_on_a_refusal_has_a_next_step() -> None:
    """Red team r1 O3: a `failed` journal whose step the engine refused (no errno, not a stop) still says what to do."""
    message = blocked_screen(Blocking(J, "failed", "engine.internal"), WS).message
    assert message.endswith("Press Retry to try again.")


def test_an_action_on_a_folder_that_could_not_be_listed_is_not_called_resolved() -> None:
    """Red team r1 R1-4."""
    text = action_text(ActOutcome("refused", "unlisted", None, None, None))
    assert text is not None
    assert "resolved" not in text
    assert "could not read" in text


@pytest.mark.parametrize("reason", ["cancelled", "failed", "not_tried"])
def test_cancelled_failed_and_not_tried_say_it_is_not_undone_yet(reason: str) -> None:
    b = Blocking(J, reason, "io_error" if reason == "failed" else None)  # type: ignore[arg-type]
    screen = blocked_screen(b, WS)
    assert screen.message.startswith(
        "accounts.beancount has an unfinished save from Personal that Finzytrack has not undone yet."
    )
    assert screen.actions == ("retry", "stay_or_back", "keep")


def test_failed_names_the_file_the_entry_and_the_systems_answer() -> None:
    b = Blocking(J, "failed", "io_error", "/L/accounts.beancount", "/L/.accounts.beancount.ft-s1-j7.tmp", None, 5)
    message = blocked_screen(b, WS).message
    assert "accounts.beancount has an unfinished save" in message
    assert f"/L/.accounts.beancount.ft-s1-j7.tmp: {os.strerror(5)}" in message


def test_failed_on_a_read_only_drive_says_so() -> None:
    """Red team r8 F1: every Retry fails `EROFS` while the drive is read-only; Keep or a read-write remount clears
    it."""
    b = Blocking(J, "failed", "io_error", "/L/accounts.beancount", "/L", None, errno.EROFS)
    message = blocked_screen(b, WS).message
    assert "read-only" in message
    assert "Make the drive writable and press Retry, or choose Keep the files as they are now." in message


def test_failed_with_no_read_permission_gives_the_acl_way_out() -> None:
    b = Blocking(J, "failed", "io_error", "/L/accounts.beancount", "/app/local/k1/journal/s1-j7.open", None, 13)
    assert 'chmod -N "/app/local/k1/journal/s1-j7.open"' in blocked_screen(b, WS).message


def test_an_engine_that_stopped_while_undoing() -> None:
    message = blocked_screen(Blocking(J, "failed", ENGINE_STOPPED), WS).message
    assert "stopped while it was undoing it" in message


def test_nothing_listed_blocks_every_open_with_retry() -> None:
    assert not_recovered_screen(NotRecovered(None, None)).actions == ("retry",)
    message = not_recovered_screen(NotRecovered("/app/local/k1/journal", 5)).message
    assert "/app/local/k1/journal" in message
    assert os.strerror(5) in message


def test_a_linked_journal_folder_says_to_put_the_real_folder_back() -> None:
    """S0077 (BL-0193): the cause is named, and the way out; "could not read" would send the user to Retry alone."""
    screen = not_recovered_screen(NotRecovered("/app/local/k1/journal", None, linked=True))
    assert screen.actions == ("retry",)
    assert "/app/local/k1/journal is a link to another place" in screen.message
    assert "Put the real folder back in its place, then press Retry." in screen.message
    assert "If there is no real folder (the link is a mistake, or its disk is gone), remove the link" in screen.message
    assert "could not read" not in screen.message
    unreachable = not_recovered_screen(NotRecovered("/app/local/k1/journal", 2, linked=True)).message
    assert os.strerror(2) in unreachable


@pytest.mark.parametrize(("reason", "retry_may"), [("stopped", False), ("cancelled", True), ("failed", True)])
def test_the_keep_confirmation(reason: str, retry_may: bool) -> None:
    b = Blocking(J, reason, None, None, inspection=kept_inspection("accounts.beancount.kept-s1-j7"))  # type: ignore[arg-type]
    text = keep_confirmation(b, WS)
    assert "accounts.beancount, prices.bean" in text, "the files it releases"
    assert ("Retry may still undo it." in text) == retry_may
    assert "The kept copy /L/accounts.beancount.kept-s1-j7 stays. Delete or move it before you use the ledger." in text


def _with_missing(*missing: str, good: bool = True, cause: str = "foreign") -> Blocking:
    """A try stopped on a.bean (another program's file) while the files `missing` are absent: on a drive without the
    swap a power loss in the middle of a replace can leave them so (§6.8; S0071 red team F2). `good`: the engine found
    their backups holding the old versions (`backup_good`)."""
    files = ("/L/inc/a.bean", "/L/prices.bean", "/L/elsewhere/b.bean", "/L/d.bean", *(f"/L/{m}" for m in missing))
    journal = Journal(J.journal_dir, J.name, True, "k1", "lin1", tuple(JournalFile(f, f, False) for f in files))
    inspection = (
        InspectedFile("/L/inc/a.bean", False, True, SEEN, (), None, True),
        InspectedFile("/L/prices.bean", True, True, None, (), None),  # created, not there: never "missing"
        *(InspectedFile(f"/L/{m}", False, True, None, (), None, good) for m in missing),
        InspectedFile("/L/elsewhere/b.bean", False, False, None, (), None, True),  # its folder is not there: unknown
        InspectedFile("/L/d.bean", False, True, None, (), errno.EIO, True),  # the look failed: not known to be missing
    )
    return Blocking(journal, "stopped", cause, "/L/inc/a.bean", inspection=inspection)


def test_a_replaced_file_found_missing_is_named_on_the_screen_and_in_the_keep_confirmation() -> None:
    """Keep leaves a missing file missing (ADR-0068 D2), so the user is told before choosing it (§11)."""
    b = _with_missing("main.bean")
    assert blocked_screen(b, WS).message == (
        "a.bean has an unfinished save from Personal that could not be undone. "
        "main.bean is missing now. Finzytrack has its old version in its backup."
    )
    assert keep_confirmation(b, WS).endswith(
        " main.bean is missing now and will stay missing. Its old version is only in Finzytrack's backup."
    )
    two = _with_missing("main.bean", "c.bean")
    assert "main.bean and c.bean are missing now. Finzytrack has their old versions in its backup." in (
        blocked_screen(two, WS).message
    )
    assert (
        "main.bean and c.bean are missing now and will stay missing. Their old versions are only in Finzytrack's "
        "backup."
    ) in keep_confirmation(two, WS)
    nothing_missing = _with_missing()
    assert "missing" not in blocked_screen(nothing_missing, WS).message
    assert "missing" not in keep_confirmation(nothing_missing, WS)


@pytest.mark.parametrize("cause", ["foreign", "backup_bad"])
def test_a_missing_file_whose_backup_is_not_known_good_is_named_without_promising_the_backup(cause: str) -> None:
    """S0071 round 2 (R2-1): a try that stops `backup_bad` at the missing file, or never reached its backup; the
    engine's `backup_good` is False, so neither text claims a backup (and nothing else says "backup")."""
    b = _with_missing("main.bean", good=False, cause=cause)
    screen, keep = blocked_screen(b, WS).message, keep_confirmation(b, WS)
    assert screen.endswith(" main.bean is missing now."), screen
    assert keep.endswith(" main.bean is missing now and will stay missing."), keep
    assert "backup" not in screen
    assert "backup" not in keep
    mixed = _with_missing("main.bean", "c.bean", good=False)
    mixed = Blocking(
        mixed.journal,
        "stopped",
        "foreign",
        mixed.path,
        inspection=tuple(replace(i, backup_good=True) if i.path == "/L/c.bean" else i for i in mixed.inspection or ()),
    )
    assert blocked_screen(mixed, WS).message.endswith(
        " c.bean is missing now. Finzytrack has its old version in its backup. main.bean is missing now."
    )
    assert keep_confirmation(mixed, WS).endswith(
        " c.bean is missing now and will stay missing. Its old version is only in Finzytrack's backup."
        " main.bean is missing now and will stay missing."
    )


def test_the_delete_confirmations() -> None:
    assert "the other program's version of accounts.beancount" in delete_kept_confirmation(
        "/L/accounts.beancount", "accounts.beancount.kept-s1-j7"
    )
    assert delete_created_confirmation("prices.bean") == "Delete prices.bean? This cannot be undone."


def test_an_action_refused_because_the_bytes_changed() -> None:
    text = action_text(ActOutcome("refused", "changed", "/L/x", None, None))
    assert text is not None
    assert "changed after you looked at it" in text
    assert action_text(ActOutcome("done", None, None, None, None)) is None


def test_the_reopen_text_after_a_created_path_does_not_promise_an_undo() -> None:
    assert "undo" in REOPEN
    text = reopen_after_created("/L/prices.bean")
    assert "undo" not in text
    assert "It will ask you about prices.bean, which it was creating." in text
