"""The reasons a test skips on Windows until the Windows port lands (S0127, BL-0279; ADR-0069's split), each naming the
backlog item that turns it on, or why the test has no Windows meaning. The real-disk write path runs on Windows since
S0128 (BL-0280: `tests/fs/test_windows.py`), and in the product since S0150 (BL-0087). Written as the
macOS-only skip is (`tests/macos_only.py`), so pyright checks the code after it for the other platforms only:

    if sys.platform == "win32":
        raise pytest.skip.Exception(NO_FIFO, allow_module_level=True)  # a whole module, before its POSIX imports

and the same `raise` (without `allow_module_level`) inside one test. The engine-windows CI job runs pytest with `-rs`,
so each skip prints its one line there.
"""

from __future__ import annotations

from typing import Final

HELD_FOLDER: Final = (
    "Windows refuses to rename a folder whose entries are held open (probed, S0126): "
    "the replacement cannot happen there"
)
NO_MOUNT: Final = (
    "a volume mounted inside the mirror folder needs hdiutil or sudo mount; Windows' mount folders: untested"
)
NO_FORK: Final = (
    "a plugin's `os.fork` and `signal.pause`: Windows has neither; the cases of a process that holds the answer's "
    "pipe run there as the S0146 tests of tests/ledger/test_fresh_load.py"
)
NO_FIFO: Final = "Windows has no FIFO in its file system: the non-blocking read it guards against cannot arise there"
NO_UID: Final = "Windows has no POSIX user and group ids or permission bits on files"
FLOCK_HOLDER: Final = "a POSIX `flock` holder; the Windows engine lock's own tests are tests/fs/test_windows.py"
NO_RLIMIT: Final = "Windows has no descriptor limit to lower (`resource.RLIMIT_NOFILE`)"

POSIX_PATH_MODULES: Final = frozenset(
    {"test_journal_record.py", "test_copy_layout.py", "test_v_journal_folder_born.py"}
)
"""Pure tests written in POSIX paths: on Windows they run with the POSIX path model, as the tests over the fake do
(`tests/conftest.py`); the Windows model has its own (`tests/fs/test_pathmodel.py`, `tests/ledger/
test_journal_windows.py`, the Windows case of `test_copy_layout.py`, which simulates it inside)."""
