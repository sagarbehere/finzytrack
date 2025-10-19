"""
Transaction deletion schemas for DELETE /api/ledger/transactions endpoint.
"""
from pydantic import BaseModel, Field
from typing import List


class DeleteTransactionRequest(BaseModel):
    """Request body for deleting transactions."""
    transaction_ids: List[str] = Field(..., description="List of transaction IDs (UUIDv7) to delete", min_length=1)


class DeleteTransactionResponse(BaseModel):
    """Response after deleting transactions."""
    deleted_count: int = Field(..., description="Number of transactions successfully deleted")
    message: str = Field(..., description="Success message")
