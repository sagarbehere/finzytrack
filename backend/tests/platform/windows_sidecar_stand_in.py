"""A stand-in sidecar for `test_engine_windows.py`'s kill-on-close test (S0129): the real engine client starts an
engine whose plugin child holds a lock and sleeps, prints `<engine pid> <child pid>`, then waits until it is ended.

    python windows_sidecar_stand_in.py ENGINE_VENV LOCK
"""

import sys
import threading
from pathlib import Path

from app.platform.adapters.engine_client import EngineClient, EngineCommand


def main() -> None:
    client = EngineClient(EngineCommand.for_venv(Path(sys.argv[1]), test_ops=True))
    out = client.call("test.child", {"lock": sys.argv[2]})
    if not isinstance(out, dict):
        sys.stderr.write(f"test.child: {out}\n")
        raise SystemExit(1)
    sys.stdout.write(f"{client.pid} {out['pid']}\n")
    sys.stdout.flush()
    threading.Event().wait()  # until the test ends this process hard


if __name__ == "__main__":
    main()
