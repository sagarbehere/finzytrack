"""The adapter selection (`ledger_engine.fs.this_os`; S0113): macOS gets the macOS adapter; any other operating system
refuses file operations with `UnsupportedPlatform`, while the engine still imports, answers its handshake and its
ping, and never imports the macOS adapter (BL-0087 builds the others)."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import textwrap

import pytest

from ledger_engine.fs import this_os


@pytest.mark.parametrize("platform", ["linux", "win32"])
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
    loaded = "ledger_engine.fs.darwin" in sys.modules
    try:
        import ledger_engine.fs.darwin  # noqa: F401
        imported = "imported"
    except ImportError:
        imported = "refused"
    print(json.dumps({"replies": replies, "port": port, "loaded": loaded, "imported": imported}))
    """
)


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_off_macos_the_engine_answers_its_handshake_and_ping_and_never_loads_the_macos_adapter(platform: str) -> None:
    run = subprocess.run(  # noqa: S603 (our own interpreter and code, no shell)
        [sys.executable, "-c", OFF_MACOS, platform], capture_output=True, text=True, check=False, timeout=120
    )
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.splitlines()[-1])
    assert [r["ok"] for r in got["replies"]] == [True, True], got["replies"]
    assert got["replies"][1]["result"] == {"status": "ok"}
    assert got["port"] == "refused"
    assert got["loaded"] is False, "the macOS adapter is never imported off macOS"
    assert got["imported"] == "refused", "the macOS adapter states its platform and refuses to be imported elsewhere"
