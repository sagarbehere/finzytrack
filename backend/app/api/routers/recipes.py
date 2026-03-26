"""
Recipes router — serves dashboard recipe JSON files from config/recipes/.

Endpoints:
  GET    /api/recipes/manifest.json         — recipe manifest
  GET    /api/recipes/{path:path}           — individual recipe file
  PUT    /api/recipes/{path:path}           — write/update a recipe file
  PUT    /api/recipes/manifest.json         — update manifest
  DELETE /api/recipes/{path:path}           — delete a recipe file + remove from manifest
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.dependencies import get_config_manager, get_backup_manager
from app.core.config_manager import ConfigManager
from app.core.backup_manager import BackupManager
from app.exceptions import APIError
from app.schemas.response_schemas import ApiResponse
from app.helpers.response_helpers import success_json_response

logger = logging.getLogger(__name__)

router = APIRouter()


class RecipeWriteRequest(BaseModel):
    content: dict


class RecipeWriteResponse(BaseModel):
    path: str
    warning: Optional[str] = None


def _recipes_dir(config_manager: ConfigManager) -> Path:
    config = config_manager.get_config()
    return Path(config.recipes_dir)


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
    """Write or update a recipe JSON file."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(recipes_path.resolve())):
        raise APIError("Invalid path", "INVALID_PATH", 400)

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
