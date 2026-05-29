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
        """Evict the oldest entry if cache exceeds max_users.

        Eviction is silent with respect to in-flight requests: if a handler
        is still holding a reference to the evicted ``UserServices``, Python
        will keep it alive until the handler returns. Data integrity is
        preserved across this window because ``WriteLockManager`` is wired
        with a ``portalocker`` file lock in production (see
        ``service_factory.py``) — two ``WriteLockManager`` instances for the
        same user pointing at the same sidecar lock file still serialise via
        the filesystem, so the old in-flight write and any new write from a
        post-eviction services bundle don't interleave.

        Post-CQRS there are no background tasks tied to a user
        (``shutdown_user_services`` is a no-op extension point — H2 #1), so
        eviction doesn't drop pending work either.

        Clean F1 hardening, deferred until hosted is live: refcount active
        requests per user, only evict when refcount == 0. That removes the
        intra-process ``threading.Lock`` divergence across the
        eviction-and-recreate window. Today the cost-benefit doesn't justify
        adding per-request acquire/release middleware — desktop never
        evicts, and portalocker covers the safety property hosted needs.
        """
        while len(self._cache) > self._max_users:
            oldest_id, oldest_svc = self._cache.popitem(last=False)
            self._configs.pop(oldest_id, None)
            logger.info("Evicting services for user %s (LRU)", oldest_id)
            await shutdown_user_services(oldest_svc)
