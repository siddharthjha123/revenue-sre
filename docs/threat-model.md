# Threat model

## Scope and assets

Protected assets are Razorpay credentials, webhook secrets, merchant/customer
payment metadata, tenant isolation, approval records, audit history, recovery
links, and the merchant's money and reputation. Razorpay remains the payment
source of truth.

## Security objectives

- No action without authenticated merchant authority and exact-plan approval.
- No duplicate collection, recovery of an already-paid order, or excess contact.
- No cross-merchant reads or writes.
- Every material decision is attributable, explainable, and tamper-evident.
- Model or orchestration failure results in no financial action.

## Threats and required controls

| Threat | Impact | Required control |
|---|---|---|
| Committed API/webhook secret | Account compromise | `.env` ignored, secret scanning, CI/repository secrets, rotation procedure |
| Forged webhook | False incident or action | HMAC verification on raw body; reject before JSON processing |
| Replayed/duplicate event | Duplicate plan/action | Unique provider event ID and idempotent ingestion transaction |
| Cross-merchant access | Data breach or unauthorized action | Authenticated tenant ID; tenant predicate on every query and tool call |
| Amount unit confusion | 100× over-collection | Integer `amount_subunits`, currency field, UI formatting at boundary |
| Prompt injection in payment notes | Tool misuse/data leak | Treat provider text as quoted data; structured output; no secret access |
| Hallucinated root cause | Wrong intervention | Separate facts, metrics and hypotheses; evidence; confidence; human review |
| Over-privileged MCP agent | Unauthorized Razorpay write | Separate investigator/executor resources; tool allowlist; deterministic policy |
| Approval changed after consent | Unauthorized scope | Canonical plan serialization and SHA-256 hash with immutable approval |
| Payment succeeds before retry | Double collection | Live status recheck immediately before action; exclude paid payment |
| Duplicate execution | Multiple links/messages | Idempotency key per action; unique database constraint; retry-safe executor |
| Customer harassment | Reputation/compliance harm | Contact cap, cooldown, consent rules; stop after payment or opt-out |
| Sandbox escape/resource abuse | Host or secret compromise | No secrets/network by default; CPU, memory and time limits |
| Audit modification | False recovery claims | Append-only records, correlation IDs, restricted writer, log export |
| Model/provider outage | Workflow unavailable | Timeout/circuit breaker, retry reads, deterministic manual fallback |
| Stale MCP connector | Accidental use | Unique names; inventory; never attach stale connector to agents |

## Control implementation status

The service currently validates payment, incident, recovery, approval and audit
contracts. Recovery execution is disabled by default through
`EXECUTION_ENABLED=false`.

The following controls are part of the implementation roadmap:

- Signed webhook verification and replay protection
- Merchant authentication and tenant-scoped persistence
- Atomic action idempotency and live payment-status rechecks
- Immutable approval storage and append-only audit persistence
- Separate read-only investigation and allowlisted execution identities
- Resource-limited analysis sandbox and external-provider circuit breakers

## Financial execution invariants

- A recovery action references exactly one approved plan version.
- The approved plan hash matches the plan presented for execution.
- The plan has not expired and remains within its amount and contact limits.
- The payment is still recoverable immediately before the provider call.
- Repeated delivery of the same command produces at most one external action.
- Execution and verification results are appended to the audit history.
