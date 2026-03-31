"""
FinzyTrack Desktop Launcher
Starts the FastAPI backend and opens a PyWebView window.
"""
import os
import sys
import time
import threading
import argparse
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Path resolution — works both from source and inside a PyInstaller bundle
# ---------------------------------------------------------------------------

if getattr(sys, 'frozen', False):
    # Running inside PyInstaller bundle.
    # Python bytecode is accessible via import machinery from sys._MEIPASS.
    # User data (config, ledger, etc.) lives next to the executable.
    BUNDLE_DIR = sys._MEIPASS
    FRONTEND_DIST = os.path.join(BUNDLE_DIR, 'frontend_dist')
    SEED_CONFIG_DIR = os.path.join(BUNDLE_DIR, 'backend', 'seed_config')
    SEED_DATA_DIR = os.path.join(BUNDLE_DIR, 'backend', 'seed_data')
    # Working directory = folder containing the FinzyTrack executable.
    # All relative paths in config.yaml resolve from here.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(APP_DIR)
    # On first run, copy seed templates so that relative paths in
    # config.yaml resolve correctly.
    local_config_dir = os.path.join(APP_DIR, 'config')
    if not os.path.exists(local_config_dir):
        import shutil
        shutil.copytree(SEED_CONFIG_DIR, local_config_dir)
        print('[launcher] Seeded config directory from template.', flush=True)
    local_data_dir = os.path.join(APP_DIR, 'data')
    if not os.path.exists(local_data_dir):
        import shutil
        shutil.copytree(SEED_DATA_DIR, local_data_dir)
        print('[launcher] Seeded data directory from template.', flush=True)
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
        with open(config_path, 'r') as f:
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
        description='FinzyTrack — personal finance app',
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
    return parser.parse_args()


def find_config_path() -> str:
    """Return the config file path to use.

    Both packaged and development mode use config/config.yaml as the
    single user-editable config.  In dev mode, the backend's _seed_config()
    will create it from resources/seed_config/ if it doesn't exist yet.
    """
    path = './config/config.yaml'
    if os.path.exists(path):
        return path
    if getattr(sys, 'frozen', False):
        # Should not happen after first-run copy, but fall back to bundle
        return os.path.join(SEED_CONFIG_DIR, 'config.yaml')
    raise FileNotFoundError(
        'No config.yaml found. Expected ./config/config.yaml '
        '(run the backend once to seed from resources/seed_config/)'
    )


def start_backend(args):
    """Start uvicorn in a background thread."""
    def p(msg):
        print(f'[backend] {msg}', flush=True)

    p('importing Config...')
    from app.config import Config, ConfigurationError
    p('importing create_app...')
    from app.main import create_app, setup_logging
    p('importing uvicorn...')
    import uvicorn

    p('finding config path...')
    config_path = args.config or find_config_path()
    p(f'loading config from {config_path}...')

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

    try:
        config = Config.from_yaml_file(config_path, cli_overrides or None)
    except ConfigurationError as e:
        print(f'[backend] Config error: {e}', file=sys.stderr, flush=True)
        return

    p('setting up logging...')
    setup_logging(config.logging.level)

    p('creating FastAPI app...')
    static_dir = FRONTEND_DIST if os.path.exists(FRONTEND_DIST) else None
    app = create_app(config, static_dir=static_dir)

    p(f'starting uvicorn on {config.server.host}:{config.server.port}...')
    try:
        uvicorn.run(app, host=config.server.host, port=config.server.port, log_level='warning')
    except Exception as e:
        print(f'[backend] uvicorn crashed: {e}', flush=True)
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

    # Start backend in daemon thread (dies automatically when main thread exits)
    thread = threading.Thread(target=start_backend, args=(args,), daemon=True)
    thread.start()

    print(f'[launcher] Waiting for backend on {url}...', flush=True)
    if not wait_for_backend(url):
        print(f'[launcher] Backend failed to start within 180 seconds.', file=sys.stderr)
        sys.exit(1)

    print(f'[launcher] Backend ready at {url}')

    window = webview.create_window(
        'FinzyTrack',
        url,
        width=1380,
        height=860,
        min_size=(900, 600),
    )

    if not args.debug:
        def on_loaded():
            window.evaluate_js("document.addEventListener('contextmenu', e => e.preventDefault())")
        window.events.loaded += on_loaded

    webview.start(debug=args.debug)


if __name__ == '__main__':
    main()
