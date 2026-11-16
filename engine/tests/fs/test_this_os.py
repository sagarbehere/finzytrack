"""The adapter selection (`ledger_engine.fs.this_os`; S0113, S0125): macOS gets the macOS adapter, Linux the Linux
adapter; any other operating system refuses file operations with `UnsupportedPlatform`, while the engine still imports,
answers its handshake and its ping, and never imports either adapter (BL-0087 builds Windows's)."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import textwrap

import pytest

from ledger_engine.fs import this_os


@pytest.mark.parametrize("platform", ["win32", "freebsd14"])
def test_other_operating_systems_are_refused(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    monkeypatch.setattr(this_os.sys, "platform", platform)
    with pytest.raises(this_os.UnsupportedPlatform) as port:
        this_os.for_this_os()
    with pytest.raises(this_os.UnsupportedPlatform) as locks:
        this_os.legacy_locks_for_this_os()
    for refusal in (port.value, locks.value):
        assert isinstance(refusal, OSError), "a file operation refuses like any other I/O failure"
        assert refusal.errno == errno.ENOTSUP


def test_on_macos_the_adapters_are_the_macos_ones() -> None:
    if sys.platform != "darwin":
        pytest.skip("the macOS adapter exists only on macOS")
    from ledger_engine.fs.darwin import DarwinFS, DarwinLegacyLocks

    assert isinstance(this_os.for_this_os(), DarwinFS)
    assert isinstance(this_os.legacy_locks_for_this_os(), DarwinLegacyLocks)


def test_on_linux_the_adapters_are_the_linux_ones() -> None:
    if sys.platform != "linux":
        pytest.skip("the Linux adapter exists only on Linux")
    from ledger_engine.fs.linux import LinuxFS, LinuxLegacyLocks

    assert isinstance(this_os.for_this_os(), LinuxFS)
    assert isinstance(this_os.legacy_locks_for_this_os(), LinuxLegacyLocks)
    assert this_os.supported()


# A fresh interpreter whose engine modules believe they run on another OS. The standard library and the dependencies
# are loaded first with the real platform (some pick their own OS modules at import: `shutil` imports `_winapi` on
# win32), then every engine module is dropped and imported again under the other platform: the engine imports,
# answers the handshake and the ping, refuses a file port, and never loads the macOS adapter, which refuses to be
# imported there.
OFF_MACOS = textwrap.dedent(
    """
    import json, sys
    import ledger_engine.protocol.server  # noqa: F401  (loads the standard library and the dependencies for real)
    for name in [m for m in sys.modules if m == "ledger_engine" or m.startswith("ledger_engine.")]:
        del sys.modules[name]
    sys.platform = sys.argv[1]
    from ledger_engine.fs import this_os
    from ledger_engine.protocol.server import Engine

    engine = Engine()
    replies = [
        json.loads(engine.handle(json.dumps({"v": 2, "id": i, "op": op, "params": {}}).encode() + b"\\n"))
        for i, op in ((1, "handshake"), (2, "ping"))
    ]
    try:
        this_os.for_this_os()
        port = "no refusal"
    except this_os.UnsupportedPlatform:
        port = "refused"
    loaded = "ledger_engine.fs.darwin" in sys.modules or "ledger_engine.fs.linux" in sys.modules
    imported = "refused"
    for adapter in ("darwin", "linux"):
        try:
            __import__(f"ledger_engine.fs.{adapter}")
            imported = "imported"
        except ImportError:
            pass
    print(json.dumps({"replies": replies, "port": port, "loaded": loaded, "imported": imported}))
    """
)


@pytest.mark.parametrize("platform", ["win32", "freebsd14"])
def test_without_a_port_the_engine_answers_its_handshake_and_ping_and_never_loads_an_adapter(platform: str) -> None:
    run = subprocess.run(  # noqa: S603 (our own interpreter and code, no shell)
        [sys.executable, "-c", OFF_MACOS, platform], capture_output=True, text=True, check=False, timeout=120
    )
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.splitlines()[-1])
    assert [r["ok"] for r in got["replies"]] == [True, True], got["replies"]
    assert got["replies"][1]["result"] == {"status": "ok"}
    assert got["port"] == "refused"
    assert got["loaded"] is False, "no adapter is imported off its own OS"
    assert got["imported"] == "refused", "each adapter states its platform and refuses to be imported elsewhere"


def test_BL0261_an_op_that_needs_the_port_is_refused_naming_the_platform_not_a_missing_app_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BL-0261 (S0112, the Linux CI run of 2026-11-13): on an operating system without a port, the boot's recover-all
    said the app folder was missing ("quit and reopen"), which is false there. An op that needs the engine lock (it
    reads or writes ledger or journal files) is refused `engine.unsupported_platform`, naming the platform."""
    from ledger_engine.protocol.server import Engine

    engine = Engine()
    assert json.loads(engine.handle(b'{"v": 2, "id": 1, "op": "handshake", "params": {}}\n'))["ok"] is True
    engine.took_engine_lock("/nowhere/locks/engine.lock", (1, 2))
    monkeypatch.setattr(this_os.sys, "platform", "win32")
    request = {"v": 2, "id": 2, "op": "recover_list", "params": {"app_local_data": "/nowhere"}}
    reply = json.loads(engine.handle(json.dumps(request).encode() + b"\n"))
    assert reply["ok"] is False, reply
    assert reply["error"]["key"] == "engine.unsupported_platform", reply
    assert "win32" in reply["error"]["message"]
