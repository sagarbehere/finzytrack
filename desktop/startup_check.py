"""Check that a built Finzytrack app starts and is healthy.

    python startup_check.py <path-to-app-binary> [extra launcher args...]

Runs the app headless on a free port and waits for the server to answer. On a
fresh install (setup not complete: CI, a new machine) it completes setup the
way the first-run wizard does, with the bundled demo ledger, which also proves
the packaged seed data loads. On an install whose setup is done it changes
nothing. Either way it then requires /health to answer 200.

Exit 0 on pass; 1 if the app exits before answering or /health is not 200.
The app's output is printed either way. CI runs this on every packaged build,
since a build that compiles can still fail to start.

There is no timeout here: the launcher itself exits when the backend dies and
gives up after its own startup limit, so "the app exited" covers a hang too.
"""
import json
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


def request(base: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """GET (or POST JSON) and return (status, body); HTTP error statuses are returned, not raised."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{base}{path}', data=data,
                                 headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode(errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace')


def check(base: str, proc: subprocess.Popen) -> str | None:
    """Return None on pass, else the failure reason."""
    # Wait for the server to answer at all (any status): the launcher's own notion of ready.
    while True:
        if proc.poll() is not None:
            return f'the app exited with code {proc.returncode} before the server answered'
        try:
            request(base, '/health')
            break
        except OSError:
            time.sleep(0.5)

    status, body = request(base, '/api/config')
    if status != 200:
        return f'/api/config answered {status}: {body}'
    if not json.loads(body)['data'].get('setup_complete'):
        print('==> Fresh install: completing setup with the demo ledger', flush=True)
        status, body = request(base, '/api/setup/complete', {'currency': 'USD', 'ledger_mode': 'demo'})
        if status != 200:
            return f'setup failed, /api/setup/complete answered {status}: {body}'

    status, body = request(base, '/health')
    if status != 200:
        return f'/health answered {status}: {body}'
    print(f'==> /health answered 200: {body}', flush=True)
    return None


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
        try:
            failure = check(f'http://127.0.0.1:{port}', proc)
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

    if failure:
        print(f'FAIL: {failure}')
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
