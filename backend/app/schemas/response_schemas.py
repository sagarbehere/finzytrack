from pydantic import BaseModel, Field
from typing import TypeVar, Generic, Optional, Dict, Any

T = TypeVar('T')

class ErrorInfo(BaseModel):
    code: str = Field(..., description="A machine-readable error code.")
    message: str = Field(..., description="A human-readable error message.")
    details: Optional[Dict[str, Any]] = Field(None, description="Optional structured error details.")

class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[ErrorInfo] = None
