"""`GET /health`: the process answers (failure-modes M2, M3; the M1a exit's "answers under 100 ms during a 54k load").

It does no work: no engine call, no file, no lock; only the version. What it says about the engine arrives with
BL-0094 (M2).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.envelope import Envelope, context_of, ok
from app.api.scope import declare


class HealthData(BaseModel, frozen=True):
    status: Literal["ok"]
    version: str


def health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", openapi_extra=declare("app"))
    async def health(request: Request) -> Envelope[HealthData]:
        return ok(request, HealthData(status="ok", version=context_of(request).container.app_version))

    return router
