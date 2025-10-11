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

    def __init__(self, config: MetabaseConfig, duckdb_path: str):
        """
        Initialize Metabase manager.

        Args:
            config: Metabase configuration
            duckdb_path: Path to DuckDB file for connection setup
        """
        self.config = config
        self.duckdb_path = duckdb_path
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[datetime] = None

    # =====================================
    # Lifecycle Management
    # =====================================

    async def start(self) -> Dict[str, Any]:
        """
        Start Metabase subprocess.

        Returns:
            Dictionary with pid, port, url, started_at

        Raises:
            APIError: If JAR file not found or Metabase already running
        """
        # Check if already running
        if self.is_running():
            raise APIError(
                message="Metabase is already running",
                code="METABASE_ALREADY_RUNNING",
                status_code=409,
                details={"pid": self.process.pid if self.process else None}
            )

        # Verify JAR exists
        jar_path = Path(self.config.jar_path)
        if not jar_path.exists():
            raise APIError(
                message="Metabase JAR not found. Please ensure Metabase is installed.",
                code="METABASE_JAR_NOT_FOUND",
                status_code=500,
                details={"expected_path": str(jar_path)}
            )

        # Ensure data and plugins directories exist
        Path(self.config.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.plugins_dir).mkdir(parents=True, exist_ok=True)

        # Build Java command
        env = os.environ.copy()
        env["MB_JETTY_PORT"] = str(self.config.port)
        env["MB_DB_FILE"] = str(Path(self.config.data_dir) / "metabase.db")
        env["MB_PLUGINS_DIR"] = str(Path(self.config.plugins_dir).absolute())

        # Build Java command with properly split java_opts
        java_cmd = ["java", f"-Xmx{self.config.java_heap_size}"]

        # Split java_opts into separate arguments
        if self.config.java_opts:
            java_cmd.extend(shlex.split(self.config.java_opts))

        java_cmd.extend(["-jar", str(jar_path.absolute())])

        logger.info(f"Starting Metabase: {' '.join(java_cmd)}")

        # Start subprocess
        try:
            # For debugging: let stdout/stderr go to console so we can see Metabase output
            # Don't set cwd - let Metabase use absolute paths from environment variables
            self.process = subprocess.Popen(
                java_cmd,
                env=env,
                stdout=None,  # Inherit parent's stdout (console)
                stderr=None,  # Inherit parent's stderr (console)
            )
            self.start_time = datetime.utcnow()

            logger.info(f"Metabase started with PID {self.process.pid}")
            logger.info(f"Metabase database will be at: {env['MB_DB_FILE']}")
            logger.info(f"Metabase will output logs directly to console...")

            # Wait for Metabase to become healthy (with timeout)
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
            raise APIError(
                message=f"Failed to start Metabase: {str(e)}",
                code="METABASE_START_FAILED",
                status_code=500
            )

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

        # Store process reference for type safety
        process = self.process

        logger.info(f"Stopping Metabase (PID {process.pid})")

        # Calculate uptime
        uptime_seconds = 0
        if self.start_time:
            uptime_seconds = int((datetime.utcnow() - self.start_time).total_seconds())

        # Terminate process
        process.terminate()

        # Wait for graceful shutdown (max 10 seconds)
        try:
            await asyncio.wait_for(
                asyncio.create_task(self._wait_for_exit()),
                timeout=10.0
            )
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

    def is_running(self) -> bool:
        """Check if Metabase process is running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    async def is_healthy(self) -> bool:
        """
        Check if Metabase is responding.

        Returns:
            True if healthy, False otherwise
        """
        if not self.is_running():
            return False

        try:
            # Suppress httpx logging for health checks to reduce noise
            httpx_logger = logging.getLogger("httpx")
            original_level = httpx_logger.level
            httpx_logger.setLevel(logging.WARNING)

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"http://localhost:{self.config.port}/api/health",
                        timeout=5.0
                    )
                    return response.status_code == 200
            finally:
                httpx_logger.setLevel(original_level)
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """
        Get Metabase status.

        Returns:
            Dictionary with running, healthy, initialized, port, uptime_seconds, etc.
        """
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

    # =====================================
    # First-Run Initialization
    # =====================================

    async def initialize_first_run(self, config_manager) -> Dict[str, Any]:
        """
        First-time setup - auto-configure Metabase.

        This calls Metabase's REST API to:
        1. Get setup token
        2. Create admin account
        3. Add DuckDB connection
        4. Import dashboard templates (if available)

        Args:
            config_manager: ConfigManager instance to save state

        Returns:
            Dictionary with admin_email, admin_password, session_token, database_id, etc.
        """
        if not self.is_running():
            raise APIError(
                message="Metabase is not running. Start it first.",
                code="METABASE_NOT_RUNNING",
                status_code=409
            )

        if self.config.initialized:
            raise APIError(
                message="Metabase has already been initialized",
                code="METABASE_ALREADY_INITIALIZED",
                status_code=409
            )

        logger.info("Starting Metabase first-run initialization")

        try:
            # Step 1: Get setup token
            setup_token = await self._get_setup_token()

            # Step 2: Create admin account
            admin_password = self._generate_password()
            session_token = await self._create_admin_account(
                setup_token, admin_password
            )

            # Step 3: Add DuckDB connection
            database_id = await self._add_duckdb_connection(session_token)

            # Step 4: Import dashboard templates (if available)
            dashboards_imported = 0
            # TODO: Implement dashboard import when templates are ready

            # Step 5: Save state to config, storing password in plaintext
            state = {
                "initialized": True,
                "admin_password": admin_password,
                "session_token": session_token,
                "database_id": database_id
            }
            config_manager.save_metabase_state(state)

            logger.info("Metabase initialization completed successfully")

            return {
                "admin_email": self.config.admin_email,
                "admin_password": admin_password,  # Return plaintext password for one-time display
                "session_token": session_token,
                "database_id": database_id,
                "dashboards_imported": dashboards_imported
            }

        except Exception as e:
            logger.error(f"Metabase initialization failed: {e}", exc_info=True)
            raise APIError(
                message=f"Failed to initialize Metabase: {str(e)}",
                code="METABASE_INIT_FAILED",
                status_code=500
            )

    async def trigger_schema_refresh(self) -> Dict[str, Any]:
        """
        Tell Metabase to refresh DuckDB schema cache.

        Returns:
            Dictionary with sync status

        Raises:
            APIError: If not initialized or database not connected
        """
        if not self.config.initialized or not self.config.database_id:
            raise APIError(
                message="Metabase not initialized or DuckDB not connected",
                code="METABASE_NOT_CONFIGURED",
                status_code=409
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://localhost:{self.config.port}/api/database/{self.config.database_id}/sync_schema",
                    headers={"X-Metabase-Session": self.config.session_token},
                    timeout=30.0
                )
                response.raise_for_status()

                logger.info(f"Triggered schema refresh for database {self.config.database_id}")

                return {
                    "synced_at": datetime.utcnow().isoformat() + "Z",
                    "database_id": self.config.database_id
                }

        except Exception as e:
            logger.error(f"Failed to trigger schema refresh: {e}", exc_info=True)
            raise APIError(
                message=f"Failed to refresh schema: {str(e)}",
                code="SCHEMA_REFRESH_FAILED",
                status_code=500
            )

    # =====================================
    # Private Helper Methods
    # =====================================

    async def _wait_for_health(self, timeout: int = 120):
        """Wait for Metabase to become healthy."""
        logger.info(f"Waiting for Metabase to become healthy (timeout: {timeout}s)")
        logger.info("This may take 30-60 seconds on first launch...")
        start = datetime.utcnow()
        last_log_time = start
        check_count = 0

        while (datetime.utcnow() - start).total_seconds() < timeout:
            check_count += 1

            # Check if process is still alive
            if self.process and self.process.poll() is not None:
                exit_code = self.process.poll()
                logger.error(f"Metabase process died unexpectedly with exit code: {exit_code}")
                logger.error(f"Check the console output above for Metabase error messages")
                raise RuntimeError(f"Metabase process terminated with exit code {exit_code}")

            if await self.is_healthy():
                elapsed = int((datetime.utcnow() - start).total_seconds())
                logger.info(f"Metabase is healthy (took {elapsed}s, {check_count} checks)")
                return

            # Log progress every 10 seconds
            elapsed = (datetime.utcnow() - last_log_time).total_seconds()
            if elapsed >= 10:
                total_elapsed = int((datetime.utcnow() - start).total_seconds())
                # Check if process is still running
                if self.process:
                    logger.info(f"Still waiting for Metabase... ({total_elapsed}s elapsed, PID {self.process.pid} still running)")
                else:
                    logger.error(f"Still waiting but process reference lost ({total_elapsed}s elapsed)")
                last_log_time = datetime.utcnow()

            await asyncio.sleep(2)

        # Final diagnostic before timeout
        if self.process and self.process.poll() is None:
            logger.error(f"Metabase process (PID {self.process.pid}) is still running but not responding to health checks")
            logger.error(f"Attempted {check_count} health checks on http://localhost:{self.config.port}/api/health")

        raise TimeoutError(f"Metabase did not become healthy within {timeout} seconds")

    async def _wait_for_exit(self):
        """Wait for process to exit (async wrapper)."""
        if self.process:
            await asyncio.to_thread(self.process.wait)

    async def _get_setup_token(self) -> str:
        """Get setup token from Metabase."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:{self.config.port}/api/session/properties",
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
            return data["setup-token"]

    async def _create_admin_account(self, setup_token: str, password: str) -> str:
        """Create admin account and return session token."""
        payload = {
            "token": setup_token,
            "user": {
                "first_name": "Admin",
                "last_name": "User",
                "email": self.config.admin_email,
                "password": password,
                "site_name": "Finzytrack Analytics"
            },
            "prefs": {
                "site_name": "Finzytrack Analytics",
                "allow_tracking": False
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:{self.config.port}/api/setup",
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
            return data["id"]  # Session token

    async def _add_duckdb_connection(self, session_token: str) -> int:
        """Add DuckDB as a data source in Metabase."""
        db_path = str(Path(self.duckdb_path).absolute())

        # This payload is modeled after the one captured from a successful
        # manual setup in the Metabase UI.
        payload = {
            "name": "Finzytrack Ledger",
            "engine": "duckdb",
            "details": {
                "database_file": db_path,
                "read_only": False,
                "old_implicit_casting": True,
                "allow_unsigned_extensions": False,
            },
            "is_on_demand": False,
            "is_full_sync": True,
            "is_sample": False,
            "cache_ttl": None,
            "refingerprint": False,
            "auto_run_queries": True,
            "schedules": {},
        }

        logger.info(f"Adding DuckDB connection with path: {db_path}")
        logger.info(f"Metabase database payload: {json.dumps(payload, indent=2)}")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://localhost:{self.config.port}/api/database",
                    json=payload,
                    headers={"X-Metabase-Session": session_token},
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()
                logger.info(f"DuckDB connection added successfully with ID: {data['id']}")
                return data["id"]
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to add DuckDB connection. Status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
            raise e

    @staticmethod
    def _generate_password(length: int = 24) -> str:
        """
        Generate a secure random password with only alphanumeric characters.
        """
        # Use a simple alphanumeric character set to avoid any potential issues
        # with special characters during authentication.
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        return password
