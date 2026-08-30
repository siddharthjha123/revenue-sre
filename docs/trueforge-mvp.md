# TrueForge MVP

Revenue SRE is an approval-first payment incident commander. The MVP detects a
payment failure spike without an LLM, exposes exact evidence to a TrueForge
agent, verifies the evidence, and lets the agent persist a bounded recovery
proposal. Merchant approval is immutable and separate from agent authority.

## Demonstrated flow

```text
Razorpay-format test webhooks
        |
        v
FastAPI webhook inbox -----> durable PostgreSQL worker
                                  |
                                  v
                         deterministic incident
                                  |
                                  v
TrueForge/Qwen --------> Revenue SRE MCP
                         1. list incident
                         2. get evidence
                         3. verify evidence
                         4. create bounded proposal
                                  |
                                  v
                      pending merchant approval
                                  |
                    approve or reject via REST
                                  |
                                  v
                      immutable audit timeline
```

Proposal creation is not recovery execution. The MCP server derives payment
IDs, amounts, currency, evidence IDs, contact limit, expiry, and merchant
identity from trusted server-side state. The model supplies only the proposed
action type and an evidence-backed rationale.

## MCP tools

| Tool | Mutability | Purpose |
| --- | --- | --- |
| `list_open_incidents` | Read-only | Select an actionable incident by risk. |
| `get_incident_evidence` | Read-only | Retrieve PII-filtered facts and metrics. |
| `verify_incident_evidence` | Read-only | Recalculate metrics when Daytona is unavailable. |
| `create_bounded_recovery_proposal` | Write, non-destructive | Persist a policy-reviewed proposal only. |
| `get_recovery_proposal` | Read-only | Read policy and merchant-decision status. |
| `get_incident_audit_timeline` | Read-only | Explain the append-only decision trail. |

The proposal tool is marked as a write tool so TrueForge pauses for its human
approval checkpoint before calling it. The resulting proposal still has status
`pending_approval`; a merchant decision is a separate authorization boundary.

## Verification modes

The preferred mode is the git-backed `revenue-incident-investigator` skill
running `verify_incident.py` in TrueForge's Daytona sandbox.

If Daytona cannot be configured, `verify_incident_evidence` performs the same
class of deterministic checks inside Revenue SRE and returns:

```json
{
  "verification_runtime": "revenue_sre_mcp_fallback",
  "native_trueforge_sandbox_used": false
}
```

This fallback is intentionally explicit. A demo must not describe it as native
TrueForge sandbox execution.

## TrueForge configuration

Create a saved agent with:

- Model: the configured Qwen 3.6 model.
- Connector: the Revenue SRE MCP server.
- Instructions: `trueforge/agents/revenue-sre-incident-commander.md`.
- Optional connector: official Razorpay MCP, restricted to read-only tools.
- Optional skill: `trueforge/skills/revenue-incident-investigator` when a
  sandbox provider is available.

The official Razorpay MCP and Revenue SRE MCP remain separate connectors.
TrueForge correlates them using Razorpay `payment_id`. Webhooks remain the
authoritative real-time ingestion path.

## Demo prompts

Investigation:

```text
Investigate the highest-risk open incident. Verify its evidence and explain
confirmed facts separately from hypotheses. Do not execute recovery.
```

Proposal:

```text
For the verified incident, prepare one bounded create-payment-link proposal for
the failed payments. Explain the policy result and stop for merchant approval.
Do not call Razorpay or contact customers.
```

After approving or rejecting through the merchant API:

```text
Read the proposal status and show the incident audit timeline. State whether
any recovery action was executed.
```

## Merchant decision API

Approval:

```http
POST /proposals/{proposal_id}/approve
X-Merchant-Id: {merchant_id}
Content-Type: application/json

{"decided_by":"merchant-owner"}
```

Rejection uses the same body at:

```http
POST /proposals/{proposal_id}/reject
```

Proposal status is available at:

```http
GET /proposals/{proposal_id}
X-Merchant-Id: {merchant_id}
```

## Deliberate MVP boundary

The MVP does not claim or implement:

- Razorpay payment-link execution.
- Customer notification delivery.
- Automatic merchant approval.
- Native sandbox execution when Daytona is unavailable.
- Recovered-revenue attribution.

Those operations require a restricted Razorpay adapter, execution idempotency,
delivery controls, and later payment-event verification. Until that adapter is
connected, `execution_performed` remains `false` even after merchant approval.