"""
Recipes router — serves dashboard recipe JSON files from config/recipes/.

Endpoints:
  GET    /api/recipes/manifest.json           — recipe manifest
  GET    /api/recipes/{path:path}             — individual recipe file (parsed JSON)
  GET    /api/recipes/{path:path}/raw         — individual recipe file (raw text)
  PUT    /api/recipes/{path:path}             — write/update a recipe file (validates + updates manifest)
  DELETE /api/recipes/{path:path}             — delete a recipe file + remove from manifest
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_config_manager, get_backup_manager
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.exceptions import APIError
from app.helpers.recipe_validation import (
    validate_dashboard,
    validate_id,
    validate_widget,
)
from app.schemas.recipe_schemas import RecipeWriteRequest, RecipeWriteResponse
from app.schemas.rule_write_schemas import RuleContentResponse
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

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
            code="VALIDATION_ERROR",
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


def _update_manifest_add(recipes_path: Path, file_path: str, recipe_type: str) -> Optional[str]:
    """Add file_path to manifest if not already present. Returns warning on failure."""
    manifest_path = recipes_path / "manifest.json"
    manifest_key = "widgets" if recipe_type == "widget" else "dashboards"

    try:
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {"widgets": [], "dashboards": []}

        if file_path not in manifest.get(manifest_key, []):
            manifest.setdefault(manifest_key, []).append(file_path)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            logger.info(f"Added '{file_path}' to manifest")
    except Exception as e:
        logger.error(f"Failed to update manifest: {e}")
        return f"Recipe saved but manifest update failed: {e}"
    return None


@router.get("/recipes/manifest.json")
async def get_manifest(
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Return the recipe manifest."""
    recipes_path = _recipes_dir(config_manager)
    manifest_path = recipes_path / "manifest.json"
    if not manifest_path.is_file():
        raise APIError("No recipe manifest found", "RECIPE_NOT_FOUND", 404)
    return JSONResponse(content=json.loads(manifest_path.read_text(encoding="utf-8")))


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

    if not str(target).startswith(str(recipes_path.resolve())):
        raise APIError("Invalid path", "INVALID_PATH", 400)

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
    if not str(target).startswith(str(recipes_path.resolve())):
        raise APIError("Invalid path", "INVALID_PATH", 400)

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
    """Write or update a recipe JSON file. Validates content and updates manifest."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(recipes_path.resolve())):
        raise APIError("Invalid path", "INVALID_PATH", 400)

    # Determine type and validate
    recipe_type = _recipe_type_from_path(file_path)
    errors = _validate_recipe(body.content, recipe_type)
    if errors:
        raise APIError(
            message=f"{'Widget' if recipe_type == 'widget' else 'Dashboard'} recipe validation failed.",
            code="VALIDATION_ERROR",
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

    # Update manifest
    warning = _update_manifest_add(recipes_path, file_path, recipe_type)

    return success_json_response(RecipeWriteResponse(path=str(target), warning=warning))


@router.delete("/recipes/{file_path:path}", response_model=ApiResponse[RecipeWriteResponse])
async def delete_recipe_file(
    file_path: str,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Delete a recipe file and remove it from the manifest."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(recipes_path.resolve())):
        raise APIError("Invalid path", "INVALID_PATH", 400)

    if not target.is_file():
        raise APIError(f"Recipe file not found: {file_path}", "RECIPE_NOT_FOUND", 404)

    # Delete the file
    target.unlink()
    logger.info(f"Deleted recipe file: {target}")

    # Remove from manifest
    warning = None
    manifest_path = recipes_path / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            changed = False
            for key in ("widgets", "dashboards"):
                if file_path in manifest.get(key, []):
                    manifest[key].remove(file_path)
                    changed = True
            if changed:
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                logger.info(f"Removed '{file_path}' from manifest")
        except Exception as e:
            logger.error(f"Failed to update manifest after delete: {e}")
            warning = f"File deleted but manifest update failed: {e}"

    return success_json_response(RecipeWriteResponse(path=str(target), warning=warning))
