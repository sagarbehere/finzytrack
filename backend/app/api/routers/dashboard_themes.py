"""Dashboard theme router — serves chart/widget color themes.

Themes are JSON files under ``config/dashboard-themes/`` (seeded from
``resources/seed_config/dashboard-themes/``). The active theme is chosen by the
``active_dashboard_theme`` config setting. See ``dev-docs/dashboard-color-system.md``.

Endpoints:
  GET  /api/dashboard-theme            — the active theme (never 404s; falls back to default)
  GET  /api/dashboard-themes           — list available themes (for the picker)
  GET  /api/dashboard-theme/{id}       — a specific theme
  PUT  /api/dashboard-theme/{id}       — write/update a theme (validates)

Namespaced under ``dashboard-theme`` so a future app-GUI theme (``/api/app-theme``)
never clashes.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from app.dependencies import get_config_manager, get_backup_manager
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.exceptions import APIError
from app.helpers.path_guard import guard_path
from app.helpers.response_helpers import success_json_response
from app.schemas.response_schemas import ApiResponse
from app.schemas.dashboard_theme_schemas import (
    DashboardTheme,
    DashboardThemeListResponse,
    DashboardThemeSummary,
    DashboardThemeWriteRequest,
    DashboardThemeWriteResponse,
    ModeColor,
    ModePalette,
    Valence,
)
from app.service_factory import SEED_CONFIG_DIR
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_THEME_ID = "dusty-spectrum"


# ── theme resolution ────────────────────────────────────────────────────────

def _user_dir(config_manager: ConfigManager) -> Path:
    return Path(config_manager.get_config().dashboard_themes_dir)


def _bundled_dir() -> Path:
    """The seed themes shipped with the app (dev tree or frozen bundle)."""
    return SEED_CONFIG_DIR / "dashboard-themes"


def _load_file(path: Path) -> Optional[DashboardTheme]:
    """Parse+validate a theme file; return None (and warn) if broken/missing."""
    if not path.is_file():
        return None
    try:
        return DashboardTheme(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        logger.warning("Ignoring invalid dashboard theme %s: %s", path, exc)
        return None


def _load_theme(config_manager: ConfigManager, theme_id: str) -> Optional[DashboardTheme]:
    """A theme by id — the user's editable copy first, then the bundled seed."""
    return (
        _load_file(_user_dir(config_manager) / f"{theme_id}.json")
        or _load_file(_bundled_dir() / f"{theme_id}.json")
    )


def _builtin_default() -> DashboardTheme:
    """Last-resort default (Dusty Spectrum) so charts never lack a theme, even
    if the bundled files are somehow missing. Kept in sync with the seed file
    ``resources/seed_config/dashboard-themes/dusty-spectrum.json``."""
    return DashboardTheme(
        id=DEFAULT_THEME_ID,
        name="Dusty Spectrum",
        description="Built-in fallback theme.",
        brand=ModeColor(light="#4f6bb0", dark="#7b93d6"),
        baseline=ModeColor(light="#9aa5b1", dark="#8a95a3"),
        valence=Valence(
            good=ModeColor(light="#4e8f66", dark="#5faf7f"),
            warn=ModeColor(light="#b8863a", dark="#d8a24a"),
            bad=ModeColor(light="#bf5b52", dark="#d97066"),
            complete=ModeColor(light="#3f8d82", dark="#56b0a4"),
        ),
        series={"budget": "{{theme.baseline}}", "actual": "{{theme.brand}}"},
        categorical=ModePalette(
            light=["#3f89c3", "#ce724d", "#32a281", "#cba243", "#826dc1", "#c96d97",
                   "#47a8af", "#7db04f", "#ae8157", "#976ec1", "#ca606b", "#6b94c1"],
            dark=["#4ba3e8", "#f5885c", "#3cc199", "#f2c150", "#9b82e6", "#ef82b4",
                  "#54c8d0", "#95d15e", "#cf9a68", "#b483e6", "#f0727f", "#7fb0e6"],
        ),
    )


def _resolve_active(config_manager: ConfigManager) -> DashboardTheme:
    """The active theme, always resolvable: active id → default id → built-in."""
    active = config_manager.get_config().active_dashboard_theme or DEFAULT_THEME_ID
    theme = _load_theme(config_manager, active)
    if theme is None and active != DEFAULT_THEME_ID:
        theme = _load_theme(config_manager, DEFAULT_THEME_ID)
    return theme or _builtin_default()


# ── endpoints ───────────────────────────────────────────────────────────────

@router.get("/dashboard-theme", response_model=ApiResponse[DashboardTheme])
async def get_active_dashboard_theme(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """The active dashboard theme. Never 404s — falls back to the default."""
    return success_json_response(_resolve_active(config_manager))


@router.get("/dashboard-themes", response_model=ApiResponse[DashboardThemeListResponse])
async def list_dashboard_themes(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Available themes (bundled ∪ user; a user copy overrides the bundled one)."""
    summaries: dict[str, DashboardThemeSummary] = {}
    for directory in (_bundled_dir(), _user_dir(config_manager)):  # user wins (later)
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                theme = _load_file(path)
                if theme is not None:
                    summaries[theme.id] = DashboardThemeSummary(
                        id=theme.id, name=theme.name, description=theme.description
                    )
    active = config_manager.get_config().active_dashboard_theme or DEFAULT_THEME_ID
    return success_json_response(
        DashboardThemeListResponse(themes=list(summaries.values()), active=active)
    )


@router.get("/dashboard-theme/{theme_id}", response_model=ApiResponse[DashboardTheme])
async def get_dashboard_theme(
    theme_id: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """A specific theme by id."""
    theme = _load_theme(config_manager, theme_id)
    if theme is None:
        raise APIError(
            f"Dashboard theme not found: {theme_id}",
            ec.DASHBOARD_THEME_NOT_FOUND,
            404,
        )
    return success_json_response(theme)


@router.put("/dashboard-theme/{theme_id}", response_model=ApiResponse[DashboardThemeWriteResponse])
async def write_dashboard_theme(
    theme_id: str,
    body: DashboardThemeWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """Write/update a theme file (validated, atomic write + backup)."""
    try:
        theme = DashboardTheme(**body.content)
    except ValidationError as exc:
        raise APIError(
            message="Dashboard theme validation failed.",
            code=ec.DASHBOARD_THEME_VALIDATION_ERROR,
            status_code=400,
            details={"validation_errors": exc.errors()},
        )
    if theme.id != theme_id:
        raise APIError(
            message=f"Theme id '{theme.id}' does not match path id '{theme_id}'.",
            code=ec.DASHBOARD_THEME_VALIDATION_ERROR,
            status_code=400,
        )

    user_dir = _user_dir(config_manager)
    target = (user_dir / f"{theme_id}.json").resolve()
    guard_path(target, user_dir, "dashboard theme path")

    user_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(body.content, indent=2) + "\n"
    with backup_manager.atomic_write(str(target)) as f:
        f.seek(0)
        f.write(content)
        f.truncate()

    logger.info("Wrote dashboard theme: %s", target)
    return success_json_response(DashboardThemeWriteResponse(path=str(target)))
