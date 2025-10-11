"""
Metabase Manager Service - Manages Metabase lifecycle and REST API interactions.

This service is responsible for:
- Starting/stopping Metabase subprocess
- Health checking
- First-run initialization (creating admin account, adding DuckDB connection)
- Schema sync triggers
"""
import os
import asyncio
import subprocess
import logging
import json
import secrets
import string
import shlex
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

import httpx

from app.config import MetabaseConfig
from app.exceptions import APIError

logger = logging.getLogger(__name__)


class MetabaseManager:
    """Service for managing Metabase lifecycle and API interactions."""

    def __init__(self, config: MetabaseConfig, duckdb_path: str, config_manager):
        """
        Initialize Metabase manager.

        Args:
            config: Metabase configuration
            duckdb_path: Path to DuckDB file for connection setup
            config_manager: The application's ConfigManager instance.
        """
        self.config = config
        self.duckdb_path = duckdb_path
        self.config_manager = config_manager
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[datetime] = None

    # =====================================
    # Public API
    # =====================================

    async def start(self) -> Dict[str, Any]:
        """
        Start Metabase subprocess.

        Returns:
            Dictionary with pid, port, url, started_at

        Raises:
            APIError: If JAR file not found or Metabase already running
        """
        if self.is_running():
            raise APIError(
                message="Metabase is already running",
                code="METABASE_ALREADY_RUNNING",
                status_code=409,
                details={"pid": self.process.pid if self.process else None}
            )

        return await self._start_process()

    async def stop(self) -> Dict[str, Any]:
        """
        Stop Metabase gracefully.

        Returns:
            Dictionary with stopped_at, uptime_seconds
        """
        if not self.is_running() or self.process is None:
            raise APIError(
                message="Metabase is not running",
                code="METABASE_NOT_RUNNING",
                status_code=409
            )

        process = self.process
        logger.info(f"Stopping Metabase (PID {process.pid})")

        uptime_seconds = 0
        if self.start_time:
            uptime_seconds = int((datetime.utcnow() - self.start_time).total_seconds())

        process.terminate()
        try:
            await asyncio.wait_for(asyncio.create_task(self._wait_for_exit()), timeout=10.0)
            logger.info("Metabase stopped gracefully")
        except asyncio.TimeoutError:
            logger.warning("Metabase did not stop gracefully, forcing kill")
            process.kill()
            await asyncio.create_task(self._wait_for_exit())

        self.process = None
        self.start_time = None

        return {
            "stopped_at": datetime.utcnow().isoformat() + "Z",
            "uptime_seconds": uptime_seconds
        }

    async def get_status(self) -> Dict[str, Any]:
        """
        Get Metabase status, attempting to auto-start if necessary.
        """
        await self._ensure_metabase_is_running()
        
        running = self.is_running()
        healthy = await self.is_healthy() if running else False

        uptime_seconds = 0
        if running and self.start_time:
            uptime_seconds = int((datetime.utcnow() - self.start_time).total_seconds())

        return {
            "running": running,
            "healthy": healthy,
            "initialized": self.config.initialized,
            "port": self.config.port,
            "uptime_seconds": uptime_seconds,
            "pid": self.process.pid if self.process else None,
            "started_at": self.start_time.isoformat() + "Z" if self.start_time else None
        }

    async def initialize_first_run(self) -> Dict[str, Any]:
        """
        First-time setup - auto-configure Metabase.
        """
        await self._ensure_metabase_is_running()

        if self.config.initialized:
            raise APIError(
                message="Metabase has already been initialized",
                code="METABASE_ALREADY_INITIALIZED",
                status_code=409
            )

        logger.info("Starting Metabase first-run initialization")
        try:
            setup_token = await self._get_setup_token()
            admin_password = self._generate_password()
            session_token = await self._create_admin_account(setup_token, admin_password)
            database_id = await self._add_duckdb_connection(session_token)

            state = {
                "initialized": True,
                "admin_password": admin_password,
                "session_token": session_token,
                "database_id": database_id
            }
            self.config_manager.save_metabase_state(state)

            logger.info("Metabase initialization completed successfully")

            return {
                "admin_email": self.config.admin_email,
                "admin_password": admin_password,
                "session_token": session_token,
                "database_id": database_id,
                "dashboards_imported": 0  # TODO: Implement dashboard import
            }
        except Exception as e:
            logger.error(f"Metabase initialization failed: {e}", exc_info=True)
            raise APIError(message=f"Failed to initialize Metabase: {str(e)}", code="METABASE_INIT_FAILED", status_code=500)

    async def trigger_schema_refresh(self) -> Dict[str, Any]:
        """
        Tell Metabase to refresh DuckDB schema cache.
        """
        await self._ensure_metabase_is_running()

        if not self.config.initialized or not self.config.database_id:
            raise APIError(message="Metabase not initialized or DuckDB not connected", code="METABASE_NOT_CONFIGURED", status_code=409)

        await self._api_request("POST", f"/api/database/{self.config.database_id}/sync_schema")
        logger.info(f"Triggered schema refresh for database {self.config.database_id}")

        return {
            "synced_at": datetime.utcnow().isoformat() + "Z",
            "database_id": self.config.database_id
        }

    # =====================================
    # Lifecycle Helpers
    # =====================================

    def is_running(self) -> bool:
        """Check if Metabase process is running."""
        return self.process is not None and self.process.poll() is None

    async def is_healthy(self) -> bool:
        """
        Check if Metabase is responding to health checks.
        """
        if not self.is_running():
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:{self.config.port}/api/health", timeout=5.0)
                return response.status_code == 200
        except Exception:
            return False

    async def _start_process(self) -> Dict[str, Any]:
        """Internal method to start the Metabase Java process."""
        jar_path = Path(self.config.jar_path)
        if not jar_path.exists():
            raise APIError(message="Metabase JAR not found.", code="METABASE_JAR_NOT_FOUND", status_code=500, details={"expected_path": str(jar_path)})

        Path(self.config.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.plugins_dir).mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["MB_JETTY_PORT"] = str(self.config.port)
        env["MB_DB_FILE"] = str(Path(self.config.data_dir) / "metabase.db")
        env["MB_PLUGINS_DIR"] = str(Path(self.config.plugins_dir).absolute())

        java_cmd = ["java", f"-Xmx{self.config.java_heap_size}"]
        if self.config.java_opts:
            java_cmd.extend(shlex.split(self.config.java_opts))
        java_cmd.extend(["-jar", str(jar_path.absolute())])

        logger.info(f"Starting Metabase: {' '.join(java_cmd)}")
        try:
            self.process = subprocess.Popen(java_cmd, env=env, stdout=None, stderr=None)
            self.start_time = datetime.utcnow()
            logger.info(f"Metabase started with PID {self.process.pid}")
            await self._wait_for_health(timeout=120)
            return {
                "pid": self.process.pid,
                "port": self.config.port,
                "url": f"http://localhost:{self.config.port}",
                "started_at": self.start_time.isoformat() + "Z"
            }
        except Exception as e:
            logger.error(f"Failed to start Metabase: {e}", exc_info=True)
            if self.process:
                self.process.kill()
                self.process = None
            raise APIError(message=f"Failed to start Metabase: {str(e)}", code="METABASE_START_FAILED", status_code=500)

    async def _ensure_metabase_is_running(self):
        """Checks if Metabase is running and starts it if auto_start is enabled."""
        if not self.is_running() and self.config.auto_start:
            logger.info("Metabase is not running. Attempting to auto-start...")
            await self._start_process()

    async def _wait_for_health(self, timeout: int = 120):
        """Wait for Metabase to become healthy."""
        logger.info(f"Waiting for Metabase to become healthy (timeout: {timeout}s). This can take a minute...")
        start = datetime.utcnow()
        while (datetime.utcnow() - start).total_seconds() < timeout:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(f"Metabase process terminated unexpectedly with exit code {self.process.poll()}")
            if await self.is_healthy():
                logger.info(f"Metabase is healthy (took {int((datetime.utcnow() - start).total_seconds())}s)")
                return
            await asyncio.sleep(2)
        raise TimeoutError(f"Metabase did not become healthy within {timeout} seconds")

    async def _wait_for_exit(self):
        """Wait for process to exit (async wrapper)."""
        if self.process:
            await asyncio.to_thread(self.process.wait)

    # =====================================
    # API Helpers
    # =====================================

    async def _login(self) -> str:
        """Login to Metabase to get a new session token."""
        logger.info("Attempting to log in to Metabase to get a new session token.")
        payload = {"username": self.config.admin_email, "password": self.config.admin_password}
        async with httpx.AsyncClient() as client:
            response = await client.post(f"http://localhost:{self.config.port}/api/session", json=payload, timeout=10.0)
            response.raise_for_status()
            return response.json()["id"]

    async def _api_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """Makes an authenticated API request to Metabase, handling token refresh."""
        if not self.config.session_token:
            raise APIError(message="Metabase session token is not available.", code="METABASE_NOT_CONFIGURED")

        headers = {"X-Metabase-Session": self.config.session_token, **kwargs.pop("headers", {})}
        url = f"http://localhost:{self.config.port}{endpoint}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(method, url, headers=headers, timeout=30.0, **kwargs)
                response.raise_for_status() # Raise HTTPStatusError for 4xx/5xx responses
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.warning("Metabase API call failed with 401. Token may be expired. Attempting to refresh token...")
                    new_token = await self._login()
                    self.config_manager.save_metabase_state({"session_token": new_token})
                    
                    logger.info("Retrying API request with new session token.")
                    headers["X-Metabase-Session"] = new_token
                    response = await client.request(method, url, headers=headers, timeout=30.0, **kwargs)
                    response.raise_for_status()
                    return response
                else:
                    raise # Re-raise other HTTP errors

    async def _get_setup_token(self) -> str:
        """Get setup token from Metabase."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://localhost:{self.config.port}/api/session/properties", timeout=10.0)
            response.raise_for_status()
            return response.json()["setup-token"]

    async def _create_admin_account(self, setup_token: str, password: str) -> str:
        """Create admin account and return session token."""
        payload = {
            "token": setup_token,
            "user": {"first_name": "Admin", "last_name": "User", "email": self.config.admin_email, "password": password, "site_name": "Finzytrack Analytics"},
            "prefs": {"site_name": "Finzytrack Analytics", "allow_tracking": False}
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"http://localhost:{self.config.port}/api/setup", json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()["id"]

    async def _add_duckdb_connection(self, session_token: str) -> int:
        """Add DuckDB as a data source in Metabase."""
        db_path = str(Path(self.duckdb_path).absolute())
        payload = {
            "name": "Finzytrack Ledger",
            "engine": "duckdb",
            "details": {"database_file": db_path, "read_only": False, "old_implicit_casting": True, "allow_unsigned_extensions": False},
            "is_on_demand": False,
            "is_full_sync": True,
            "is_sample": False,
            "cache_ttl": None,
            "refingerprint": False,
            "auto_run_queries": True,
            "schedules": {},
        }
        logger.info(f"Adding DuckDB connection with payload: {json.dumps(payload, indent=2)}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"http://localhost:{self.config.port}/api/database", json=payload, headers={"X-Metabase-Session": session_token}, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                logger.info(f"DuckDB connection added successfully with ID: {data['id']}")
                return data["id"]
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to add DuckDB connection. Status: {e.response.status_code}, Body: {e.response.text}")
            raise e

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        """Generate a secure random password with only alphanumeric characters."""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
