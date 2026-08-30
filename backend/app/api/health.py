"""Operational health endpoint.

The endpoint intentionally checks only process liveness for now. Database and
external-provider readiness checks will be added when those adapters exist.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["operations"])


class HealthResponse(BaseModel):
    """Stable response contract for probes and CI smoke tests."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    service: str = "revenue-sre"


@router.get("/health", response_model=HealthResponse, summary="Check process liveness")
async def health() -> HealthResponse:
    """Return success when the FastAPI process can serve requests."""

    return HealthResponse()
