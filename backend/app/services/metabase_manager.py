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
        self.config = config
        self.duckdb_path = duckdb_path
        self.config_manager = config_manager
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[datetime] = None

    # =====================================
    # Public API
    # =====================================

    async def start(self) -> Dict[str, Any]:
        if self.is_running():
            raise APIError(message="Metabase is already running", code="METABASE_ALREADY_RUNNING", status_code=409)
        return await self._start_process()

    async def stop(self) -> Dict[str, Any]:
        if not self.is_running() or self.process is None:
            raise APIError(message="Metabase is not running", code="METABASE_NOT_RUNNING", status_code=409)
        return await self._stop_process()

    async def reset(self) -> Dict[str, Any]:
        """Performs a factory reset of the Metabase instance."""
        if self.is_running():
            await self._stop_process()
        
        db_file = Path(self.config.data_dir) / "metabase.db.mv.db"
        if db_file.exists():
            logger.info(f"Deleting Metabase database file: {db_file}")
            db_file.unlink()
        
        self.config_manager.reset_metabase_state()
        logger.info("Metabase configuration has been reset.")
        
        return {"reset_successful": True, "timestamp": datetime.utcnow().isoformat() + "Z"}

    async def get_status(self) -> Dict[str, Any]:
        await self._ensure_metabase_is_running()
        running = self.is_running()
        healthy = await self.is_healthy() if running else False
        uptime_seconds = int((datetime.utcnow() - self.start_time).total_seconds()) if running and self.start_time else 0
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
        await self._ensure_metabase_is_running()

        if self.config.initialized:
            logger.info("Checking existing Metabase initialization...")
            try:
                await self._api_request("GET", f"/api/database/{self.config.database_id}")
                logger.info("Metabase is already initialized and configuration is valid.")
                return {"status": "already_initialized"}
            except APIError as e:
                logger.warning(f"Metabase is marked as initialized, but health check failed ({e.code}). Proceeding with re-initialization.")
                self.config_manager.reset_metabase_state()

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
                "dashboards_imported": 0
            }
        except Exception as e:
            logger.error(f"Metabase initialization failed: {e}", exc_info=True)
            raise APIError(message=f"Failed to initialize Metabase: {str(e)}", code="METABASE_INIT_FAILED", status_code=500)

    async def trigger_schema_refresh(self) -> Dict[str, Any]:
        await self._ensure_metabase_is_running()
        if not self.config.initialized or not self.config.database_id:
            raise APIError(message="Metabase not initialized or DuckDB not connected", code="METABASE_NOT_CONFIGURED", status_code=409)

        await self._api_request("POST", f"/api/database/{self.config.database_id}/sync_schema")
        logger.info(f"Triggered schema refresh for database {self.config.database_id}")
        return {"synced_at": datetime.utcnow().isoformat() + "Z", "database_id": self.config.database_id}

    # =====================================
    # Lifecycle & API Helpers
    # =====================================

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    async def is_healthy(self) -> bool:
        if not self.is_running(): return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://localhost:{self.config.port}/api/health", timeout=5.0)
                return response.status_code == 200
        except Exception: return False

    async def _start_process(self) -> Dict[str, Any]:
        # Identical to previous implementation
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

    async def _stop_process(self) -> Dict[str, Any]:
        assert self.process is not None
        process = self.process
        logger.info(f"Stopping Metabase (PID {process.pid})")
        uptime_seconds = int((datetime.utcnow() - self.start_time).total_seconds()) if self.start_time else 0
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

    async def _ensure_metabase_is_running(self):
        if not self.is_running() and self.config.auto_start:
            logger.info("Metabase is not running. Attempting to auto-start...")
            await self._start_process()

    async def _wait_for_health(self, timeout: int = 120):
        # Identical to previous implementation
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
        if self.process:
            await asyncio.to_thread(self.process.wait)

    async def _login(self) -> str:
        logger.info("Attempting to log in to Metabase to get a new session token.")
        payload = {"username": self.config.admin_email, "password": self.config.admin_password}
        async with httpx.AsyncClient() as client:
            response = await client.post(f"http://localhost:{self.config.port}/api/session", json=payload, timeout=10.0)
            response.raise_for_status()
            return response.json()["id"]

    async def _api_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        if not self.config.session_token:
            raise APIError(message="Metabase session token is not available.", code="METABASE_NOT_CONFIGURED")

        headers = {"X-Metabase-Session": self.config.session_token, **kwargs.pop("headers", {})}
        url = f"http://localhost:{self.config.port}{endpoint}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(method, url, headers=headers, timeout=30.0, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.warning("Metabase API call failed with 401. Token may be expired or invalid.")
                    try:
                        logger.info("Attempting to refresh token by logging in again...")
                        new_token = await self._login()
                        self.config_manager.save_metabase_state({"session_token": new_token})
                        logger.info("Retrying API request with new session token.")
                        headers["X-Metabase-Session"] = new_token
                        response = await client.request(method, url, headers=headers, timeout=30.0, **kwargs)
                        response.raise_for_status()
                        return response
                    except httpx.HTTPStatusError as login_e:
                        if login_e.response.status_code == 401:
                            logger.error("Re-login failed. The stored password may be incorrect or the user may not exist. Soft-resetting config.")
                            self.config_manager.reset_metabase_state()
                            raise APIError(message="Metabase configuration was out of sync and has been reset. Please initialize again.", code="METABASE_CONFIG_RESET", status_code=500)
                        else:
                            raise login_e
                else:
                    raise

    async def _get_setup_token(self) -> str:
        # Identical to previous implementation
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://localhost:{self.config.port}/api/session/properties", timeout=10.0)
            response.raise_for_status()
            return response.json()["setup-token"]

    async def _create_admin_account(self, setup_token: str, password: str) -> str:
        # Identical to previous implementation
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
        # Identical to previous implementation
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
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
