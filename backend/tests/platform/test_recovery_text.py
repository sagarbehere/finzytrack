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
    assert "keep" in screen.actions


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


@pytest.mark.parametrize(("reason", "retry_may"), [("stopped", False), ("cancelled", True), ("failed", True)])
def test_the_keep_confirmation(reason: str, retry_may: bool) -> None:
    b = Blocking(J, reason, None, None, inspection=kept_inspection("accounts.beancount.kept-s1-j7"))  # type: ignore[arg-type]
    text = keep_confirmation(b, WS)
    assert "accounts.beancount, prices.bean" in text, "the files it releases"
    assert ("Retry may still undo it." in text) == retry_may
    assert "The kept copy /L/accounts.beancount.kept-s1-j7 stays. Delete or move it before you use the ledger." in text


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
