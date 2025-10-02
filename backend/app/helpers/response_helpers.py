from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional, TypeVar

from app.schemas.response_schemas import ApiResponse, ErrorInfo

T = TypeVar("T")

def create_success_response(data: T, message: Optional[str] = None) -> ApiResponse[T]:
    """Creates a standardized success response envelope."""
    return ApiResponse[T](success=True, data=data, error=None)

def create_error_response(
    message: str, code: str, details: Optional[Dict[str, Any]] = None
) -> ApiResponse:
    """Creates a standardized error response envelope."""
    error_info = ErrorInfo(code=code, message=message, details=details)
    return ApiResponse(success=False, data=None, error=error_info)

def success_json_response(
    data: Any = None, message: Optional[str] = None, status_code: int = 200
) -> JSONResponse:
    """Create a success JSONResponse with an explicit HTTP status code."""
    # Note: The message from the original success_json_response is not part of the new ApiResponse model.
    # It can be added to the data payload if needed.
    response_envelope = create_success_response(data=data)
    return JSONResponse(status_code=status_code, content=response_envelope.model_dump(mode='json'))
