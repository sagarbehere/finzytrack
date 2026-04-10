"""
Fixtures for multi-user isolation tests.

Provides a hosted-mode FastAPI TestClient with two pre-provisioned users
(``alice`` and ``bob``), each with their own config + ledger.
"""

import shutil
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.app_mode import AppMode
from app.config import Config
from app.main import create_app


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _provision_user(base: Path, user_id: str) -> Path:
    """Create a fully provisioned user directory with config + small ledger."""
    user_root = base / "users" / user_id
    (user_root / "config" / "csv_rules").mkdir(parents=True)
    (user_root / "config" / "xls_rules").mkdir(parents=True)
    (user_root / "config" / "email_rules").mkdir(parents=True)
    (user_root / "config" / "recipes").mkdir(parents=True)
    (user_root / "data" / "ledgers").mkdir(parents=True)
    (user_root / "data" / "backups").mkdir(parents=True)
    (user_root / "logs").mkdir(parents=True)

    # Write a minimal config.yaml
    config_yaml = user_root / "config" / "config.yaml"
    config_yaml.write_text(
        "setup_complete: true\n"
        "ledger_file: ./data/ledgers/main.beancount\n"
        "accounts:\n"
        "  default_currency: USD\n"
        "  default_unknown_account: Expenses:Unknown\n"
    )

    # Copy the small_ledger fixture
    src = FIXTURES_DIR / "small_ledger.beancount"
    dst = user_root / "data" / "ledgers" / "main.beancount"
    shutil.copy2(src, dst)

    return user_root


@pytest.fixture
def hosted_tmp_root(tmp_path: Path) -> Path:
    """Root directory with two pre-provisioned users for isolation tests."""
    _provision_user(tmp_path, "alice")
    _provision_user(tmp_path, "bob")

    # Server-level directories (hosted mode still needs a root with valid paths
    # even though per-user services use their own directories)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "ledgers").mkdir(parents=True)
    (tmp_path / "data" / "backups").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
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
def hosted_client(hosted_tmp_root: Path) -> Generator[TestClient, None, None]:
    """TestClient running in hosted mode with alice + bob provisioned."""
    config = Config(
        root_dir=hosted_tmp_root,
        setup_complete=True,
        ledger_file=str(hosted_tmp_root / "data" / "ledgers" / "main.beancount"),
        config_file_path=hosted_tmp_root / "config" / "config.yaml",
        accounts={"default_currency": "USD", "default_unknown_account": "Expenses:Unknown"},
        backup={"enabled": True, "retention_count": 5},
    )
    app = create_app(config, mode=AppMode.HOSTED)
    with TestClient(app) as client:
        yield client


def alice(hosted_client: TestClient):
    """Helper — return a dict of headers for user alice."""
    return {"X-User-ID": "alice"}


def bob(hosted_client: TestClient):
    """Helper — return a dict of headers for user bob."""
    return {"X-User-ID": "bob"}
