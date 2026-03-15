"""
FinzyTrack Email Service entry point.

Usage:
    python3 -m app.main -c config/config.yaml --port 8100
    python3 -m app.main -c config/config.yaml --port 8100 --debug
"""
import logging
import sys

import click
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import health, profiles, fetch
from app.config import load_config


def setup_logging(level: str) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def create_app() -> FastAPI:
    """Create the FastAPI application. Config must already be loaded."""
    application = FastAPI(
        title="FinzyTrack Email Service",
        version="1.0.0",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(profiles.router)
    application.include_router(fetch.router)

    return application


# Module-level app instance for direct uvicorn use (uvicorn app.main:app)
app = create_app()


@click.command()
@click.option('--config', '-c',
              default='config/config.yaml',
              show_default=True,
              help='Path to configuration file')
@click.option('--host',
              default=None,
              help='Server host address (overrides config)')
@click.option('--port',
              default=None,
              type=int,
              help='Server port (overrides config)')
@click.option('--log-level',
              default='INFO',
              show_default=True,
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
              help='Logging level')
@click.option('--debug', is_flag=True,
              help='Enable debug mode (sets log level to DEBUG)')
def main(config: str, host: str, port: int, log_level: str, debug: bool) -> None:
    """Start the FinzyTrack email import service."""
    effective_log_level = 'DEBUG' if debug else log_level

    setup_logging(effective_log_level)
    logger = logging.getLogger(__name__)

    try:
        cfg = load_config(config)
        logger.info(f"Configuration loaded from: {config}")
        logger.info(f"Rules directory: {cfg.rules_path}")

        effective_host = host or cfg.server.host
        effective_port = port or cfg.server.port
        logger.info(f"Starting email service on {effective_host}:{effective_port}")

        uvicorn.run(
            app,
            host=effective_host,
            port=effective_port,
            log_config=None,
        )
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
