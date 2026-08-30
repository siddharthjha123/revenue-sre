"""Low-cardinality Prometheus metrics for webhook ingestion and processing."""

from prometheus_client import Counter, Gauge, Histogram

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
WORKER_POLLS_TOTAL = Counter(
    "worker_polls_total",
    "Total durable queue polling iterations by outcome.",
    ("outcome",),
)
WORKER_JOBS_PROCESSED_TOTAL = Counter(
    "worker_jobs_processed_total",
    "Total claimed durable jobs by final processing outcome.",
    ("outcome",),
)
WORKER_LOOP_ERRORS_TOTAL = Counter(
    "worker_loop_errors_total",
    "Total unexpected failures outside individual durable job handling.",
)
WORKER_LAST_SUCCESS_TIMESTAMP_SECONDS = Gauge(
    "worker_last_success_timestamp_seconds",
    "Unix timestamp of the most recent successfully processed durable job.",
)
WORKER_RUNTIME_UP = Gauge(
    "worker_runtime_up",
    "Whether this process currently has an active durable worker loop.",
)
