"""
Shared pytest fixtures for Finzytrack backend tests.

Provides:
- Isolated temp directories per test (config/, data/, logs/)
- Real Config, BackupManager, LedgerManager instances
- FastAPI TestClient wired to a real backend with fixture data
"""

import shutil
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.core.backup_manager import BackupManager
from app.core.ledger_manager import LedgerManager
from app.core.ledger_initializer import LedgerInitializer


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """
    Create an isolated root directory mimicking a desktop installation:
      tmp_path/
        config/
          csv_rules/
          xls_rules/
          email_rules/
          recipes/
          config.yaml
        data/
          ledgers/
          backups/
        logs/
    """
    (tmp_path / "config" / "csv_rules").mkdir(parents=True)
    (tmp_path / "config" / "xls_rules").mkdir(parents=True)
    (tmp_path / "config" / "email_rules").mkdir(parents=True)
    (tmp_path / "config" / "recipes").mkdir(parents=True)
    (tmp_path / "data" / "ledgers").mkdir(parents=True)
    (tmp_path / "data" / "backups").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)

    # Write a minimal config.yaml
    config_yaml = tmp_path / "config" / "config.yaml"
    config_yaml.write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n"
        "  default_currency: USD\n"
        "  default_unknown_account: Expenses:Unknown\n"
    )

    return tmp_path


@pytest.fixture
def tmp_root_with_ledger(tmp_root: Path) -> Path:
    """tmp_root with the small_ledger fixture copied into place."""
    src = FIXTURES_DIR / "small_ledger.beancount"
    dst = tmp_root / "data" / "ledgers" / "main.beancount"
    shutil.copy2(src, dst)
    return tmp_root


@pytest.fixture
def tmp_root_with_edge_cases(tmp_root: Path) -> Path:
    """tmp_root with the edge_cases fixture copied into place."""
    src = FIXTURES_DIR / "edge_cases.beancount"
    dst = tmp_root / "data" / "ledgers" / "main.beancount"
    shutil.copy2(src, dst)
    return tmp_root


def _build_config(root: Path) -> Config:
    """Build a Config pointing at the given root directory."""
    ledger_path = str(root / "data" / "ledgers" / "main.beancount")
    config_file = root / "config" / "config.yaml"
    return Config(
        root_dir=root,
        setup_complete=True,
        ledger_file=ledger_path,
        config_file_path=config_file,
        accounts={"default_currency": "USD", "default_unknown_account": "Expenses:Unknown"},
        backup={"enabled": True, "retention_count": 5},
    )


def _build_managers(config: Config, root: Path):
    """Build the core service managers for a test environment."""
    backup_manager = BackupManager(
        backup_dir=Path(config.backup_dir),
        retention_count=config.backup.retention_count,
    )
    ledger_initializer = LedgerInitializer(
        ledger_file=config.ledger_file,
        default_currency=config.accounts.default_currency,
        backup_manager=backup_manager,
    )
    beancount_manager = LedgerManager(
        ledger_file=config.ledger_file,
        backup_manager=backup_manager,
        ledger_initializer=ledger_initializer,
    )
    return backup_manager, ledger_initializer, beancount_manager


@pytest.fixture
def config(tmp_root_with_ledger: Path) -> Config:
    """Config object pointing at the small_ledger fixture."""
    return _build_config(tmp_root_with_ledger)


@pytest.fixture
def backup_manager(config: Config) -> BackupManager:
    return BackupManager(
        backup_dir=Path(config.backup_dir),
        retention_count=config.backup.retention_count,
    )


@pytest.fixture
def beancount_manager(config: Config, tmp_root_with_ledger: Path) -> LedgerManager:
    """LedgerManager wired to the small_ledger fixture in a temp dir."""
    _, _, mgr = _build_managers(config, tmp_root_with_ledger)
    return mgr


def _create_test_client(root: Path) -> TestClient:
    """Create a TestClient backed by a real backend at the given root."""
    from app.main import create_app

    config = _build_config(root)
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def test_client(tmp_root_with_ledger: Path) -> Generator[TestClient, None, None]:
    """
    FastAPI TestClient backed by a real backend with the small_ledger fixture.
    All paths resolve via Config.root_dir — no CWD manipulation needed.
    """
    with _create_test_client(tmp_root_with_ledger) as client:
        yield client


@pytest.fixture
def edge_case_client(tmp_root_with_edge_cases: Path) -> Generator[TestClient, None, None]:
    """TestClient backed by the edge_cases fixture."""
    with _create_test_client(tmp_root_with_edge_cases) as client:
        yield client


# ── Multi-file ledger fixtures ───────────────────────────────────────────────
#
# These back the API-level automation of dev-docs/multi-file-ledger-test.md.
# The shipped seed ledger `fake-multi/` is copied into data/ledgers/ so that
# main.beancount sits at the standard config path and its relative `include`
# directives resolve in place. See backend/tests/api/test_multi_file_api.py.

_SEED_LEDGERS_DIR = Path(__file__).parent.parent / "resources" / "seed_data" / "ledgers"


def _copy_multi_file_fixture(root: Path) -> None:
    """Copy the fake-multi seed tree into ``root/data/ledgers`` (flattened so
    main.beancount is at the config's expected ledger path)."""
    dst = root / "data" / "ledgers"
    src = _SEED_LEDGERS_DIR / "fake-multi"
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _normalize_multi_file_on_disk(root: Path) -> None:
    """Do one no-op write through the real write path so the intentionally
    un-normalized ``pushtag-demo.beancount`` (block syntax) is collapsed to the
    printer's house style up front. After this, a later mutation only rewrites
    the file it actually changes — which is what the 'only file X changed'
    assertions depend on. Backups created here are cleared so per-test backup
    assertions start clean."""
    config = _build_config(root)
    _, _, mgr = _build_managers(config, root)
    entries, _, options = mgr._parse_ledger()
    mgr._write_entries(entries, options)
    backup_dir = root / "data" / "backups"
    if backup_dir.exists():
        for f in backup_dir.glob("*.backup"):
            f.unlink()


@pytest.fixture
def tmp_root_with_multi_file(tmp_root: Path) -> Path:
    """tmp_root with the multi-file fake-multi fixture copied into place
    (pushtag-demo.beancount still in its original block-syntax form)."""
    _copy_multi_file_fixture(tmp_root)
    return tmp_root


@pytest.fixture
def multi_file_client(tmp_root_with_multi_file: Path) -> Generator[TestClient, None, None]:
    """TestClient over a *pre-normalized* multi-file ledger.

    The pushtag block in pushtag-demo.beancount is collapsed up front, so a
    subsequent mutation only rewrites the file that actually changes. Use this
    for routing / 'only file X changed' / backup assertions.
    """
    _normalize_multi_file_on_disk(tmp_root_with_multi_file)
    with _create_test_client(tmp_root_with_multi_file) as client:
        yield client


@pytest.fixture
def multi_file_client_fresh(tmp_root_with_multi_file: Path) -> Generator[TestClient, None, None]:
    """TestClient over the *raw* multi-file ledger (pushtag block still intact).

    Use this for the pushtag→inline transformation and the advisory-notice
    lifecycle, which depend on the un-normalized starting state.
    """
    with _create_test_client(tmp_root_with_multi_file) as client:
        yield client
