"""Command-line entry point for the standalone MCP process."""

import uvicorn

from ..config import get_settings


def main() -> None:
    """Run the MCP ASGI application as one independently deployable process."""

    settings = get_settings()
    uvicorn.run(
        "backend.app.mcp.server:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
