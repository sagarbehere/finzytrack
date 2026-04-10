"""
ServiceRegistry — per-user ``UserServices`` cache with LRU eviction.

Desktop mode: one pre-registered entry (``"local"``).
Hosted mode:  lazily creates and caches per-user service sets; evicts the
              least-recently-used when the cache exceeds ``max_users``.
"""

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from app.app_mode import AppMode, UserContext
from app.config import Config
from app.service_factory import (
    create_user_services,
    seed_config,
    startup_user_services,
    shutdown_user_services,
)
from app.user_services import UserServices

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Manages per-user ``UserServices`` instances with LRU eviction."""

    def __init__(
        self,
        mode: AppMode,
        base_dir: Path,
        max_users: int = 50,
    ) -> None:
        self._mode = mode
        self._base_dir = base_dir
        self._max_users = max_users
        self._cache: OrderedDict[str, UserServices] = OrderedDict()
        self._configs: dict[str, Config] = {}
        self._lock = asyncio.Lock()

    # ── Public API ──────────────────────────────────────────────────────────

    async def get_or_create(self, ctx: UserContext) -> UserServices:
        """Return cached services for *ctx.user_id*, creating them if needed."""
        async with self._lock:
            if ctx.user_id in self._cache:
                self._cache.move_to_end(ctx.user_id)
                return self._cache[ctx.user_id]

            # Provision directory + create services (hosted mode)
            services, config = self._create_for_user(ctx)
            await startup_user_services(services, config)

            self._cache[ctx.user_id] = services
            self._configs[ctx.user_id] = config

            # Evict oldest if over capacity
            await self._evict_if_needed()

            return services

    def pre_register(
        self,
        user_id: str,
        services: UserServices,
        config: Config,
    ) -> None:
        """Register pre-built services (used in desktop mode at startup)."""
        self._cache[user_id] = services
        self._configs[user_id] = config

    async def shutdown_all(self) -> None:
        """Shut down all cached user services (called at app shutdown)."""
        for user_id in list(self._cache):
            logger.info("Shutting down services for user %s", user_id)
            await shutdown_user_services(self._cache[user_id])
        self._cache.clear()
        self._configs.clear()

    # ── Internal ────────────────────────────────────────────────────────────

    def _create_for_user(self, ctx: UserContext) -> tuple[UserServices, Config]:
        """Provision (if needed) and create services for a user."""
        if self._mode == AppMode.HOSTED:
            self._provision_user(ctx)

        config_path = ctx.root_dir / "config" / "config.yaml"
        config = Config.from_yaml_file(str(config_path), root_dir=ctx.root_dir)

        logger.info(
            "Creating services for user %s (root=%s)", ctx.user_id, ctx.root_dir
        )
        services = create_user_services(config, user_id=ctx.user_id)
        return services, config

    def _provision_user(self, ctx: UserContext) -> None:
        """Seed config directory for a brand-new hosted user."""
        config_dir = ctx.root_dir / "config"
        if not config_dir.exists():
            ctx.root_dir.mkdir(parents=True, exist_ok=True)
            seed_config(config_dir)
            logger.info("Provisioned new user directory: %s", ctx.root_dir)

    async def _evict_if_needed(self) -> None:
        """Evict the oldest entry if cache exceeds max_users."""
        while len(self._cache) > self._max_users:
            oldest_id, oldest_svc = self._cache.popitem(last=False)
            self._configs.pop(oldest_id, None)
            logger.info("Evicting services for user %s (LRU)", oldest_id)
            await shutdown_user_services(oldest_svc)
