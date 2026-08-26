"""Revenue SRE FastAPI application entry point."""

from fastapi import FastAPI

from .api.health import router as health_router
from .config import get_settings


def create_app() -> FastAPI:
    """Application factory used by the server and isolated tests."""

    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Human-gated payment incident detection and revenue recovery.",
    )
    application.include_router(health_router)
    return application


app = create_app()
