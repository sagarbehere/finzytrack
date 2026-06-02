"""
Finzytrack Desktop Launcher
Starts the FastAPI backend and opens a PyWebView window.
"""
import os
import sys
import time
import threading
import argparse
import urllib.request
import urllib.error

# Windows defaults stdout/stderr to cp1252, which crashes the moment any
# log line contains a non-Latin-1 character (e.g. "→" in our progress
# output). Reconfigure to UTF-8 before any other module imports so every
# downstream print() / logging handler is safe.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# On Windows, force pythonnet to use the .NET Framework runtime via
# mscoree.dll, which ships with every Windows install. clr_loader's
# auto-detection picks .NET Core/.NET 5+ when a SDK is on PATH (as on
# the GitHub windows-latest runner), but the assemblies pythonnet
# bundles can't be initialised under that runtime in our PyInstaller
# layout — the app crashes at startup with "Failed to resolve
# Python.Runtime.Loader.Initialize". Forcing netfx makes the runtime
# selection deterministic across dev machines and CI, and removes the
# need for end users to install any .NET runtime.
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONNET_RUNTIME', 'netfx')

# Strip the Mark-of-the-Web from every bundled file. Windows tags each
# file extracted from a downloaded zip with a Zone.Identifier alternate
# data stream marking it untrusted, and the .NET Framework loader then
# refuses to load pythonnet's managed Python.Runtime.dll — surfacing as
# "Failed to resolve Python.Runtime.Loader.Initialize" at startup. The
# bytes on disk are fine; only the trust metadata is wrong. Unblock-File
# clears the ADS in place. Best-effort: any failure here is non-fatal so
# the app can still attempt to start (and surface its own error if the
# real problem is something else).
if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    import subprocess
    _bundle_root = os.path.dirname(sys.executable)
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f"Get-ChildItem -Recurse -LiteralPath '{_bundle_root}' | Unblock-File"],
            check=False, capture_output=True, timeout=30,
        )
    except Exception as _e:
        print(f'[launcher] Unblock-File step failed (non-fatal): {_e}',
              file=sys.stderr, flush=True)

# On Linux, the bundle deliberately doesn't ship GTK / WebKit / etc. —
# those come from the user's system (see the allowlist in
# finzytrack.spec). Probe for libwebkit2gtk-4.1 before importing
# pywebview so users hit a clear "install this package" message
# instead of a cryptic stack trace from inside webview.platforms.gtk.
if sys.platform.startswith('linux') and getattr(sys, 'frozen', False):
    import ctypes
    try:
        ctypes.CDLL('libwebkit2gtk-4.1.so.0')
    except OSError:
        print(
            '\nFinzytrack requires libwebkit2gtk-4.1 (and libfuse2) on the\n'
            'system to render its UI. Install it with one of:\n'
            '  Debian 13 / Ubuntu 24.04+:  sudo apt install libwebkit2gtk-4.1-0 libfuse2t64\n'
            '  Debian 12 / Ubuntu 22.04:   sudo apt install libwebkit2gtk-4.1-0 libfuse2\n'
            '  Fedora 36+:                 sudo dnf install webkit2gtk4.1 fuse-libs\n'
            '  Arch / Manjaro:             sudo pacman -S webkit2gtk-4.1 fuse2\n'
            '  openSUSE Tumbleweed:        sudo zypper install libwebkit2gtk-4_1-0 libfuse2\n',
            file=sys.stderr, flush=True,
        )
        sys.exit(1)

# ---------------------------------------------------------------------------
# Path resolution — works both from source and inside a PyInstaller bundle
# ---------------------------------------------------------------------------

if getattr(sys, 'frozen', False):
    # Running inside PyInstaller bundle.
    # Python bytecode is accessible via import machinery from sys._MEIPASS.
    BUNDLE_DIR = sys._MEIPASS
    FRONTEND_DIST = os.path.join(BUNDLE_DIR, 'frontend_dist')
    SEED_CONFIG_DIR = os.path.join(BUNDLE_DIR, 'backend', 'seed_config')
    SEED_DATA_DIR = os.path.join(BUNDLE_DIR, 'backend', 'seed_data')
    # User data (config, ledger, etc.) in the platform-standard location:
    #   macOS:   ~/Library/Application Support/Finzytrack
    #   Windows: %LOCALAPPDATA%/Finzytrack
    #   Linux:   ~/.local/share/Finzytrack (XDG_DATA_HOME)
    if sys.platform == 'darwin':
        APP_DIR = os.path.join(os.path.expanduser('~'), 'Library',
                               'Application Support', 'Finzytrack')
    elif sys.platform == 'win32':
        APP_DIR = os.path.join(os.environ.get('LOCALAPPDATA',
                               os.path.expanduser('~')), 'Finzytrack')
    else:
        APP_DIR = os.path.join(os.environ.get('XDG_DATA_HOME',
                               os.path.join(os.path.expanduser('~'), '.local', 'share')),
                               'Finzytrack')
    os.makedirs(APP_DIR, exist_ok=True)
    os.chdir(APP_DIR)
    # Config and data directories are seeded by main.py on first run.
else:
    # Running from source (development / POC testing).
    ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    BACKEND_DIR = os.path.join(ROOT_DIR, 'backend')
    FRONTEND_DIST = os.path.join(ROOT_DIR, 'frontend', 'dist')
    os.chdir(BACKEND_DIR)

sys.path.insert(0, BACKEND_DIR if not getattr(sys, 'frozen', False) else BUNDLE_DIR)

# ---------------------------------------------------------------------------
# Imports (after sys.path is set)
# ---------------------------------------------------------------------------

import webview  # noqa: E402 — must come after path setup

# ---------------------------------------------------------------------------
# CLI argument parsing — config file values used as defaults
# ---------------------------------------------------------------------------

_FALLBACK_DEFAULTS = {
    'host': '127.0.0.1',
    'port': 8001,
    'ledger_file': None,
    'log_level': 'INFO',
}


def peek_config_path_from_argv() -> str | None:
    """Check sys.argv for --config/-c before full parsing."""
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg in ('--config', '-c') and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith('--config='):
            return arg.split('=', 1)[1]
    return None


def load_config_defaults() -> dict:
    """Lightweight YAML read of the config file to extract default values.
    Uses plain yaml.safe_load to avoid importing the full Pydantic stack here.
    Respects --config/-c if present in sys.argv."""
    try:
        import yaml
        config_path = peek_config_path_from_argv() or find_config_path()
        with open(config_path, 'r', encoding='utf-8') as f:
            raw = yaml.safe_load(f) or {}
        return {
            'host':         raw.get('server', {}).get('host', _FALLBACK_DEFAULTS['host']),
            'port':         raw.get('server', {}).get('port', _FALLBACK_DEFAULTS['port']),
            'ledger_file':  raw.get('ledger_file', _FALLBACK_DEFAULTS['ledger_file']),
            'log_level':    raw.get('logging', {}).get('level', _FALLBACK_DEFAULTS['log_level']),
        }
    except Exception:
        return dict(_FALLBACK_DEFAULTS)


def parse_args():
    defaults = load_config_defaults()
    parser = argparse.ArgumentParser(
        description='Finzytrack — personal finance app',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--config', '-c', default=None, metavar='PATH',
        help='Path to configuration file (default: config/config.yaml)'
    )
    parser.add_argument(
        '--host', type=str, default=defaults['host'],
        help='Backend server host address'
    )
    parser.add_argument(
        '--port', type=int, default=defaults['port'],
        help='Backend server port'
    )
    parser.add_argument(
        '--ledger-file', default=defaults['ledger_file'], metavar='PATH',
        help='Path to Beancount ledger file'
    )
    parser.add_argument(
        '--log-level', default=defaults['log_level'],
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Logging level'
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='Enable debug mode (sets log level to DEBUG)'
    )
    parser.add_argument(
        '--headless', action='store_true',
        help='Run without a GUI window (server only, access via browser)'
    )
    return parser.parse_args()


def find_config_path() -> str:
    """Return the config file path if it exists.

    Used only by load_config_defaults() to read CLI defaults from the
    config file before argparse runs.  On first run the file won't exist
    yet (start_server seeds it), so callers must handle FileNotFoundError.
    """
    path = './config/config.yaml'
    if os.path.exists(path):
        return path
    raise FileNotFoundError(
        'No config.yaml found. Expected ./config/config.yaml '
        '(start_server will seed it on first run)'
    )


def start_backend(args, shutdown_event):
    """Start uvicorn in a background thread via the shared start_server() path."""
    from app.main import start_server

    config_path = args.config or './config/config.yaml'

    # Build CLI overrides from launcher args — only pass values explicitly
    # provided so config file values aren't accidentally overwritten by defaults.
    cli_overrides = {}
    if args.host != _FALLBACK_DEFAULTS['host']:
        cli_overrides['server-host'] = args.host
    if args.port != _FALLBACK_DEFAULTS['port']:
        cli_overrides['server-port'] = args.port
    if args.ledger_file:
        cli_overrides['ledger-file'] = args.ledger_file
    log_level = 'DEBUG' if args.debug else args.log_level
    if log_level != _FALLBACK_DEFAULTS['log_level']:
        cli_overrides['logging-level'] = log_level

    static_dir = FRONTEND_DIST if os.path.exists(FRONTEND_DIST) else None

    try:
        start_server(
            config_path=config_path,
            cli_overrides=cli_overrides or None,
            static_dir=static_dir,
            shutdown_event=shutdown_event,
        )
    except Exception as e:
        print(f'[backend] crashed: {e}', file=sys.stderr, flush=True)
        raise


def wait_for_backend(url: str, timeout: int = 180) -> bool:
    """Poll /health until the backend responds (any HTTP response = ready)."""
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f'{url}/health', timeout=2)
            return True
        except urllib.error.HTTPError:
            # Server is up but health check returned non-200 (e.g. 503 unhealthy)
            return True
        except Exception:
            time.sleep(0.5)
            dots += 1
            if dots % 6 == 0:
                elapsed = int(time.time() - (deadline - timeout))
                print(f'[launcher] Still waiting... ({elapsed}s)', flush=True)
    return False


def main():
    args = parse_args()
    # Host and port for the window URL come from args (which already reflect config defaults)
    url = f'http://{args.host}:{args.port}'

    # Event to signal a graceful backend shutdown when the window closes.
    shutdown_event = threading.Event()

    thread = threading.Thread(target=start_backend, args=(args, shutdown_event))
    thread.start()

    print(f'[launcher] Waiting for backend on {url}...', flush=True)
    if not wait_for_backend(url):
        print(f'[launcher] Backend failed to start within 180 seconds.', file=sys.stderr)
        sys.exit(1)

    print(f'[launcher] Backend ready at {url}')

    if args.headless:
        # Headless mode — no GUI window, just run the server until Ctrl+C.
        print(f'[launcher] Running headless. Open {url} in a browser.', flush=True)
        if args.host == '0.0.0.0':
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('8.8.8.8', 80))
                local_ip = s.getsockname()[0]
                s.close()
                print(f'[launcher] Local network: http://{local_ip}:{args.port}', flush=True)
            except Exception:
                pass
        try:
            thread.join()
        except KeyboardInterrupt:
            print('\n[launcher] Interrupted, shutting down...', flush=True)
            shutdown_event.set()
            thread.join(timeout=5)
            print('[launcher] Done.', flush=True)
        return

    window = webview.create_window(
        'Finzytrack',
        url,
        width=1380,
        height=860,
        min_size=(900, 600),
        text_select=True,
    )

    # Open maximized — works around small-screen overflow on Linux laptops
    # and is the right default for a finance app where users want as much
    # tabular real estate as possible.
    window.events.shown += lambda: window.maximize()

    # Browser-like zoom shortcuts (Ctrl/Cmd +/-/0) — pywebview has no built-in
    # zoom UI, so we drive document.body.style.zoom from JS and persist the
    # level in localStorage so it survives restarts.
    zoom_js = """
    (function () {
      const KEY = 'finzytrack:zoom';
      const apply = (z) => { document.body.style.zoom = z; };
      let zoom = parseFloat(localStorage.getItem(KEY)) || 1.0;
      apply(zoom);
      const set = (z) => {
        zoom = Math.min(3.0, Math.max(0.5, Math.round(z * 100) / 100));
        localStorage.setItem(KEY, zoom);
        apply(zoom);
      };
      window.addEventListener('keydown', (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        if (e.key === '=' || e.key === '+') { set(zoom + 0.1); e.preventDefault(); }
        else if (e.key === '-')             { set(zoom - 0.1); e.preventDefault(); }
        else if (e.key === '0')             { set(1.0);        e.preventDefault(); }
      });
    })();
    """

    def on_loaded():
        window.evaluate_js(zoom_js)
        if not args.debug:
            window.evaluate_js("document.addEventListener('contextmenu', e => e.preventDefault())")
    window.events.loaded += on_loaded

    # On Linux, pywebview tries Qt first and falls back to GTK with a noisy
    # traceback. Force GTK so the log stays clean. macOS/Windows have their
    # own native backends and don't need an override.
    start_kwargs = {
        'debug': args.debug,
        'private_mode': False,
        'storage_path': os.path.join(APP_DIR, 'browser_storage'),
    }
    if sys.platform.startswith('linux'):
        start_kwargs['gui'] = 'gtk'

    # Blocks until the window is closed.
    webview.start(**start_kwargs)

    # Window closed — tell the backend to shut down gracefully so FastAPI
    # lifespan cleanup runs (cancels pending SQLite syncs, flushes logs).
    print('[launcher] Window closed, shutting down backend...', flush=True)
    shutdown_event.set()
    thread.join(timeout=5)
    print('[launcher] Done.', flush=True)

    # pywebview's GTK backend leaves a non-daemon thread alive after the
    # window closes, which keeps the process attached to the terminal even
    # though everything has shut down cleanly. Force exit so the shell
    # prompt comes back.
    if sys.platform.startswith('linux'):
        os._exit(0)


if __name__ == '__main__':
    main()
