"""Request correlation that survives concurrent asynchronous execution."""

from contextvars import ContextVar
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_correlation_id: ContextVar[UUID | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> UUID:
    """Return the active request ID, or create one for non-HTTP callers."""

    correlation_id = _correlation_id.get()
    if correlation_id is None:
        correlation_id = uuid4()
        _correlation_id.set(correlation_id)
    return correlation_id


class CorrelationIdMiddleware:
    """Validate or create ``X-Correlation-ID`` and return it to the caller."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-correlation-id", b"").decode("ascii", errors="ignore")
        try:
            correlation_id = UUID(supplied)
        except (ValueError, AttributeError):
            correlation_id = uuid4()

        token = _correlation_id.set(correlation_id)

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-correlation-id", str(correlation_id).encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_correlation)
        finally:
            _correlation_id.reset(token)
