"""Run a packaged build's `--smoke-test` and judge the result (spec: `shell/src-tauri/src/smoke.rs`).

    python3 shell/smoke_packaged.py PATH [--expect-fail]

PATH is the shell executable, or a macOS `.app` (its `Contents/MacOS/` executable is run). Passes only when
the process exits 0 and its stdout holds exactly one smoke line, `FT_SMOKE_OK {json}`. With `--expect-fail`
(the must-not-pass run) it passes only when the process exits non-zero with exactly one `FT_SMOKE_FAIL` line,
so a crash or a hang never counts as the expected refusal. Linux CI wraps this in `xvfb-run`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 180  # above the shell's own 120 s limit, so the shell's verdict normally wins
OK, FAIL = "FT_SMOKE_OK ", "FT_SMOKE_FAIL "


def executable(path: Path) -> Path:
    if path.suffix == ".app" and path.is_dir():
        found = [p for p in (path / "Contents/MacOS").iterdir() if p.is_file()]
        if len(found) != 1:
            raise SystemExit(f"smoke: expected one executable in {path}/Contents/MacOS, found {len(found)}")
        return found[0]
    return path


def verdict(returncode: int | None, stdout: str, expect_fail: bool) -> tuple[bool, str]:
    """(passed, reason). `returncode` is None after a timeout."""
    smoke = [line for line in stdout.splitlines() if line.startswith(("FT_SMOKE_OK", "FT_SMOKE_FAIL"))]
    if returncode is None:
        return False, f"no exit within {TIMEOUT_S}s"
    if len(smoke) != 1:
        return False, f"expected one smoke line, got {len(smoke)} (exit {returncode})"
    line = smoke[0]
    if expect_fail:
        if returncode != 0 and line.startswith(FAIL):
            return True, f"refused as expected: {line}"
        return False, f"expected a refusal, got exit {returncode}: {line}"
    if returncode == 0 and line.startswith(OK):
        return True, line
    return False, f"exit {returncode}: {line}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect-fail", action="store_true")
    args = parser.parse_args(argv)
    exe = executable(args.path)
    try:
        proc = subprocess.run([str(exe), "--smoke-test"], capture_output=True, text=True, timeout=TIMEOUT_S)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = None, e.stdout or "", e.stderr or ""
        out, err = (s.decode(errors="replace") if isinstance(s, bytes) else s for s in (out, err))
    passed, reason = verdict(code, out, args.expect_fail)
    print(f"smoke {'PASS' if passed else 'FAIL'}: {exe}: {reason}")
    if not passed and err.strip():
        print(err.strip()[-4000:], file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
