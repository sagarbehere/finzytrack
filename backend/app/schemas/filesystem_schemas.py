from typing import List, Literal, Optional

from pydantic import BaseModel


class FileEntry(BaseModel):
    """A single file or directory entry."""
    name: str
    type: Literal["file", "directory"]
    size: Optional[int] = None  # bytes, only for files


class BrowseResponse(BaseModel):
    """Directory listing response."""
    current_path: str
    parent_path: Optional[str] = None  # null at filesystem root
    home_path: str
    entries: List[FileEntry]
