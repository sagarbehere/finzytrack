from typing import Optional

from pydantic import BaseModel


class RecipeWriteRequest(BaseModel):
    content: dict


class RecipeWriteResponse(BaseModel):
    path: str
    warning: Optional[str] = None
