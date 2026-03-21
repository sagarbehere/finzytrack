from fastapi import APIRouter
from app.state import get_registry
from app.schemas.result_schemas import ProfilesListResponse

router = APIRouter()


@router.get("/profiles", response_model=ProfilesListResponse)
async def list_profiles():
    """
    Return list of configured account profiles.
    Each profile_id is the filename without .yaml extension.
    Credentials are never included in the response.
    """
    registry = get_registry()
    return ProfilesListResponse(
        profiles=registry.list_profiles(),
        invalid_profiles=registry.list_invalid_profiles(),
    )
