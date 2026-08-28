"""Low-cardinality Prometheus metrics for webhook ingestion and processing."""

from prometheus_client import Counter, Histogram

WEBHOOKS_RECEIVED_TOTAL = Counter(
    "webhooks_received_total",
    "Total Razorpay webhook requests received before authentication.",
)
WEBHOOKS_DUPLICATE_TOTAL = Counter(
    "webhooks_duplicate_total",
    "Total authenticated duplicate Razorpay webhook deliveries.",
)
WEBHOOKS_INVALID_SIGNATURE_TOTAL = Counter(
    "webhooks_invalid_signature_total",
    "Total Razorpay webhook requests with missing or invalid signatures.",
)
WEBHOOK_PROCESSING_FAILURES_TOTAL = Counter(
    "webhook_processing_failures_total",
    "Total claimed webhook jobs that failed processing.",
)
WEBHOOK_PROCESSING_DURATION_SECONDS = Histogram(
    "webhook_processing_duration_seconds",
    "Duration of claimed webhook job processing.",
)
