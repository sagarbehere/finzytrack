from fastapi import APIRouter
from app.state import get_registry

router = APIRouter()


@router.get("/profiles")
async def list_profiles():
    """
    Return list of configured account profiles.
    Each profile_id is the filename without .yaml extension.
    Credentials are never included in the response.
    """
    registry = get_registry()
    profiles = registry.list_profiles()
    invalid_profiles = registry.list_invalid_profiles()
    return {
        "profiles": [p.model_dump() for p in profiles],
        "invalid_profiles": [p.model_dump() for p in invalid_profiles],
    }
