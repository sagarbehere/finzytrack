#!/usr/bin/env python3
"""The strict type check once per target platform (S0113): `<package>/.venv/bin/pyright --pythonplatform <P>` for each
P in PLATFORMS, in parallel, with the package's own pinned `pyrightconfig.json` (only the platform is passed). Pyright
otherwise checks for the platform it runs on, so a macOS-only name (`fcntl.F_FULLFSYNC`) passed the type check on a Mac
and failed it on the Linux CI runner; a module that states its platform once at its top
(`if sys.platform != "darwin": raise ImportError(...)`) is checked in full for that platform and skipped for the others.

PLATFORMS has no Windows yet: the product's POSIX-only process and lock code (process groups, `flock`, signals) has no
Windows port to check until BL-0087; its pass is BL-0260.

Passes only when every pass ran and reported no error; each pass's output is printed under its platform, and a failure
names it. Used by the T0 pre-push hook and the manual CI run (`ci.yml`), for `backend/` and `engine/`.

Usage: python3 scripts/pyright_platforms.py <package dir>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

PLATFORMS: Final = ("Darwin", "Linux")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    package = Path(argv[0])
    pyright = package / ".venv" / "bin" / "pyright"
    if not pyright.is_file():
        print(f"pyright_platforms: {pyright} missing; run `uv sync --locked` in {package}", file=sys.stderr)
        return 1
    runs = {
        p: subprocess.Popen(  # noqa: S603 (the package's own pyright, no shell)
            [str(pyright.absolute()), "--pythonplatform", p],
            cwd=package,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for p in PLATFORMS
    }
    failed: list[str] = []
    for p, run in runs.items():
        out, _ = run.communicate()
        print(f"--- pyright --pythonplatform {p} ---\n{out.rstrip()}")
        if run.returncode != 0:
            failed.append(p)
    for p in failed:
        print(f"pyright_platforms: pyright --pythonplatform {p}: FAILED", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
