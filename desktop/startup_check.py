"""Check that a built Finzytrack app starts: run it headless, wait for /health.

    python startup_check.py <path-to-app-binary> [extra launcher args...]

Passes (exit 0) when /health answers 200; fails (exit 1) if the app exits first
or /health answers anything else, printing the app's output either way. CI runs
this on every packaged build, since a build that compiles can still fail to
start (a module dropped by PyInstaller, a launcher change, a platform quirk).

There is no timeout here: the launcher itself exits when the backend dies and
gives up after its own startup limit, so "the app exited" covers a hang too.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    binary, extra = sys.argv[1], sys.argv[2:]
    port = free_port()
    cmd = [binary, '--headless', '--port', str(port), *extra]
    print(f'==> Starting: {" ".join(cmd)}', flush=True)

    with tempfile.TemporaryFile(mode='w+') as out:
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
        status = None
        try:
            while proc.poll() is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as resp:
                        status = resp.status
                        body = resp.read().decode(errors='replace')
                    break
                except urllib.error.HTTPError as e:
                    status, body = e.code, e.read().decode(errors='replace')
                    break
                except OSError:
                    time.sleep(0.5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
            out.seek(0)
            print('----- app output -----')
            print(out.read().rstrip())
            print('----------------------', flush=True)

    if status is None:
        print(f'FAIL: the app exited with code {proc.returncode} before /health answered.')
        return 1
    if status != 200:
        print(f'FAIL: /health answered {status}: {body}')
        return 1
    print(f'PASS: /health answered 200: {body}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
