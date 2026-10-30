"""The ACL allowlist (`ledger.handles.blocking_entry`; owner, 2026-10-30: "Allowlist (Recommended)"; ADR-0013
amendment of that date; S0061 r6), tested from the rule as written, not from the code's answers: a folder the save or
the undo writes in is refused when it carries a deny entry that applies to us, unless new entries do not inherit it
(no `file_inherit`, `directory_inherit` or `only_inherit`) and every bit it denies protects only the folder itself
(`delete`, `writeextattr`, `writeattr`, `writesecurity`, `chown`). Unknown bits fail closed."""

from __future__ import annotations

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


def deny(*perms: str, flags: frozenset[str] = frozenset(), ours: bool = True) -> AclEntry:
    return AclEntry(deny=True, ours=ours, perms=frozenset(perms), flags=flags)


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
    allow = AclEntry(deny=False, ours=True, perms=frozenset({"delete_child"}), flags=frozenset({"file_inherit"}))
    d = deny("delete_child", flags=frozenset({"file_inherit"}))
    assert blocking_entry((allow, d)) == d


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
def test_allow_entries_never_refuse(flags: frozenset[str]) -> None:
    allow = AclEntry(deny=False, ours=True, perms=frozenset(SELF_BITS + OTHER_BITS), flags=flags)
    assert blocking_entry((allow,)) is None


@pytest.mark.parametrize("flags", NOT_INHERITING + INHERITING)
def test_another_users_deny_entry_does_not_apply_to_us_and_is_allowed(flags: frozenset[str]) -> None:
    assert blocking_entry((deny(*SELF_BITS, *OTHER_BITS, flags=flags, ours=False),)) is None


def test_macos_own_entries_on_the_home_folder_desktop_documents_and_downloads_pass() -> None:
    """`group:everyone deny delete` and `group:everyone deny writeextattr` (checked on this Mac, 2026-10-30), as two
    entries or as the one entry `chmod` merges them into."""
    assert blocking_entry((deny("delete"), deny("writeextattr"))) is None
    assert blocking_entry((deny("delete", "writeextattr"),)) is None


def test_the_first_blocking_entry_is_named_and_an_empty_acl_passes() -> None:
    first, second = deny("search"), deny("delete_child")
    assert blocking_entry((deny("delete"), first, second)) == first
    assert blocking_entry(()) is None
