"""Whitelist-based JSON logging that excludes sensitive request data."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

SAFE_LOG_FIELDS = (
    "correlation_id",
    "razorpay_event_id",
    "merchant_id",
    "event_type",
    "processing_status",
    "duration_ms",
    "job_id",
    "attempt_count",
    "error_code",
)


class StructuredJsonFormatter(logging.Formatter):
    """Serialize only explicitly approved operational fields."""

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in SAFE_LOG_FIELDS:
            value = getattr(record, field, None)
            document[field] = (
                value if isinstance(value, (int, float, bool)) or value is None else str(value)
            )
        return json.dumps(document, separators=(",", ":"), ensure_ascii=True)


def configure_structured_logging(level: str) -> None:
    """Install one application JSON handler without duplicating handlers."""

    root = logging.getLogger()
    root.setLevel(level.upper())
    if any(getattr(handler, "revenue_sre_structured", False) for handler in root.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    handler.revenue_sre_structured = True  # type: ignore[attr-defined]
    root.addHandler(handler)
