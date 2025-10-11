"""
Finzytrack Backend Main Entry Point

CLI interface with configuration management and logging setup.
"""
import os
import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Config, ConfigurationError
from .api.routers.importer import ofx_accounts, transaction
from .api.routers import accounts, commodities, ledger_export, metabase
from .core.beancount_manager import BeancountManager
from .error_handler import setup_error_handlers
from .core.backup_manager import BackupManager
from .core.config_manager import ConfigManager
from .core.ledger_initializer import LedgerInitializer
from .services.duckdb_exporter import DuckDBExporter
from .services.duckdb_sync_manager import DuckDBSyncManager
from .services.metabase_manager import MetabaseManager


def setup_logging(level: str, log_file: str, log_format: str) -> None:
    """Configure application logging with automatic directory creation."""
    # Create logs directory if it doesn't exist
    log_path = Path(log_file)
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
    formatter = logging.Formatter(log_format)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)



def create_app(config: Config) -> FastAPI:
    """Create FastAPI application with configuration."""
    # Get logger for this module
    logger = logging.getLogger(__name__)

    # --- Service Instantiation ---

    # 1. Create BackupManager service
    backup_manager = BackupManager(
        backup_dir=Path(config.backup.backup_dir),
        retention_count=config.backup.retention_count
    )

    # 2. Create ConfigManager service
    config_manager = ConfigManager(
        config=config,
        backup_manager=backup_manager
    )

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

    # 5. Create DuckDB exporter
    duckdb_exporter = DuckDBExporter(
        duckdb_path=config.analytics.duckdb.export_path
    )

    # 6. Create DuckDB sync manager (coordinator with debouncing)
    duckdb_sync_manager = DuckDBSyncManager(
        duckdb_exporter=duckdb_exporter,
        ledger_file=config.ledger_file,
        delay=config.analytics.duckdb.sync_debounce_seconds
    )

    # 7. Create Metabase manager
    metabase_manager = MetabaseManager(
        config=config.analytics.metabase,
        duckdb_path=config.analytics.duckdb.export_path,
        config_manager=config_manager
    )

    # 8. Ensure ledger exists - fail fast if it can't be created
    try:
        if not ledger_initializer.ensure_ledger_exists():
            raise RuntimeError(f"Failed to create ledger file at {config.ledger_file}")
        logger.info(f"Ledger verified/created: {config.ledger_file}")
    except Exception as e:
        logger.error(f"Fatal: Cannot initialize ledger file {config.ledger_file}: {e}")
        raise RuntimeError(f"Failed to initialize ledger file {config.ledger_file}: {e}")

    # 9. Register DuckDB sync manager's callback with BeancountManager
    if config.analytics.duckdb.auto_sync_enabled:
        beancount_manager.register_cache_invalidation_callback(
            duckdb_sync_manager.on_ledger_changed
        )
        logger.info("DuckDB auto-sync enabled with debouncing")

    # 10. Define lifespan event handler
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        Application lifespan event handler for startup and shutdown tasks.

        This replaces the deprecated @app.on_event decorators.
        """
        # STARTUP: Tasks to run when the application starts
        logger.info("Application starting up...")

        # Export DuckDB on startup if out of date
        if duckdb_sync_manager._needs_export():
            try:
                entries = beancount_manager.cache.get_entries()
                await duckdb_exporter.export_entries(entries)
                logger.info("DuckDB exported on startup")
            except Exception as e:
                logger.error(f"Failed to export DuckDB on startup: {e}")

        # Auto-start Metabase if configured
        if config.analytics.metabase.auto_start:
            try:
                await metabase_manager.start()
                logger.info("Metabase auto-started on application startup")
            except Exception as e:
                logger.error(f"Failed to auto-start Metabase: {e}")

        logger.info("Application startup complete")

        # YIELD: Application is running, handle requests
        yield

        # SHUTDOWN: Tasks to run when the application shuts down
        logger.info("Application shutting down...")

        # Cancel pending DuckDB sync
        try:
            await duckdb_sync_manager.cancel_pending_sync()
            logger.info("Cancelled pending DuckDB sync")
        except Exception as e:
            logger.error(f"Error cancelling DuckDB sync: {e}")

        # Stop Metabase gracefully if running
        if metabase_manager.is_running():
            try:
                await metabase_manager.stop()
                logger.info("Metabase stopped on application shutdown")
            except Exception as e:
                logger.error(f"Error stopping Metabase: {e}")

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
        allow_origins=config.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 13. Register centralized exception handlers
    setup_error_handlers(app)

    # 14. Store managers in app state for access in routes
    app.state.config_manager = config_manager
    app.state.beancount_manager = beancount_manager
    app.state.backup_manager = backup_manager
    app.state.duckdb_exporter = duckdb_exporter
    app.state.duckdb_sync_manager = duckdb_sync_manager
    app.state.metabase_manager = metabase_manager

    # Include API routers
    app.include_router(ofx_accounts.router, prefix="/api/import", tags=["import"])
    app.include_router(transaction.router, prefix="/api/import", tags=["import"])
    app.include_router(accounts.router, prefix="/api", tags=["accounts"])
    app.include_router(commodities.router, prefix="/api", tags=["commodities"])
    app.include_router(ledger_export.router, prefix="/api/ledger", tags=["ledger"])
    app.include_router(metabase.router, prefix="/api/metabase", tags=["metabase"])
    
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
            backup_path = Path(config.backup.backup_dir)
            if backup_path.exists() and os.access(backup_path, os.W_OK):
                checks["backup_dir_writable"] = True
        except Exception:
            checks["backup_dir_writable"] = False

        # Conditionally check ML training data file
        if config.ml.enabled:
            checks["ml_training_data_readable"] = False
            if config.ml.training_data_file:
                try:
                    training_file_path = Path(config.ml.training_data_file)
                    if training_file_path.exists() and os.access(training_file_path, os.R_OK):
                        checks["ml_training_data_readable"] = True
                except Exception:
                    checks["ml_training_data_readable"] = False

        # Determine overall status
        is_healthy = all(checks.values())

        if not is_healthy:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "details": checks}
            )

        return {"status": "healthy", "details": checks}
    
    @app.get("/debug/config")
    async def debug_config():
        """Debug endpoint to inspect current configuration values."""
        return {
            "server": {
                "host": config.server.host,
                "port": config.server.port
            },
            "files": {
                "ledger_file": config.ledger_file,
                "backup_dir": config.backup.backup_dir,
            },
            "ml": {
                "enabled": config.ml.enabled,
                "training_data_file": config.ml.training_data_file
            },
            "logging": {
                "level": config.logging.level,
                "file": config.logging.file
            },
            "accounts": {
                "default_currency": config.accounts.default_currency,
                "default_unknown_account": config.accounts.default_unknown_account
            },
            "features": {
                "duplicate_detection": config.features.duplicate_detection,
                "auto_categorization": config.features.auto_categorization
            },
            "ofx_account_mappings_count": len(config.ofx_account_mappings)
        }
    
    return app



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
@click.option('--backup-dir', 
              help='Path to backup directory (overrides config)')
@click.option('--ml-enabled', is_flag=True, 
              help='Enable ML categorization (overrides config)')
@click.option('--ml-disabled', is_flag=True, 
              help='Disable ML categorization (overrides config)')
@click.option('--ml-training-data-file', 
              help='Path to ML training data file (overrides config)')
@click.option('--log-level', 
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              help='Logging level (overrides config)')
@click.option('--log-file', 
              help='Path to log file (overrides config)')
@click.option('--debug', is_flag=True, 
              help='Enable debug mode (sets log level to DEBUG)')
def main(config: str, server_host: str, server_port: int,
         ledger_file: str, backup_dir: str, ml_enabled: bool, ml_disabled: bool,
         ml_training_data_file: str, log_level: str, log_file: str, debug: bool):
    """
    Start Finzytrack backend server.
    
    CLI arguments take precedence over YAML configuration file settings.
    Use flattened argument names for nested config values.
    """
    
    try:
        # Prepare CLI overrides dictionary
        cli_overrides = {}
        
        # Server settings
        if server_host:
            cli_overrides['server-host'] = server_host
        if server_port:
            cli_overrides['server-port'] = server_port
        
        # File paths
        if ledger_file:
            cli_overrides['ledger-file'] = ledger_file
        if backup_dir:
            cli_overrides['backup-dir'] = backup_dir
        
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
        if log_file:
            cli_overrides['logging-file'] = log_file
        
        # Debug mode overrides
        if debug:
            cli_overrides['logging-level'] = 'DEBUG'
        
        # Load configuration with CLI overrides using Pydantic
        app_config = Config.from_yaml_file(config, cli_overrides)
        
        # Setup logging first
        setup_logging(
            level=app_config.logging.level,
            log_file=app_config.logging.file,
            log_format=app_config.logging.format
        )
        
        logger = logging.getLogger(__name__)
        logger.info("Starting Finzytrack backend server")
        logger.info(f"Configuration loaded from: {config}")
        logger.info(f"Server will start on {app_config.server.host}:{app_config.server.port}")
        
        if cli_overrides:
            logger.info(f"CLI overrides applied: {list(cli_overrides.keys())}")
        
        # Create FastAPI app
        app = create_app(app_config)

        # Ensure uvicorn loggers use the same level and handlers
        log_level = app_config.logging.level.upper()
        logging.getLogger("uvicorn").setLevel(log_level)
        logging.getLogger("uvicorn.error").setLevel(log_level)
        logging.getLogger("uvicorn.access").setLevel(log_level)
        
        # Start server
        uvicorn.run(
            app,
            host=app_config.server.host,
            port=app_config.server.port,
            log_config=None
        )
        
    except ConfigurationError as e:
        click.echo(f"Configuration Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()