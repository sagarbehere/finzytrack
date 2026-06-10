"""
Finzytrack Backend Main Entry Point

CLI interface with configuration management and logging setup.
"""
import os
import sys
import logging
import platform
from pathlib import Path
from typing import Any, Optional
from contextlib import asynccontextmanager

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from ._version import __version__
from .app_mode import AppMode
from .config import (
    Config, ConfigurationError,
    LOG_FORMAT, CORS_ORIGINS,
)
from .api.routers.importer import ofx_accounts, transaction, csv_rules, xls_rules, email as email_import_router, llm_parse, rule_templates
from .api.routers import accounts, commodities, ledger_export, ledger_transactions, query, config as config_router, filesystem, notices, assistant, recipes, ai_services, setup as setup_router
from .error_handler import setup_error_handlers
from .service_factory import create_user_services, seed_config, startup_user_services
from .service_registry import ServiceRegistry


_RULE_TEMPLATE_FILES = ["csv-template.yaml", "xls-template.yaml", "email-template.yaml"]


def setup_logging(
    level: str,
    max_file_size_mb: int = 5,
    backup_count: int = 3,
    log_file: str = "./logs/finzytrack.log",
) -> None:
    """Configure application logging with automatic directory creation and rotation."""
    from logging.handlers import RotatingFileHandler

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

    # Create formatter and the user_id filter (injects per-request user_id
    # from the contextvar onto every LogRecord; see app/logging_context.py)
    from app.logging_context import UserIdLogFilter
    formatter = logging.Formatter(LOG_FORMAT)
    user_id_filter = UserIdLogFilter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(user_id_filter)
    logger.addHandler(console_handler)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_file_size_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(user_id_filter)
    logger.addHandler(file_handler)


def _check_rule_templates(logger: logging.Logger) -> None:
    """Log a warning if any rule template files are missing.

    Templates are read-only app resources served via importlib.resources,
    not user-editable config.  A missing template is non-fatal — the API
    endpoint returns a proper error and the frontend falls back to an
    empty editor.
    """
    try:
        import importlib.resources
        pkg = importlib.resources.files("app.templates.rules")
        for name in _RULE_TEMPLATE_FILES:
            resource = pkg.joinpath(name)
            if not resource.is_file():
                logger.warning(f"Rule template missing: {name} — 'New Rule' will not pre-fill a template for this type")
    except Exception as e:
        logger.warning(f"Could not verify rule templates: {e}")


def create_app(
    config: Config,
    mode: AppMode = AppMode.DESKTOP,
    static_dir: Optional[str] = None,
) -> FastAPI:
    """Create FastAPI application with configuration.

    In desktop mode, services are created eagerly and pre-registered in the
    ``ServiceRegistry``.  In hosted mode, services are created lazily per
    user on first request.
    """
    logger = logging.getLogger(__name__)

    # ── Service registry setup ──────────────────────────────────────────

    registry = ServiceRegistry(mode=mode, base_dir=config.root_dir)

    if mode == AppMode.DESKTOP:
        # Desktop: create services eagerly (same as before)
        services = create_user_services(config, user_id="local")
        registry.pre_register("local", services, config)
    # Hosted: services are created lazily by the registry on first request

    # ── Lifespan ────────────────────────────────────────────────────────

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Application starting up...")

        # Desktop mode: run async startup for the pre-registered services
        if mode == AppMode.DESKTOP:
            await startup_user_services(services, config)

        # AI assistant readiness:
        #   - In dev mode, auto-sync from frontend/ if any file is missing so a
        #     fresh clone works without remembering to run the sync script.
        #   - In frozen mode, autosync_dev is a no-op (the bundle ships
        #     pre-synced copies; source files don't exist at runtime).
        # Either way, log the final readiness state so missed sync steps show
        # up as a WARNING instead of silently breaking the assistant later.
        from app.ai.reference import autosync_dev, log_readiness
        autosync_dev()
        log_readiness()

        logger.info("Application startup complete")
        yield

        logger.info("Application shutting down...")
        await registry.shutdown_all()
        logger.info("Application shutdown complete")

    # ── FastAPI app ─────────────────────────────────────────────────────

    app = FastAPI(
        title="Finzytrack Backend",
        description="Privacy-focused personal finance application backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store mode, paths, and registry on app state for dependency injection
    app.state.mode = mode
    app.state.root_dir = config.root_dir
    app.state.base_dir = config.root_dir
    app.state.registry = registry

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Centralised exception handlers
    setup_error_handlers(app)

    # ── API routers ─────────────────────────────────────────────────────

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
    app.include_router(config_router.router, prefix="/api", tags=["config"])
    app.include_router(filesystem.router, prefix="/api", tags=["filesystem"])
    app.include_router(notices.router, prefix="/api", tags=["notices"])
    app.include_router(assistant.router, prefix="/api", tags=["assistant"])
    app.include_router(ai_services.router, prefix="/api", tags=["ai"])
    app.include_router(recipes.router, prefix="/api", tags=["recipes"])
    app.include_router(setup_router.router, prefix="/api", tags=["setup"])

    # ── Static files (mounts only — SPA fallback registered last) ───────
    #
    # The SPA catch-all `@app.get("/{full_path:path}")` must be the LAST
    # route registered. FastAPI matches routes in registration order, so any
    # top-level (non-`/api`) route declared after the catch-all (e.g.
    # `/health`, `/debug/config`) would be shadowed and return `index.html`
    # instead of the intended JSON. See the bottom of this function for
    # where the SPA fallback is actually registered.

    static_path = Path(static_dir) if static_dir else None
    if static_path and static_path.exists():
        app.mount("/assets", StaticFiles(directory=str(static_path / "assets")), name="assets")
        for item in static_path.iterdir():
            if item.is_dir() and item.name != "assets":
                app.mount(f"/{item.name}", StaticFiles(directory=str(item)), name=item.name)

        @app.get("/")
        async def serve_index():
            return FileResponse(str(static_path / "index.html"))
    else:
        @app.get("/")
        async def root():
            return {"message": "Finzytrack Backend", "version": __version__}

    # ── Health / debug (use registry for desktop, mode-aware) ───────────

    @app.get("/health")
    async def health():
        """Perform health checks on critical application components.

        The response also carries app/runtime metadata (version, python,
        platform) that the frontend's About tab surfaces and that users
        include in bug reports via the "Copy diagnostics" button. The
        metadata fields don't participate in the healthy/unhealthy
        determination — they're purely informational.
        """
        checks: dict[str, Any] = {
            "config_loaded": True,
            "mode": mode.value,
            "app_version": __version__,
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        }

        if mode == AppMode.DESKTOP:
            svc = services  # pre-registered singleton
            cfg = config

            checks["ledger_readable"] = False
            try:
                ledger_path = Path(cfg.ledger_file)
                if ledger_path.exists() and os.access(ledger_path, os.R_OK):
                    checks["ledger_readable"] = True
            except Exception:
                pass

            checks["backup_dir_writable"] = False
            try:
                backup_path = Path(cfg.backup_dir)
                if backup_path.exists() and os.access(backup_path, os.W_OK):
                    checks["backup_dir_writable"] = True
            except Exception:
                pass

            training_samples = 0
            try:
                training_samples = len(svc.sqlite_reader.get_training_data())
            except Exception:
                pass
            checks["training_samples"] = training_samples

            try:
                reg = svc.email_registry
                checks["email_import"] = {
                    "enabled": cfg.email_import.enabled,
                    "profiles_loaded": reg.profile_count,
                    "invalid_profiles": len(reg.list_invalid_profiles()),
                }
            except Exception:
                checks["email_import"] = {"enabled": False}

            critical_checks = {k: v for k, v in checks.items()
                               if k not in ("training_samples", "mode",
                                            "app_version", "python_version",
                                            "platform")}
            is_healthy = all(critical_checks.values())
        else:
            # Hosted mode: lightweight health (no per-user checks)
            is_healthy = True

        if not is_healthy:
            return JSONResponse(status_code=503, content={"status": "unhealthy", "details": checks})
        return {"status": "healthy", "details": checks}

    @app.get("/debug/config")
    async def debug_config():
        """Debug endpoint to inspect current configuration values."""
        if mode == AppMode.DESKTOP:
            config_dict = config.model_dump(mode='json')
            config_dict["_runtime"] = {
                "active_database": "sqlite",
                "ofx_account_mappings_count": len(services.config_manager.get_ofx_mappings()),
            }
            return config_dict
        return {"mode": "hosted", "message": "Per-user config available via /api/config"}

    # ── SPA fallback (MUST be the last route registered) ────────────────
    # Anything not matched by an earlier route or `/api/*` router falls
    # through to the SPA so client-side routes (e.g. /dashboard) work on
    # hard refresh. Adding any non-`/api` top-level route below this point
    # would silently break it.
    if static_path and static_path.exists():
        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            return FileResponse(str(static_path / "index.html"))

    return app


def start_server(
    config_path: str = './config/config.yaml',
    cli_overrides: Optional[dict[str, Any]] = None,
    static_dir: Optional[str] = None,
    shutdown_event: Optional[Any] = None,
    mode: AppMode = AppMode.DESKTOP,
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
        mode: AppMode.DESKTOP (single user) or AppMode.HOSTED (multi-user).
    """
    # Set up logging with defaults first so seed/config errors are captured.
    # Reconfigured below with user settings once config is loaded.
    setup_logging(level='INFO')

    # Seed config directory on first run (before loading config).
    # Data directory is seeded later by the setup wizard so the user's
    # chosen currency can be applied to the starter ledger.
    seed_config(Path('./config'))

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
        log_file=app_config.log_file,
    )

    logger = logging.getLogger(__name__)
    logger.info("Starting Finzytrack backend server (mode=%s)", mode.value)
    logger.info(f"Configuration loaded from: {config_path}")
    logger.info(f"Server will start on {app_config.server.host}:{app_config.server.port}")

    if cli_overrides:
        logger.info(f"CLI overrides applied: {list(cli_overrides.keys())}")

    # Check rule templates are accessible (non-fatal — endpoint returns 404 if missing)
    _check_rule_templates(logger)

    # Create FastAPI app
    app = create_app(app_config, mode=mode, static_dir=static_dir)

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
@click.option('--mode', 'app_mode',
              type=click.Choice(['desktop', 'hosted'], case_sensitive=False),
              default=None,
              help='Application mode (default: desktop, env: FINZYTRACK_MODE)')
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
@click.option('--log-level',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              help='Logging level (overrides config)')
@click.option('--static-dir',
              help='Path to built frontend (e.g. ../frontend/dist) to serve the UI')
@click.option('--debug', is_flag=True,
              help='Enable debug mode (sets log level to DEBUG)')
def main(config: str, app_mode: Optional[str], server_host: str, server_port: int,
         ledger_file: str, ml_enabled: bool, ml_disabled: bool,
         log_level: str, static_dir: str,
         debug: bool):
    """
    Start Finzytrack backend server.

    CLI arguments take precedence over YAML configuration file settings.
    Use flattened argument names for nested config values.
    """

    try:
        # Resolve mode: CLI flag > env var > default (desktop)
        if app_mode:
            mode = AppMode(app_mode.lower())
        else:
            env_mode = os.environ.get("FINZYTRACK_MODE", "").lower()
            mode = AppMode(env_mode) if env_mode in ("desktop", "hosted") else AppMode.DESKTOP

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

        # Logging settings
        if log_level:
            cli_overrides['logging-level'] = log_level.upper()

        # Debug mode overrides
        if debug:
            cli_overrides['logging-level'] = 'DEBUG'

        start_server(
            config_path=config,
            cli_overrides=cli_overrides or None,
            static_dir=static_dir,
            mode=mode,
        )

    except ConfigurationError as e:
        click.echo(f"Configuration Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
