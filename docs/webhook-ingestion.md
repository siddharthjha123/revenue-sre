# Razorpay webhook ingestion

## Purpose

The webhook ingestion pipeline accepts Razorpay payment events, authenticates
them, stores a privacy-reduced event history, and schedules durable asynchronous
processing. It maintains a current payment projection without treating webhook
delivery order as payment-state order.

The request path does not perform incident detection, recovery planning,
customer contact, or financial execution. Its responsibility ends after the
authenticated event and processing job are committed.

## Supported events

The first pipeline version processes these event types:

- `payment.failed`
- `payment.authorized`
- `payment.captured`

An authenticated event with another event type is retained in the webhook
history with status `ignored`. It does not create a processing job.

## Endpoint contract

```text
POST /webhooks/razorpay
```

Required headers:

| Header | Purpose |
|---|---|
| `X-Razorpay-Signature` | HMAC-SHA256 signature of the exact request bytes |
| `X-Razorpay-Event-Id` | Provider event identity used for deduplication |

The endpoint returns HTTP 200 for all successfully authenticated and persisted
deliveries. Its response body contains one of these states:

```json
{"status":"accepted"}
```

```json
{"status":"duplicate"}
```

```json
{"status":"ignored"}
```

Internal database IDs are never returned to the provider.

## Ingestion flow

```text
Razorpay
   |
   | POST raw body + signature + event ID
   v
FastAPI webhook endpoint
   |
   +-- enforce request-size limit
   +-- verify HMAC over exact raw bytes
   +-- decode and validate the envelope
   +-- verify configured merchant/account binding
   +-- retain only allowlisted payment fields
   |
   v
PostgreSQL transaction
   |
   +-- INSERT webhook_events ON CONFLICT DO NOTHING
   +-- INSERT processing_jobs ON CONFLICT DO NOTHING
   |
   v
COMMIT
   |
   +-- 200 accepted
   +-- 200 duplicate
   `-- 200 ignored for unsupported event type
```

The webhook event and its processing job are written in the same database
transaction. A supported event cannot be acknowledged as accepted with only
one of those records persisted. A database failure rolls back both writes and
returns a controlled HTTP 503 response so Razorpay can retry delivery.

## Authentication and tenant binding

The signature is computed from the exact body bytes received from Razorpay:

```text
HMAC-SHA256(webhook_secret, raw_request_body)
```

Verification uses a constant-time comparison. JSON parsing occurs only after
signature verification, which prevents a parser or serializer from changing
the signed representation.

After authentication, the webhook `account_id` must match the configured
`RAZORPAY_ACCOUNT_ID`. The event is stored under the configured `MERCHANT_ID`.
Repository reads used by the worker include the merchant ID in their predicate.
A provider event ID already owned by another merchant is treated as an identity
conflict rather than a duplicate.

The current configuration binds one Razorpay account to one merchant. A future
multi-merchant deployment requires authenticated account-to-merchant lookup;
it must not accept a merchant identifier from the webhook body as authority.

## Data minimization

The complete provider payload is not stored. Supported payment events retain
only fields needed for payment-state projection and incident evidence:

- payment and order IDs
- amount in currency subunits and currency
- status, method, captured, and international flags
- bank and wallet identifiers when present
- payment error code, description, source, step, and reason
- provider creation timestamp

Customer email, contact number, card details, VPA, notes, and other unneeded
provider fields are excluded by allowlist construction. Unsupported event types
retain only envelope metadata and no nested provider payload.

The event stores a SHA-256 hash of the original raw body for integrity and
diagnostic correlation without retaining the original sensitive body.

## Idempotency and concurrency

`webhook_events.razorpay_event_id` is unique. The repository uses
`INSERT ... ON CONFLICT DO NOTHING`, then resolves the existing record. This
keeps duplicate delivery safe even when two application instances receive the
same event concurrently.

`processing_jobs.webhook_event_id` is also unique. Therefore:

```text
one Razorpay event ID -> one webhook history record -> at most one job
```

A duplicate request returns HTTP 200 with `duplicate`. It does not enqueue a
second job and does not repeat payment processing.

## Durable processing jobs

The queue is stored in PostgreSQL rather than process memory. Available jobs
are claimed with a row lock using:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
```

This permits multiple workers to claim different jobs without blocking each
other. A claim records:

- worker identity
- random lease token
- lease expiry
- incremented attempt count

The claim transaction is short. Business processing occurs in a separate
transaction and must present the same unexpired lease token before completing
or failing the job. A worker that stalls beyond its lease cannot overwrite the
result of a worker that reclaimed the job.

Job states are:

| Status | Meaning |
|---|---|
| `pending` | Available for its first processing attempt |
| `processing` | Temporarily leased by a worker |
| `retry_scheduled` | Retryable failure waiting for its next attempt |
| `succeeded` | Processing and event-state updates committed |
| `dead_letter` | Permanent failure or retry limit exhausted |

Retry delay uses bounded exponential backoff:

```text
min(base_seconds * 2^(attempt_count - 1), cap_seconds)
```

Permanent normalization errors are dead-lettered immediately. Unexpected
runtime failures are retryable until `WORKER_MAX_ATTEMPTS` is reached. Only
sanitized error codes and messages are persisted.

`WebhookJobWorker.run_once()` currently claims and processes at most one job.
The repository does not yet include a continuous worker process entrypoint;
deployment must not assume that importing the FastAPI application starts the
worker.

## Payment normalization

The worker maps each supported event into the current `payment_attempts`
projection:

| Razorpay field | Payment projection field |
|---|---|
| `id` | `payment_id` |
| `order_id` | `order_id` |
| `amount` | `amount_subunits` |
| `currency` | `currency` |
| `status` | `status` |
| `method` | `method` |
| `captured` | `captured` |
| `international` | `international` |
| `error_code` | `error_code` |
| `error_description` | `error_description` |
| `error_source` | `error_source` |
| `error_step` | `error_step` |
| `error_reason` | `error_reason` |
| `created_at` | `provider_created_at` |

Unknown payment methods normalize to `other`. Unknown error sources normalize
to `unknown`. A new provider enum value therefore does not crash the worker.

The `webhook_events` table remains the complete sanitized event history. The
`payment_attempts` table is only the latest accepted state for each
merchant/payment pair.

## Payment ordering rules

Webhook arrival order is not trusted. Each event records the provider event
timestamp separately from local receipt time. The payment projection records
the provider timestamp of the event that last changed its state.

Supported forward transitions are:

```text
created    -> failed
created    -> authorized
created    -> captured
failed     -> authorized
failed     -> captured
authorized -> captured
```

The state machine applies these rules:

- A valid forward transition may apply even when delivery is delayed.
- The same status applies only when its provider event timestamp is newer.
- An older same-status event is stale and does not update the projection.
- A backward transition is rejected as a regression.
- A repeated Razorpay event ID is ignored as a replay.

Consequently, a delayed `payment.failed` event cannot change a captured payment
back to failed, while both events remain available in webhook history.

## Failure responses

| Condition | HTTP status | Persistence |
|---|---:|---|
| Body exceeds configured limit | 413 | Nothing stored |
| Missing signature | 400 | Nothing stored |
| Invalid signature | 401 | Nothing stored |
| Webhook authentication not configured | 503 | Nothing stored |
| Missing or invalid event ID | 400 | Nothing stored |
| Signed malformed JSON | 400 | Nothing stored |
| Invalid webhook envelope | 400 | Nothing stored |
| Merchant/account binding unavailable | 503 | Nothing stored |
| Razorpay account mismatch | 403 | Nothing stored |
| Supported event missing payment data | 400 | Nothing stored |
| Event ID belongs to another merchant | 409 | Nothing changed |
| Database transaction failure | 503 | Transaction rolled back |
| Authenticated duplicate | 200 | Existing records retained |
| Authenticated unsupported event | 200 | Event stored as `ignored`; no job |

Provider payloads, credentials, and customer data are not included in error
responses.

## Observability

Every request receives a UUID correlation ID. A valid incoming
`X-Correlation-ID` is preserved; otherwise the service generates one. The ID
is returned in the response, stored with the webhook event, and reused by the
worker logs.

Structured logging uses an explicit field allowlist:

- `correlation_id`
- `razorpay_event_id`
- `merchant_id`
- `event_type`
- `processing_status`
- `duration_ms`
- `job_id`
- `attempt_count`
- `error_code`

Logs must not contain webhook secrets, API keys, authorization headers,
customer email/contact, full recovery URLs, or raw provider payloads.

Prometheus metrics are exposed at `/metrics`:

- `webhooks_received_total`
- `webhooks_duplicate_total`
- `webhooks_invalid_signature_total`
- `webhook_processing_failures_total`
- `webhook_processing_duration_seconds`

`/metrics` is excluded from the public OpenAPI schema and must be restricted to
the private operational network at deployment.

## Configuration

Relevant environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy PostgreSQL connection URL |
| `RAZORPAY_WEBHOOK_SECRET` | Secret used only for webhook HMAC verification |
| `MERCHANT_ID` | Internal merchant UUID for the current single-tenant binding |
| `RAZORPAY_ACCOUNT_ID` | Expected Razorpay account identifier |
| `WEBHOOK_MAX_BODY_BYTES` | Maximum accepted request-body size |
| `WORKER_MAX_ATTEMPTS` | Maximum processing attempts per job |
| `WORKER_LEASE_SECONDS` | Duration of exclusive worker ownership |
| `WORKER_RETRY_BASE_SECONDS` | Initial retry delay |
| `WORKER_RETRY_CAP_SECONDS` | Maximum retry delay |

Secrets belong in the local `.env` file or the deployment secret manager. The
local `.env` file must never be committed.

## Local verification

Run commands from the repository root:

```powershell
docker compose up -d db
python -m alembic upgrade head
python -m alembic check
python -m ruff format --check .
python -m ruff check .
python -m pytest -v
```

The automated suite verifies signature handling, raw-byte authentication,
malformed payload rejection, duplicate and concurrent duplicate delivery,
tenant isolation, normalization, unknown payment methods, state ordering,
worker retry/dead-letter behavior, safe logging, metrics, and controlled
database failures.

## Current boundary

This pipeline provides authenticated ingestion and durable payment-state
projection. It does not yet provide:

- a continuous worker command or service definition
- payment-failure aggregation and incident creation
- root-cause hypothesis generation
- recovery plan generation
- merchant approval persistence
- Razorpay recovery execution
- recovered-revenue attribution

Those capabilities build on the trusted event history and current payment
projection produced here.
