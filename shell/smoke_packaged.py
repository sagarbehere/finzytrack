"""Run a packaged build's `--smoke-test` and judge the result (spec: `shell/src-tauri/src/smoke.rs`).

    python3 shell/smoke_packaged.py PATH [--expect-fail REASON]

PATH is the shell executable, or a macOS `.app` (its `Contents/MacOS/` executable is run). Passes only when
the process exits 0 and its stdout holds exactly one smoke line, `FT_SMOKE_OK {json}`. With
`--expect-fail REASON` (the must-not-pass run) it passes only when the process exits non-zero with exactly one
`FT_SMOKE_FAIL` line whose reason contains REASON, so a crash, a hang or a refusal for another cause never
counts as the expected one. Linux CI wraps this in `xvfb-run`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
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


def verdict(returncode: int | None, stdout: str, expect_fail: str | None) -> tuple[bool, str]:
    """(passed, reason). `returncode` is None after a timeout."""
    smoke = [line for line in stdout.splitlines() if line.startswith(("FT_SMOKE_OK", "FT_SMOKE_FAIL"))]
    if returncode is None:
        return False, f"no exit within {TIMEOUT_S}s"
    if len(smoke) != 1:
        return False, f"expected one smoke line, got {len(smoke)} (exit {returncode})"
    line = smoke[0]
    if expect_fail is not None:
        if returncode != 0 and line.startswith(FAIL) and expect_fail in line[len(FAIL):]:
            return True, f"refused as expected: {line}"
        return False, f"expected a refusal, got exit {returncode}: {line}"
    if returncode == 0 and line.startswith(OK):
        return True, line
    return False, f"exit {returncode}: {line}"


def run(exe: Path) -> tuple[int | None, str, str]:
    """(exit code or None on timeout, stdout, stderr). Output goes to files, not pipes: on Windows the webview's
    helper processes inherit the app's handles and outlive it, so a pipe would never reach end-of-file (S0012).
    The wait is on the app process alone; on timeout its whole process tree is stopped."""
    with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        proc = subprocess.Popen([str(exe), "--smoke-test"], stdout=fout, stderr=ferr)
        try:
            code: int | None = proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            code = None
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
            proc.kill()
            proc.wait()
        fout.seek(0)
        ferr.seek(0)
        return code, fout.read().decode(errors="replace"), ferr.read().decode(errors="replace")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect-fail", metavar="REASON", help="text the expected FT_SMOKE_FAIL reason contains")
    args = parser.parse_args(argv)
    exe = executable(args.path)
    code, out, err = run(exe)
    passed, reason = verdict(code, out, args.expect_fail)
    print(f"smoke {'PASS' if passed else 'FAIL'}: {exe}: {reason}")
    if not passed and err.strip():
        print(err.strip()[-4000:], file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
