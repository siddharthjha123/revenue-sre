# Revenue SRE architecture

## System context

Revenue SRE sits beside, not inside, Razorpay's payment processing path. A
checkout succeeds or fails independently of this service. Revenue SRE observes
test-mode events, detects degradation, proposes recovery, obtains explicit
merchant approval, and invokes bounded Razorpay recovery tools.

![Revenue SRE system architecture](assets/revenue-sre-architecture.svg)

## Responsibilities

| Component | Owns | Must not own |
|---|---|---|
| Razorpay | Payment truth and test-mode execution | Incident reasoning |
| FastAPI | Contracts, tenant boundary, policy, approvals, audit | Free-form agent reasoning |
| TrueForge | Tool orchestration and isolated analysis | Final financial authorization |
| Reasoning model | Explanations, hypotheses and plan proposals | Direct financial or customer-contact authority |
| Database | Idempotency, incidents, plans, approvals, audit | Hidden mutable reasoning state |
| Dashboard | Evidence, approval and outcomes | Silent automated execution |

## Processing flow

1. Verify the Razorpay webhook signature against the raw request body.
2. Deduplicate the event, normalize it, and bind it to one merchant tenant.
3. Group failures by method, bank, error source, checkout version, and time.
4. Run deterministic metrics in a sandbox; label model output as hypothesis.
5. Produce a schema-validated recovery plan with amount and contact bounds.
6. Apply deterministic policy and show evidence to the merchant.
7. Hash the plan and persist the merchant's decision against that exact hash.
8. Recheck payment state, policy, expiry, and idempotency immediately before a write.
9. Execute one approved Razorpay action and record request/result references.
10. Verify subsequent payment status and report money actually recovered.

## Trust boundaries

- Razorpay/merchant boundary: external event data is untrusted until verified.
- Agent/application boundary: prompts and model outputs are untrusted data.
- Tenant boundary: every query and action is scoped by authenticated merchant ID.
- Approval/execution boundary: approved plan content is immutable and hashed.
- Sandbox/host boundary: analysis gets minimum data, resources, time, and no secrets.

## Orchestration boundary

TrueForge is an orchestration adapter around the FastAPI control plane. It can
retrieve data, coordinate analysis and produce structured proposals, but it is
not the source of payment truth or authorization policy. Core validation,
approval and audit services remain independent of the orchestration runtime.
