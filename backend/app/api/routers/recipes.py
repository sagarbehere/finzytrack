"""
Recipes router — serves dashboard recipe JSON files from config/recipes/.

Endpoints:
  GET    /api/recipes/manifest.json           — auto-discovered list of recipe files
  GET    /api/recipes/{path:path}             — individual recipe file (parsed JSON)
  GET    /api/recipes/{path:path}/raw         — individual recipe file (raw text)
  PUT    /api/recipes/{path:path}             — write/update a recipe file (validates content)
  DELETE /api/recipes/{path:path}             — delete a recipe file

The manifest is auto-discovered from the filesystem on every request:
any `widgets/*.json` or `dashboards/*.json` under the recipes directory
is treated as a recipe. There is no on-disk manifest to maintain.
Per-file validation happens at the GET endpoint (and on the frontend
loader); broken files surface as errors against the file path.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_config_manager, get_backup_manager
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.exceptions import APIError
from app.helpers.path_guard import guard_path
from app.helpers.recipe_validation import (
    validate_dashboard,
    validate_id,
    validate_widget,
)
from app.schemas.recipe_schemas import RecipeWriteRequest, RecipeWriteResponse
from app.schemas.rule_write_schemas import RuleContentResponse
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response
from app import error_codes as ec

logger = logging.getLogger(__name__)

router = APIRouter()


def _recipes_dir(config_manager: ConfigManager) -> Path:
    config = config_manager.get_config()
    return Path(config.recipes_dir)


def _recipe_type_from_path(file_path: str) -> str:
    """Determine recipe type ('widget' or 'dashboard') from the file path prefix."""
    if file_path.startswith("widgets/"):
        return "widget"
    elif file_path.startswith("dashboards/"):
        return "dashboard"
    else:
        raise APIError(
            message="Recipe path must start with 'dashboards/' or 'widgets/'.",
            code=ec.VALIDATION_ERROR,
            status_code=400,
            details={"file_path": file_path},
        )


def _validate_recipe(content: dict, recipe_type: str) -> list[str]:
    """Run structural validation on a recipe dict. Returns list of error strings."""
    if recipe_type == "widget":
        errors = validate_widget(content, "(root)")
    else:
        errors = validate_dashboard(content)

    recipe_id = content.get("id")
    if isinstance(recipe_id, str):
        errors.extend(validate_id(recipe_id))

    return errors


def _discover_recipes(recipes_path: Path) -> dict[str, list[str]]:
    """Glob the recipes directory for widget and dashboard files.

    Returns paths relative to recipes_path, sorted alphabetically. Files
    that fail to parse or validate are still returned — the frontend (and
    the per-recipe GET endpoint) surface their errors against the path.
    """
    def _scan(subdir: str) -> list[str]:
        dir_path = recipes_path / subdir
        if not dir_path.is_dir():
            return []
        files = sorted(p.name for p in dir_path.glob("*.json"))
        return [f"{subdir}/{name}" for name in files]

    return {
        "widgets": _scan("widgets"),
        "dashboards": _scan("dashboards"),
    }


@router.get("/recipes/manifest.json")
async def get_manifest(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Return the auto-discovered recipe manifest.

    Any `widgets/*.json` and `dashboards/*.json` under the recipes
    directory is included. Paths are sorted alphabetically; the manifest
    is recomputed on every request, so files added by `cp`, `mv`, or any
    other out-of-band write are picked up immediately.
    """
    recipes_path = _recipes_dir(config_manager)
    return JSONResponse(content=_discover_recipes(recipes_path))


@router.get("/recipes/{file_path:path}/raw",
            response_model=ApiResponse[RuleContentResponse],
            operation_id="getRecipeRaw")
async def get_recipe_file_raw(
    file_path: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Return raw text content of a recipe JSON file."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    guard_path(target, recipes_path, "recipe path")

    if not target.is_file():
        raise APIError(f"Recipe file not found: {file_path}", "RECIPE_NOT_FOUND", 404)

    content = target.read_text(encoding="utf-8")
    return success_json_response(RuleContentResponse(filename=file_path, content=content))


@router.get("/recipes/{file_path:path}")
async def get_recipe_file(
    file_path: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Return an individual recipe JSON file."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    guard_path(target, recipes_path, "recipe path")

    if not target.is_file():
        raise APIError(f"Recipe file not found: {file_path}", "RECIPE_NOT_FOUND", 404)

    return JSONResponse(content=json.loads(target.read_text(encoding="utf-8")))


@router.put("/recipes/{file_path:path}", response_model=ApiResponse[RecipeWriteResponse])
async def write_recipe_file(
    file_path: str,
    body: RecipeWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
    backup_manager: BackupManager = Depends(get_backup_manager),
):
    """Write or update a recipe JSON file. Validates content; existing files
    are atomically replaced via the backup manager (timestamped backup + temp
    file + atomic rename); new files are written directly."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    guard_path(target, recipes_path, "recipe path")

    # Determine type and validate
    recipe_type = _recipe_type_from_path(file_path)
    errors = _validate_recipe(body.content, recipe_type)
    if errors:
        raise APIError(
            message=f"{'Widget' if recipe_type == 'widget' else 'Dashboard'} recipe validation failed.",
            code=ec.VALIDATION_ERROR,
            status_code=400,
            details={"validation_errors": errors},
        )

    # Write file
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(body.content, indent=2) + "\n"
    if target.exists():
        with backup_manager.atomic_write(str(target)) as f:
            f.seek(0)
            f.write(content)
            f.truncate()
    else:
        target.write_text(content, encoding="utf-8")

    logger.info(f"Wrote recipe file: {target}")

    return success_json_response(RecipeWriteResponse(path=str(target)))


@router.delete("/recipes/{file_path:path}", response_model=ApiResponse[RecipeWriteResponse])
async def delete_recipe_file(
    file_path: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Delete a recipe file. (Auto-discovery picks up the removal on the next manifest fetch.)"""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    guard_path(target, recipes_path, "recipe path")

    if not target.is_file():
        raise APIError(f"Recipe file not found: {file_path}", "RECIPE_NOT_FOUND", 404)

    # Delete the file
    target.unlink()
    logger.info(f"Deleted recipe file: {target}")

    return success_json_response(RecipeWriteResponse(path=str(target)))
