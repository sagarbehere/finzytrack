"""The copy's "loads the same" load in a fresh process (`ledger_engine.ledger.fresh_load`, `copy_check`;
`design/specs/engine-jobs.md` §8.2 step 5; ADR-0063 decision 1, "every wait has an exit"; BL-0203, S0082).

A real child on Beancount's load, each ledger in a scratch folder with a plugin beside it (`insert_pythonpath`):

- the child always ends once it has answered, whatever a plugin left behind (a thread that never ends, a process
  it forked that still holds the answer's pipe), and a child that never answers is ended as the drain ends it (§5.2),
  never a hang. No test here has a time limit: a hang test waits on an event nothing sets, then asserts the answer;
- what a plugin prints never reaches the answer (the child's standard output goes to standard error);
- the parent reads exactly one answer: an empty answer, a non-zero exit or garbage on standard output is `failed`.

On Windows too (S0146, BL-0087: the reader's Windows form). A plugin there cannot fork; the cases of a process that
holds the answer's pipe are run on every OS with the child's own code put before the real child (`holding`): it
starts a process that inherits its standard output and never ends, as a process a plugin started would.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ledger_engine.ledger import fresh_load
from ledger_engine.ledger.copy_check import CheckRequest, CheckResult
from ledger_engine.ledger.core import copy_layout as cl
from tests.beancount_text import escaped
from tests.windows_pending import NO_FORK

LEDGER = "2026-01-01 open Assets:Cash EUR\n2026-01-02 open Income:Pay EUR\n"
HEAD = "__plugins__ = ['run']\n"


def ledger(tmp_path: Path, plugin: str, config: str | None = None) -> CheckRequest:
    """A ledger in `tmp_path/books` that runs `plugin` (the source of a module beside it)."""
    folder = tmp_path.resolve() / "books"
    folder.mkdir()
    (folder / "pl_s0082.py").write_text(plugin)
    named = f'plugin "pl_s0082" "{escaped(config)}"\n' if config is not None else 'plugin "pl_s0082"\n'
    (folder / "main.beancount").write_text('option "insert_pythonpath" "True"\n' + named + LEDGER)
    return CheckRequest(str(folder / "main.beancount"), str(folder), cl.Layout(str(folder), ()), ())


def checked(result: CheckResult) -> None:
    assert result.outcome == "checked", result
    assert result.summary is not None
    assert len(result.summary.entries) == 2, result.summary
    assert result.summary.errors == ()


def test_a_plain_ledger_is_checked(tmp_path: Path) -> None:
    """@regression-guard: the channel's happy path, for the cases below to differ from."""
    checked(fresh_load.check(ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")))


def test_a_plugin_thread_that_never_ends_does_not_hold_the_answer(tmp_path: Path) -> None:
    """S0081 verifier F1: a non-daemon thread waiting on an event nothing sets kept the child alive after it had
    answered, and the copy job waited until Cancel. On the base this test never returns."""
    plugin = (
        "import threading\n" + HEAD + "def run(entries, options):\n"
        "    threading.Thread(target=threading.Event().wait, name='never').start()\n"
        "    return entries, []\n"
    )
    checked(fresh_load.check(ledger(tmp_path, plugin)))


def test_what_a_plugin_prints_never_reaches_the_answer(tmp_path: Path) -> None:
    """@regression-guard (passes on the base, which already isolated the output; it is the test the base lacked):
    S0081 verifier F2: the mutant `os.dup2(2, 1)` → `pass` survived. A plugin prints a forged answer with
    Python's `print` and writes garbage straight to descriptor 1; the answer is still the load's."""
    forged = '{"outcome": "checked", "summary": null, "read": [], "subject": null}'
    plugin = (
        "import os\n" + HEAD + "def run(entries, options):\n"
        f"    print({forged!r})\n"
        "    os.write(1, b'garbage\\n')\n"
        "    return entries, []\n"
    )
    result = fresh_load.check(ledger(tmp_path, plugin))
    checked(result)
    assert any(p.endswith("main.beancount") for p in result.read)


def test_a_plugin_that_exits_is_a_failed_check(tmp_path: Path) -> None:
    """A plugin's `SystemExit` escapes Beancount's plugin guard: the child answers `failed`, naming it, and ends (on
    the base it ended without an answer, and with a thread left it would not end)."""
    plugin = (
        "import sys, threading\n" + HEAD + "def run(entries, options):\n"
        "    threading.Thread(target=threading.Event().wait, name='never').start()\n"
        "    sys.exit(0)\n"
    )
    assert fresh_load.check(ledger(tmp_path, plugin)) == CheckResult("failed", subject="SystemExit")


def test_a_process_a_plugin_forked_that_holds_the_pipe_does_not_hold_the_answer(tmp_path: Path) -> None:
    """A plugin forks a process that lives on with the answer's pipe open; once the child has answered and ended,
    the job has its answer (the process is ended by the drain with the engine's group, §5.2; here by the test)."""
    if sys.platform == "win32":
        pytest.skip(NO_FORK)
    pid_file = tmp_path / "forked.pid"
    plugin = (
        "import os, signal\n" + HEAD + "def run(entries, options, config):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        try:\n"
        "            while True:\n"
        "                signal.pause()\n"
        "        finally:\n"
        "            os._exit(0)\n"
        "    with open(config, 'w') as f:\n"
        "        f.write(str(pid))\n"
        "    return entries, []\n"
    )
    try:
        checked(fresh_load.check(ledger(tmp_path, plugin, str(pid_file))))
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_a_child_that_dies_before_answering_while_a_forked_process_holds_the_pipe_is_a_failed_check(
    tmp_path: Path,
) -> None:
    """S0082 verifier V1: a plugin forks a process that keeps the answer's pipe, then the child dies before it answers
    (here it kills itself, as a crash in a C extension would end it). The check answers as soon as the child has
    ended, with its exit status; it never waits for the forked process. On the base and at 5e8d9888 this test never
    returns."""
    if sys.platform == "win32":
        pytest.skip(NO_FORK)
    pid_file = tmp_path / "forked.pid"
    plugin = (
        "import os, signal\n" + HEAD + "def run(entries, options, config):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        try:\n"
        "            while True:\n"
        "                signal.pause()\n"
        "        finally:\n"
        "            os._exit(0)\n"
        "    with open(config, 'w') as f:\n"
        "        f.write(str(pid))\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    try:
        result = fresh_load.check(ledger(tmp_path, plugin, str(pid_file)))
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
    assert result == CheckResult("failed", subject=f"exit {-signal.SIGKILL}")


def test_a_child_that_never_answers_is_ended_by_the_drain_and_the_check_fails(tmp_path: Path) -> None:
    """@regression-guard (passes on the base: `subprocess.run` also returned once the killed child closed the pipe;
    BL-0203 asked for the case to be tested). A plugin that blocks forever: the check waits (no time limit, §5.4),
    and the drain that ends the engine's group ends the child too (§5.2); the check then answers `failed` with the
    exit status, never a hang. The plugin tells its process id through a named pipe (POSIX: a FIFO; Windows: a pipe of
    `multiprocessing.connection`), so the test kills it only once it is blocked (Windows: `TerminateProcess`, as the
    Job Object's drain ends its members)."""
    results: list[CheckResult] = []
    if sys.platform == "win32":
        from multiprocessing.connection import Listener

        with Listener() as listener:
            plugin = (
                "import os, threading\nfrom multiprocessing.connection import Client\n"
                + HEAD
                + "def run(entries, options, config):\n"
                "    Client(config).send(os.getpid())\n"
                "    threading.Event().wait()\n"
            )
            request = ledger(tmp_path, plugin, str(listener.address))
            t = threading.Thread(target=lambda: results.append(fresh_load.check(request)))
            t.start()
            with listener.accept() as conn:
                pid = int(conn.recv())
        os.kill(pid, signal.SIGTERM)  # TerminateProcess(15)
        t.join()
        assert results == [CheckResult("failed", subject=f"exit {int(signal.SIGTERM)}")]
        return
    fifo = tmp_path / "pid.fifo"
    os.mkfifo(fifo)
    plugin = (
        "import os, threading\n" + HEAD + "def run(entries, options, config):\n"
        "    with open(config, 'w') as f:\n"
        "        f.write(str(os.getpid()))\n"
        "    threading.Event().wait()\n"
    )
    request = ledger(tmp_path, plugin, str(fifo))
    t = threading.Thread(target=lambda: results.append(fresh_load.check(request)))
    t.start()
    with fifo.open() as f:  # opens once the plugin opens it to write
        pid = int(f.read())
    os.kill(pid, signal.SIGKILL)
    t.join()
    assert results == [CheckResult("failed", subject=f"exit {-signal.SIGKILL}")]


@contextmanager
def holding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, then: str) -> Generator[None]:
    """The child's code: first a process started with the child's standard output and nothing else of its own to end
    on (its standard input the null device), which never ends; then `then`. The holder is ended once the check has
    answered (by the drain in the product, §5.2)."""
    pid_file = tmp_path / "holder.pid"
    head = (
        "import os, subprocess, sys\n"
        "h = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'],"
        " stdin=subprocess.DEVNULL)\n"
        f"with open({str(pid_file)!r}, 'w') as f:\n"
        "    f.write(str(h.pid))\n"
    )
    monkeypatch.setattr(fresh_load, "BOOT", head + then)
    try:
        yield
    finally:
        if pid_file.exists():
            os.kill(int(pid_file.read_text()), signal.SIGTERM)


def test_S0146_a_process_the_child_started_that_holds_the_pipe_does_not_hold_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard on POSIX (the fork test above is its plugin form there): the real child answers after it
    started a process that keeps the answer's pipe open and never ends; the check answers once the child has
    answered and ended. On Windows at the base (b90455c8) this module could not run (`selectors` takes no pipe)."""
    request = ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")
    with holding(tmp_path, monkeypatch, fresh_load.BOOT):
        checked(fresh_load.check(request))


def test_S0146_what_the_child_wrote_before_it_ended_is_its_answer_while_a_process_holds_the_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard on POSIX: the child writes its answer without a newline and ends, while a process it started
    holds the pipe: the parent reads what the child wrote (the answer it parses), never waiting for the pipe's end."""
    request = ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")
    partial = json.dumps(CheckResult("failed", subject="partial").encode())
    then = f"sys.stdin.buffer.read()\nos.write(1, {partial.encode()!r})\nos._exit(0)\n"
    with holding(tmp_path, monkeypatch, then):
        assert fresh_load.check(request) == CheckResult("failed", subject="partial")


def test_S0146_a_child_that_dies_before_answering_while_a_process_holds_the_pipe_is_a_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard on POSIX (the fork test above is its plugin form there): the child writes part of a line and
    is killed (Windows: `TerminateProcess`) while a process it started holds the pipe: the check answers at once with
    the exit status."""
    request = ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")
    then = (
        "import signal\nsys.stdin.buffer.read()\nos.write(1, b'{\"outcome\": ')\nos.kill(os.getpid(), signal.SIGTERM)\n"
    )
    killed = int(signal.SIGTERM) if sys.platform == "win32" else -signal.SIGTERM
    with holding(tmp_path, monkeypatch, then):
        assert fresh_load.check(request) == CheckResult("failed", subject=f"exit {killed}")


@pytest.mark.parametrize(
    ("boot", "subject"),
    [
        ("import sys\nsys.stdin.buffer.read()\n", "answer"),  # exit 0, nothing written
        ("import sys\nsys.stdin.buffer.read()\nsys.exit(3)\n", "exit 3"),
        ("import sys\nsys.stdin.buffer.read()\nsys.stdout.write('garbage\\n')\n", "answer"),
        ("import sys\nsys.stdin.buffer.read()\nsys.stdout.write('{}\\n')\n", "answer"),
        ("import sys\nsys.stdin.buffer.read()\nsys.stdout.write('[1]\\n')\n", "answer"),
        ('import sys\nsys.stdout.write(\'{"outcome": "checked"\')\n', "answer"),  # cut short, input unread
    ],
)
def test_an_answer_the_parent_cannot_read_is_a_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boot: str, subject: str
) -> None:
    """@regression-guard (BL-0203 F2: untested on the base): the child's code replaced, so the parent's parsing of
    one answer is tested on its own."""
    monkeypatch.setattr(fresh_load, "BOOT", boot)
    request = ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")
    assert fresh_load.check(request) == CheckResult("failed", subject=subject)


def test_a_child_that_ends_before_reading_a_long_request_is_a_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """@regression-guard: a request longer than a pipe holds, and a child that ends without reading it; the parent
    gets the broken pipe and answers with the exit status (`subprocess.run` hid it on the base)."""
    monkeypatch.setattr(fresh_load, "BOOT", "import sys\nsys.exit(4)\n")
    request = ledger(tmp_path, HEAD + "def run(entries, options):\n    return entries, []\n")
    long = CheckRequest(request.root, request.target, request.layout, tuple((f"n{i}" * 50, "o") for i in range(2000)))
    assert fresh_load.check(long) == CheckResult("failed", subject="exit 4")
