"""Revenue SRE FastAPI application entry point."""

from fastapi import FastAPI

from .api.health import router as health_router
from .api.metrics import router as metrics_router
from .api.razorpay_webhooks import router as razorpay_webhook_router
from .config import get_settings
from .observability.context import CorrelationIdMiddleware
from .observability.logging import configure_structured_logging


def create_app() -> FastAPI:
    """Application factory used by the server and isolated tests."""

    settings = get_settings()
    configure_structured_logging(settings.log_level)
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Human-gated payment incident detection and revenue recovery.",
    )
    application.add_middleware(CorrelationIdMiddleware)
    application.include_router(health_router)
    application.include_router(metrics_router)
    application.include_router(razorpay_webhook_router)
    return application


app = create_app()
