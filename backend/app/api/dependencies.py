"""Shared API dependencies for explicit single-merchant tenant binding."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from ..config import Settings, get_settings


async def require_merchant(
    settings: Annotated[Settings, Depends(get_settings)],
    merchant_header: Annotated[str | None, Header(alias="X-Merchant-Id")] = None,
) -> UUID:
    """Require the caller's tenant to match the configured merchant.

    This is a prototype tenant boundary, not a substitute for production SSO
    or signed service credentials. It still prevents accidental cross-tenant
    object reads and keeps every repository query merchant-scoped.
    """

    if settings.merchant_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Merchant tenant binding is unavailable",
        )
    try:
        requested_merchant = UUID(merchant_header) if merchant_header else None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid merchant identifier",
        ) from error
    if requested_merchant != settings.merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Merchant is not authorized",
        )
    return requested_merchant
