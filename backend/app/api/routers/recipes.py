"""
Recipes router — serves dashboard recipe JSON files from config/recipes/.

Endpoints:
  GET  /api/recipes/manifest.json         — recipe manifest
  GET  /api/recipes/{path:path}           — individual recipe file
  PUT  /api/recipes/{path:path}           — write/update a recipe file
  PUT  /api/recipes/manifest.json         — update manifest
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.dependencies import get_config_manager
from app.core.config_manager import ConfigManager

logger = logging.getLogger(__name__)

router = APIRouter()


class RecipeWriteRequest(BaseModel):
    content: dict


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
        raise HTTPException(status_code=404, detail="No recipe manifest found")
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
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Recipe file not found: {file_path}")

    return JSONResponse(content=json.loads(target.read_text(encoding="utf-8")))


@router.put("/recipes/{file_path:path}")
async def write_recipe_file(
    file_path: str,
    body: RecipeWriteRequest,
    config_manager: ConfigManager = Depends(get_config_manager),
):
    """Write or update a recipe JSON file."""
    recipes_path = _recipes_dir(config_manager)
    target = (recipes_path / file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(recipes_path.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body.content, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Wrote recipe file: {target}")

    return {"success": True, "path": str(target)}
