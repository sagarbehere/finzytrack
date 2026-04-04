"""
Finzytrack Backend Main Entry Point

CLI interface with configuration management and logging setup.
"""
import os
import sys
import logging
from pathlib import Path
from typing import Any, Optional
from contextlib import asynccontextmanager

import shutil

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    Config, ConfigurationError,
    SQLITE_EXPORT_PATH, BACKUP_DIR, LOG_FILE, LOG_FORMAT, CORS_ORIGINS,
    SQLITE_AUTO_SYNC, SQLITE_SYNC_DEBOUNCE_SECONDS, SQLITE_ENABLE_WAL,
)
from .api.routers.importer import ofx_accounts, transaction, csv_rules, xls_rules, email as email_import_router, llm_parse, rule_templates
from .api.routers import accounts, commodities, ledger_export, ledger_transactions, query, config as config_router, filesystem, ledger, assistant, recipes, ai_services, setup as setup_router
from .core.beancount_manager import BeancountManager
from .error_handler import setup_error_handlers
from .core.backup_manager import BackupManager
from .core.config_manager import ConfigManager
from .core.csv_rules_manager import CsvRulesManager
from .core.xls_rules_manager import XlsRulesManager
from .core.ledger_initializer import LedgerInitializer
from .services.sqlite_exporter import SQLiteExporter
from .services.db_sync_manager import DBSyncManager
from .email_import.rule_registry import AccountProfileRegistry


def setup_logging(level: str, max_file_size_mb: int = 5, backup_count: int = 3) -> None:
    """Configure application logging with automatic directory creation and rotation."""
    from logging.handlers import RotatingFileHandler

    # Create logs directory if it doesn't exist
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure logging level
    # FIXME: Will fail if level is invalid string. But Pydantic should catch this earlier when validating the config in config.py
    log_level = getattr(logging, level.upper())
    if not isinstance(log_level, int):
        raise ValueError(f"Invalid logging level: {level}")

    # Create logger
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=max_file_size_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)



# Seed config location differs between dev and packaged app.
_SEED_CONFIG_DIR_DEV = Path(__file__).parents[1] / "resources" / "seed_config"
_SEED_CONFIG_DIR_FROZEN = Path(getattr(sys, '_MEIPASS', '')) / "backend" / "seed_config"
_SEED_CONFIG_DIR = _SEED_CONFIG_DIR_FROZEN if getattr(sys, 'frozen', False) else _SEED_CONFIG_DIR_DEV


def _seed_config(config_dir: Path) -> None:
    """Copy seed config template to config/ on first run.

    Copies the bundled seed_config/ (default config.yaml, empty rule
    directories, default dashboard recipes) into the working config
    directory.  Skipped if config/ already exists — user data is never
    overwritten.
    """
    if config_dir.exists():
        return  # User already has a config directory — don't overwrite
    if not _SEED_CONFIG_DIR.is_dir():
        return  # No bundled seed config to copy (shouldn't happen)
    shutil.copytree(_SEED_CONFIG_DIR, config_dir)
    logging.getLogger(__name__).info(f"Seeded config directory → {config_dir}")


def create_app(config: Config, static_dir: Optional[str] = None) -> FastAPI:
    """Create FastAPI application with configuration."""
    # Get logger for this module
    logger = logging.getLogger(__name__)

    # --- Service Instantiation ---

    # 1. Create BackupManager service
    backup_manager = BackupManager(
        backup_dir=Path(BACKUP_DIR),
        retention_count=config.backup.retention_count
    )

    # 2. Create ConfigManager service
    config_manager = ConfigManager(
        config=config,
        backup_manager=backup_manager
    )

    # 2b. Create CsvRulesManager and XlsRulesManager
    csv_rules_manager = CsvRulesManager(rules_dir=config.csv_rules_dir)
    xls_rules_manager = XlsRulesManager(rules_dir=config.xls_rules_dir)

    # 2c. Create email import AccountProfileRegistry
    email_rules_path = Path(config.email_rules_dir)
    email_registry = AccountProfileRegistry(email_rules_path)
    if config.email_import.enabled:
        logger.info(f"Email import enabled: {email_registry.profile_count} profiles loaded from {email_rules_path}")
    else:
        logger.info("Email import disabled (email_import.enabled=false)")

    # 3. Create LedgerInitializer
    ledger_initializer = LedgerInitializer(
        ledger_file=config.ledger_file,
        default_currency=config.accounts.default_currency,
        backup_manager=backup_manager
    )

    # 4. Create BeancountManager
    beancount_manager = BeancountManager(
        ledger_file=config.ledger_file,
        backup_manager=backup_manager,
        ledger_initializer=ledger_initializer
    )

    # 5. Create SQLite exporter and sync manager
    sqlite_exporter = SQLiteExporter(
        sqlite_path=SQLITE_EXPORT_PATH,
        enable_wal=SQLITE_ENABLE_WAL,
    )

    sqlite_sync_manager = DBSyncManager(
        exporter=sqlite_exporter,
        ledger_file=config.ledger_file,
        delay=SQLITE_SYNC_DEBOUNCE_SECONDS,
        db_type="sqlite",
    )

    # 5b. Give ConfigManager references for hot ledger switching
    config_manager.set_ledger_services(beancount_manager, sqlite_sync_manager)

    # 6. Ensure ledger exists - fail fast if it can't be created
    #    Skip when setup is incomplete — the setup wizard will create the ledger.
    if config.setup_complete:
        try:
            if not ledger_initializer.ensure_ledger_exists():
                raise RuntimeError(f"Failed to create ledger file at {config.ledger_file}")
            logger.info(f"Ledger verified/created: {config.ledger_file}")
        except Exception as e:
            logger.error(f"Fatal: Cannot initialize ledger file {config.ledger_file}: {e}")
            raise RuntimeError(f"Failed to initialize ledger file {config.ledger_file}: {e}")
    else:
        logger.info("Setup not complete — skipping ledger initialization")

    # 7. Register SQLite sync callback
    if SQLITE_AUTO_SYNC:
        beancount_manager.register_cache_invalidation_callback(
            sqlite_sync_manager.on_ledger_changed
        )
        logger.info("SQLite auto-sync enabled with debouncing")

    # 8. Define lifespan event handler
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Application lifespan event handler for startup and shutdown tasks.

        This replaces the deprecated @app.on_event decorators.
        """
        # STARTUP: Tasks to run when the application starts
        logger.info("Application starting up...")

        # Export SQLite on startup if out of date (skip when setup is incomplete)
        if config.setup_complete and sqlite_sync_manager._needs_export():
            try:
                entries = beancount_manager.cache.get_entries()
                await sqlite_exporter.export_entries(entries)
                logger.info("SQLite exported on startup")
            except Exception as e:
                logger.error(f"Failed to export SQLite on startup: {e}")

        logger.info("Application startup complete")

        # YIELD: Application is running, handle requests
        yield

        # SHUTDOWN: Tasks to run when the application shuts down
        logger.info("Application shutting down...")

        try:
            await sqlite_sync_manager.cancel_pending_sync()
            logger.info("Cancelled pending SQLite sync")
        except Exception as e:
            logger.error(f"Error cancelling sync: {e}")

        logger.info("Application shutdown complete")

    # 11. Create FastAPI app with lifespan handler
    app = FastAPI(
        title="Finzytrack Backend",
        description="Privacy-focused personal finance application backend",
        version="1.0.0",
        lifespan=lifespan
    )

    # 12. Add CORS middleware for frontend communication
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 13. Register centralized exception handlers
    setup_error_handlers(app)

    # 14. Store managers in app state for access in routes
    app.state.config_manager = config_manager
    app.state.config_manager = config_manager
    app.state.beancount_manager = beancount_manager
    app.state.backup_manager = backup_manager
    app.state.sqlite_exporter = sqlite_exporter
    app.state.sqlite_sync_manager = sqlite_sync_manager
    app.state.csv_rules_manager = csv_rules_manager
    app.state.xls_rules_manager = xls_rules_manager
    app.state.email_registry = email_registry

    # Include API routers
    app.include_router(ofx_accounts.router, prefix="/api/import", tags=["import"])
    app.include_router(transaction.router, prefix="/api/import", tags=["import"])
    app.include_router(csv_rules.router, prefix="/api/import", tags=["import"])
    app.include_router(xls_rules.router, prefix="/api/import", tags=["import"])
    app.include_router(email_import_router.router, prefix="/api/import", tags=["import"])
    app.include_router(llm_parse.router, prefix="/api/import", tags=["import"])
    app.include_router(rule_templates.router, prefix="/api/import", tags=["import"])
    app.include_router(accounts.router, prefix="/api", tags=["accounts"])
    app.include_router(commodities.router, prefix="/api", tags=["commodities"])
    app.include_router(ledger_export.router, prefix="/api/ledger", tags=["ledger"])
    app.include_router(ledger_transactions.router, prefix="/api/ledger", tags=["ledger"])
    app.include_router(query.router, prefix="/api/ledger", tags=["ledger"])
    # Settings and file editor routers
    app.include_router(config_router.router, prefix="/api", tags=["config"])
    app.include_router(filesystem.router, prefix="/api", tags=["filesystem"])
    app.include_router(ledger.router, prefix="/api", tags=["ledger"])
    app.include_router(assistant.router, prefix="/api", tags=["assistant"])
    app.include_router(ai_services.router, prefix="/api", tags=["ai"])
    app.include_router(recipes.router, prefix="/api", tags=["recipes"])
    app.include_router(setup_router.router, prefix="/api", tags=["setup"])

    
    # Serve bundled Vue frontend (used in packaged/desktop mode)
    static_path = Path(static_dir) if static_dir else None
    if static_path and static_path.exists():
        app.mount("/assets", StaticFiles(directory=str(static_path / "assets")), name="assets")
        # Serve other top-level static files (favicon, recipes JSON, etc.)
        for item in static_path.iterdir():
            if item.is_dir() and item.name != "assets":
                app.mount(f"/{item.name}", StaticFiles(directory=str(item)), name=item.name)

        @app.get("/")
        async def serve_index():
            return FileResponse(str(static_path / "index.html"))

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Let API routes through (they're registered before this catch-all)
            return FileResponse(str(static_path / "index.html"))
    else:
        @app.get("/")
        async def root():
            return {"message": "Finzytrack Backend", "version": "1.0.0"}
    
    @app.get("/health")
    async def health():
        """Perform health checks on critical application components."""
        checks = {
            "config_loaded": True,
            "ledger_readable": False,
            "backup_dir_writable": False,
        }

        # Check ledger readability
        try:
            ledger_path = Path(config.ledger_file)
            if ledger_path.exists() and os.access(ledger_path, os.R_OK):
                checks["ledger_readable"] = True
        except Exception:
            checks["ledger_readable"] = False

        # Check backup directory writability
        try:
            backup_path = Path(BACKUP_DIR)
            if backup_path.exists() and os.access(backup_path, os.W_OK):
                checks["backup_dir_writable"] = True
        except Exception:
            checks["backup_dir_writable"] = False

        # Training data sample count — informational only, does not affect health status.
        # Insufficient training data is handled gracefully at categorization time.
        training_samples = 0
        try:
            from app.core.beancount_manager import BeancountManager
            bm: BeancountManager = app.state.beancount_manager
            training_samples = len(bm.cache.get_training_data())
        except Exception:
            pass
        checks["training_samples"] = training_samples

        # Email import registry status — informational only
        try:
            registry = app.state.email_registry
            checks["email_import"] = {
                "enabled": config.email_import.enabled,
                "profiles_loaded": registry.profile_count,
                "invalid_profiles": len(registry.list_invalid_profiles()),
            }
        except Exception:
            checks["email_import"] = {"enabled": False}

        # Determine overall status (training_samples is informational, not a hard check)
        critical_checks = {k: v for k, v in checks.items() if k != "training_samples"}
        is_healthy = all(critical_checks.values())

        if not is_healthy:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "details": checks}
            )

        return {"status": "healthy", "details": checks}
    
    @app.get("/debug/config")
    async def debug_config():
        """Debug endpoint to inspect current configuration values."""
        # Use Pydantic's model_dump to automatically serialize the entire config
        config_dict = config.model_dump(mode='json')

        # Add runtime information that's not in the config model
        config_dict["_runtime"] = {
            "active_database": "sqlite",
            "ofx_account_mappings_count": len(config_manager.get_ofx_mappings())
        }

        return config_dict
    
    return app



def start_server(
    config_path: str = './config/config.yaml',
    cli_overrides: Optional[dict[str, Any]] = None,
    static_dir: Optional[str] = None,
    shutdown_event: Optional[Any] = None,
) -> None:
    """Seed config, load settings, create app, and run uvicorn.

    This is the single startup path used by both the CLI (main()) and the
    desktop launcher.  All directory seeding and initialisation happens here
    so that every deployment context (dev, Docker, packaged app) behaves
    identically.

    Args:
        shutdown_event: Optional threading.Event.  When set, uvicorn will
            perform a graceful shutdown (running FastAPI lifespan cleanup).
            Used by the desktop launcher to stop the backend when the
            window closes.
    """
    # Set up logging with defaults first so seed/config errors are captured.
    # Reconfigured below with user settings once config is loaded.
    setup_logging(level='INFO')

    # Seed config directory on first run (before loading config).
    # Data directory is seeded later by the setup wizard so the user's
    # chosen currency can be applied to the starter ledger.
    _seed_config(Path('./config'))

    # Load environment variables from config/.env (secrets like IMAP credentials)
    from dotenv import load_dotenv
    load_dotenv(Path('./config/.env'))

    # Load configuration with CLI overrides using Pydantic
    app_config = Config.from_yaml_file(config_path, cli_overrides)

    # Reconfigure logging with user settings
    setup_logging(
        level=app_config.logging.level,
        max_file_size_mb=app_config.logging.max_file_size_mb,
        backup_count=app_config.logging.backup_count,
    )

    logger = logging.getLogger(__name__)
    logger.info("Starting Finzytrack backend server")
    logger.info(f"Configuration loaded from: {config_path}")
    logger.info(f"Server will start on {app_config.server.host}:{app_config.server.port}")

    if cli_overrides:
        logger.info(f"CLI overrides applied: {list(cli_overrides.keys())}")

    # Create FastAPI app
    app = create_app(app_config, static_dir=static_dir)

    # Ensure uvicorn loggers use the same level and handlers
    uvi_level = app_config.logging.level.upper()
    logging.getLogger("uvicorn").setLevel(uvi_level)
    logging.getLogger("uvicorn.error").setLevel(uvi_level)
    logging.getLogger("uvicorn.access").setLevel(uvi_level)

    # Start server
    uvi_config = uvicorn.Config(
        app,
        host=app_config.server.host,
        port=app_config.server.port,
        log_config=None,
    )
    server = uvicorn.Server(uvi_config)

    if shutdown_event is not None:
        # Poll the event so the desktop launcher can trigger a graceful stop.
        import threading

        def _watch_shutdown():
            shutdown_event.wait()
            logger.info("Shutdown event received — stopping server gracefully")
            server.should_exit = True

        threading.Thread(target=_watch_shutdown, daemon=True).start()

    server.run()


@click.command()
@click.option('--config', '-c',
              default='./config/config.yaml',
              help='Path to configuration file',
              show_default=True)
@click.option('--server-host',
              help='Server host address (overrides config)')
@click.option('--server-port', type=int,
              help='Server port (overrides config)')
@click.option('--ledger-file',
              help='Path to Beancount ledger file (overrides config)')
@click.option('--ml-enabled', is_flag=True,
              help='Enable ML categorization (overrides config)')
@click.option('--ml-disabled', is_flag=True,
              help='Disable ML categorization (overrides config)')
@click.option('--ml-training-data-file',
              help='Path to ML training data file (overrides config)')
@click.option('--log-level',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              help='Logging level (overrides config)')
@click.option('--debug', is_flag=True,
              help='Enable debug mode (sets log level to DEBUG)')
def main(config: str, server_host: str, server_port: int,
         ledger_file: str, ml_enabled: bool, ml_disabled: bool,
         ml_training_data_file: str, log_level: str, debug: bool):
    """
    Start Finzytrack backend server.

    CLI arguments take precedence over YAML configuration file settings.
    Use flattened argument names for nested config values.
    """

    try:
        # Prepare CLI overrides dictionary
        cli_overrides: dict[str, Any] = {}

        # Server settings
        if server_host:
            cli_overrides['server-host'] = server_host
        if server_port:
            cli_overrides['server-port'] = server_port

        # File paths
        if ledger_file:
            cli_overrides['ledger-file'] = ledger_file

        # ML settings (handle conflicting flags)
        if ml_enabled and ml_disabled:
            click.echo("Error: Cannot specify both --ml-enabled and --ml-disabled", err=True)
            sys.exit(1)
        elif ml_enabled:
            cli_overrides['ml-enabled'] = True
        elif ml_disabled:
            cli_overrides['ml-enabled'] = False

        if ml_training_data_file:
            cli_overrides['ml-training-data-file'] = ml_training_data_file

        # Logging settings
        if log_level:
            cli_overrides['logging-level'] = log_level.upper()

        # Debug mode overrides
        if debug:
            cli_overrides['logging-level'] = 'DEBUG'

        start_server(
            config_path=config,
            cli_overrides=cli_overrides or None,
        )

    except ConfigurationError as e:
        click.echo(f"Configuration Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()