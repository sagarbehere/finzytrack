"""The main sidecar entry (ARCHITECTURE.md §5, the shell interface): CLI args, loopback bind, `FT_READY` handshake.

The acceptance harness (S0030) starts this process with `--port 0` and data dirs inside its temp root, and reads
the port from the handshake line only. `--frozen-clock` is the test-only way to freeze the backend's clock.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import main
from app.platform.adapters.clock import SystemClock
from app.platform.clock import FrozenClock


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    data, cache = tmp_path / "local", tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    return data, cache


def argv(data: Path, cache: Path, *extra: str) -> list[str]:
    return ["--app-local-data", str(data), "--app-cache", str(cache), *extra]


def test_args_default_to_port_0_and_the_system_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs))
    assert args.port == 0
    assert args.workspace is None
    assert isinstance(main.wire(args).clock, SystemClock)


def test_frozen_clock_arg_wires_a_frozen_clock(dirs: tuple[Path, Path]) -> None:
    args = main.parse_args(argv(*dirs, "--frozen-clock", "2026-03-31T21:30:00-07:00"))
    clock = main.wire(args).clock
    assert isinstance(clock, FrozenClock)
    assert clock.now() == datetime(2026, 4, 1, 4, 30, tzinfo=UTC)
    assert clock.now().utcoffset() == timedelta(hours=-7)
    assert clock.now().tzinfo == timezone(timedelta(hours=-7))


@pytest.mark.parametrize(
    "extra",
    [
        ("--frozen-clock", "2026-03-31T21:30:00"),  # no offset: not an instant
        ("--frozen-clock", "yesterday"),
        ("--port", "-1"),
        ("--port", "65536"),
        ("--port", "http"),
    ],
)
def test_bad_args_are_refused(dirs: tuple[Path, Path], extra: tuple[str, str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main.parse_args(argv(*dirs, *extra))
    assert exc.value.code == 2


def test_missing_or_non_folder_roots_are_refused(tmp_path: Path, dirs: tuple[Path, Path]) -> None:
    a_file = tmp_path / "file"
    a_file.write_text("x")
    for data, cache in ((tmp_path / "missing", dirs[1]), (dirs[0], a_file)):
        with pytest.raises(SystemExit):
            main.parse_args(argv(data, cache))
    with pytest.raises(SystemExit):
        main.parse_args(argv(*dirs, "--workspace", str(tmp_path / "missing")))
    with pytest.raises(SystemExit):
        main.parse_args(["--app-cache", str(dirs[1])])  # --app-local-data is required


def test_the_handshake_line_is_ft_ready_with_port_and_version() -> None:
    line = main.handshake_line(51234, "0.4.0.dev0")
    assert line.startswith("FT_READY ")
    assert "\n" not in line
    assert json.loads(line.removeprefix("FT_READY ")) == {"port": 51234, "version": "0.4.0.dev0"}


def test_the_server_binds_loopback_only_on_an_os_assigned_port() -> None:
    server = main.bind(0)
    try:
        host, port = main.bound_address(server)
        assert host == "127.0.0.1"
        assert 0 < port <= 65535
    finally:
        server.server_close()


def test_the_process_prints_the_handshake_and_answers_on_its_port(dirs: tuple[Path, Path]) -> None:
    proc = subprocess.Popen(  # noqa: S603 (fixed argv: this interpreter running the entry under test)
        [sys.executable, "-m", "app.main", *argv(*dirs), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.startswith("FT_READY "), line
        port = json.loads(line.removeprefix("FT_READY "))["port"]
        assert port != 0
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/boot")
        # No routes until the API edge exists (L1-07, L10-09): any request is answered, with 501.
        assert conn.getresponse().status == 501
        conn.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
