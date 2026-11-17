"""A process that holds a whole-file `flock` until its stdin closes: an engine of an earlier session, as the engine
lock sees it (`design/specs/engine-jobs.md` §5.1). Prints `held` once it holds the lock.

    python lock_holder.py PATH
"""

import os
import sys

WINDOWS = "flock is POSIX; the Windows lock is BL-0280"
if sys.platform == "win32":
    raise SystemExit(WINDOWS)

import fcntl  # noqa: E402 (POSIX only: after the platform statement)


def main() -> None:
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    sys.stdout.write("held\n")
    sys.stdout.flush()
    sys.stdin.read()  # until the test closes our stdin or ends us


if __name__ == "__main__":
    main()
