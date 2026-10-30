"""The ACL allowlist (`ledger.handles.blocking_entry`; owner, 2026-10-30: "Allowlist (Recommended)", then "Any
subject (Recommended)"; ADR-0013 amendments of that date; S0061 r6, r7), tested from the rule as written, not from the
code's answers: a folder the save or the undo writes in is refused when it carries **any** deny entry, whoever it
names, unless new entries do not inherit it (no `file_inherit`, `directory_inherit` or `only_inherit`) and every bit
it denies protects only the folder itself (`delete`, `writeextattr`, `writeattr`, `writesecurity`, `chown`). Unknown
bits and flags fail closed. The rule never sees a subject: an `AclEntry` carries none."""

from __future__ import annotations

import dataclasses

import pytest

from ledger_engine.fs.port import AclEntry
from ledger_engine.ledger.handles import blocking_entry

SELF_BITS = ("delete", "writeextattr", "writeattr", "writesecurity", "chown")
OTHER_BITS = (
    "list",
    "add_file",
    "search",
    "add_subdirectory",
    "delete_child",
    "readattr",
    "readextattr",
    "readsecurity",
    "synchronize",
    "unknown",
    "read",  # a file's words for the same bits, should an adapter ever use them: not in the list, so refused
    "write",
    "append",
    "execute",
)
INHERITING = (
    frozenset({"file_inherit"}),
    frozenset({"directory_inherit"}),
    frozenset({"file_inherit", "directory_inherit"}),
    frozenset({"file_inherit", "only_inherit"}),
    frozenset({"directory_inherit", "only_inherit"}),
    frozenset({"only_inherit"}),
    frozenset({"file_inherit", "inherited"}),
    frozenset({"unknown"}),
)
NOT_INHERITING: tuple[frozenset[str], ...] = (frozenset(), frozenset({"inherited"}), frozenset({"limit_inherit"}))


def deny(*perms: str, flags: frozenset[str] = frozenset()) -> AclEntry:
    return AclEntry(deny=True, perms=frozenset(perms), flags=flags)


@pytest.mark.parametrize("flags", NOT_INHERITING)
@pytest.mark.parametrize("bit", SELF_BITS)
def test_a_deny_of_a_folder_self_bit_that_new_entries_do_not_inherit_is_allowed(
    bit: str, flags: frozenset[str]
) -> None:
    assert blocking_entry((deny(bit, flags=flags),)) is None


def test_every_folder_self_bit_at_once_not_inherited_is_allowed() -> None:
    assert blocking_entry((deny(*SELF_BITS),)) is None


@pytest.mark.parametrize("flags", INHERITING)
@pytest.mark.parametrize("bit", SELF_BITS)
def test_the_same_folder_self_bits_are_refused_when_new_entries_inherit_them(bit: str, flags: frozenset[str]) -> None:
    e = deny(bit, flags=flags)
    assert blocking_entry((e,)) == e


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
@pytest.mark.parametrize("bit", OTHER_BITS)
def test_every_other_deny_bit_is_refused_direct_and_inherited(bit: str, flags: frozenset[str]) -> None:
    e = deny(bit, flags=flags)
    assert blocking_entry((e,)) == e
    mixed = deny("delete", bit, flags=flags)  # beside a folder-self bit in the same entry
    assert blocking_entry((mixed,)) == mixed


def test_an_allow_entry_before_a_deny_still_refuses() -> None:
    """The red team saw APFS honour the earlier allow; the rule does not weigh allows, so it fails closed."""
    allow = AclEntry(deny=False, perms=frozenset({"delete_child"}), flags=frozenset({"file_inherit"}))
    d = deny("delete_child", flags=frozenset({"file_inherit"}))
    assert blocking_entry((allow, d)) == d


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
def test_allow_entries_never_refuse(flags: frozenset[str]) -> None:
    allow = AclEntry(deny=False, perms=frozenset(SELF_BITS + OTHER_BITS), flags=flags)
    assert blocking_entry((allow,)) is None


def test_an_entry_carries_no_subject_so_whom_it_names_cannot_change_the_answer() -> None:
    """Owner, 2026-10-30: "Any subject (Recommended)": a deny for another user, a group we are not in, macOS's `owner`
    or `group` subject or an unresolvable one is judged like one for us (S0061 r7: a membership lookup misjudged
    `owner` and `group`). The entry the rule sees has only its kind, bits and flags."""
    assert [f.name for f in dataclasses.fields(AclEntry)] == ["deny", "perms", "flags"]


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
def test_every_deny_of_every_bit_at_once_is_refused(flags: frozenset[str]) -> None:
    """What round 6 allowed for another user's entry (every bit, every flag set) is now refused. (At 71c84897 it
    passes only for an entry that applies to us; its other half is the subject test above, which fails there.)"""
    e = deny(*SELF_BITS, *OTHER_BITS, flags=flags)
    assert blocking_entry((e,)) == e


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
def test_a_deny_of_no_bit_is_judged_by_its_flags_alone(flags: frozenset[str]) -> None:
    """@regression-guard (the rule's bits and flags did not change). An empty permission set is a subset of the
    folder-self bits: allowed unless new entries inherit it (verifier r7: the rule's one row with no case)."""
    e = deny(flags=flags)
    assert blocking_entry((e,)) == (None if flags in NOT_INHERITING else e)


def test_macos_own_entries_on_the_home_folder_desktop_documents_and_downloads_pass() -> None:
    """`group:everyone deny delete` and `group:everyone deny writeextattr` (checked on this Mac, 2026-10-30), as two
    entries or as the one entry `chmod` merges them into."""
    assert blocking_entry((deny("delete"), deny("writeextattr"))) is None
    assert blocking_entry((deny("delete", "writeextattr"),)) is None


def test_the_first_blocking_entry_is_named_and_an_empty_acl_passes() -> None:
    first, second = deny("search"), deny("delete_child")
    assert blocking_entry((deny("delete"), first, second)) == first
    assert blocking_entry(()) is None
