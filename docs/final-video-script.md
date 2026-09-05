# Revenue SRE — Final Video Script

Target length: **5 to 5.5 minutes**  
Shot 1: introduction and architecture, approximately **2:30**  
Shot 2: live product demonstration, approximately **2:45**

## Shot 1 — Problem and architecture

### 0:00–0:40 — The problem

**Screen:** Start on the title, then reveal the architecture whiteboard.

> Suppose you are a merchant and suddenly see a stream of failed-payment
> notifications. You know payments are failing, but you do not know whether it
> is random customer behaviour or a real provider incident, which route is
> affected, how much revenue is exposed, or what action is safe.
>
> A normal payment dashboard shows transactions. It does not run the operational
> investigation for you. That is where Revenue SRE comes in.

### 0:40–1:00 — Introduction

> Hello judges, my name is Siddharth Jha. I am a third-year Artificial
> Intelligence and Machine Learning student at Thakur College of Engineering and
> Technology.
>
> Revenue SRE sits beside Razorpay and acts as a control plane between payment
> events and merchant recovery. It detects unusual failure spikes, calculates
> revenue at risk, verifies the evidence, and provides a safe path from incident
> to recovery—without allowing an AI conversation to move money by itself.

### 1:00–1:25 — Phase 1: safe ingestion

**Screen:** Select **Phase 1** on `demo/architecture-whiteboard.html`.

> Razorpay sends signed payment events to a FastAPI webhook. The signature is
> verified against the raw request body and the event is bound to the correct
> merchant account. The webhook and its processing job are committed together
> in PostgreSQL. This transactional inbox means an accepted event cannot be
> silently lost if a worker crashes immediately afterwards.

### 1:25–1:50 — Phase 2: deterministic detection

**Screen:** Select **Phase 2** and circle the segment detector.

> A durable worker leases the job, retries safely, and normalizes the provider
> event into immutable payment facts and current payment state. The detector
> groups attempts by merchant, payment method, bank, error reason, and time
> window. It compares the current five-minute window with a thirty-minute
> baseline. Detection and revenue-at-risk calculations are backend rules—not
> guesses made by the language model.

### 1:50–2:10 — Phase 3: evidence-grounded AI

**Screen:** Select **Phase 3**.

> TrueForge orchestrates a locally hosted Qwen model and the Revenue SRE MCP.
> The agent can list incidents, retrieve exact evidence, and explain impact.
> A deterministic verifier recomputes attempts, failures, rates, and money. If
> the persisted snapshot and evidence disagree, the investigation stops. Facts
> remain separate from hypotheses, so a bank timeout signal is not falsely
> presented as proof of the bank's internal root cause.

### 2:10–2:30 — Phases 4 and 5: authority and outcome

**Screen:** Select **Phase 4**, then **Phase 5**.

> The agent can prepare a bounded proposal, but policy limits its amount, action
> count, eligibility, expiry, and customer-contact scope. The merchant approves
> the exact immutable plan before the restricted adapter can create Razorpay
> test-mode Payment Links. Later `payment_link.paid` webhooks attribute successful
> payments back to their recovery actions. Only then does Revenue SRE report
> recovered revenue and close the loop in an append-only audit timeline.

## Shot 2 — Live demonstration

### 2:30–2:50 — Empty but operational dashboard

**Screen:** Open the Revenue SRE dashboard before loading data.

> The system is live and currently healthy. There are no incidents, but the
> dashboard remains fully usable. It shows today's payment attempts, captured
> payments and revenue, incident count, revenue at risk, recovered revenue, and
> the audit workspace.

### 2:50–3:10 — Replay the payment stream

**Screen:** Run the loader in a prepared terminal.

```powershell
.\venv\Scripts\revenue-sre-load-demo.exe --timeout-seconds 120
```

> I am now replaying 150 signed, PII-free Razorpay-format events through the real
> webhook path. This script does not insert incidents directly. The API, inbox,
> worker, normalizer, and detector process every event.

### 3:10–3:40 — Two segmented incidents

**Screen:** Return to the dashboard and refresh if necessary.

> The dashboard now shows all 150 attempts and the captured-payment revenue.
> Two UPI segments crossed the incident thresholds. HDFC is ranked first because
> its current failure rate is 60 percent with twelve failed payments and twelve
> thousand rupees at risk. AXIS is the second incident at 50 percent with seven
> thousand five hundred rupees at risk. ICICI card traffic remained below the threshold and
> correctly stayed out of the incident queue.
>
> Clicking each metric explains both its calculation and its exact observation
> window. Selecting an incident also changes the context attached to the Revenue
> Operator.

### 3:40–4:10 — AI investigation

**Screen:** Select HDFC and click **Summarize incident**.

> The frontend streams the request to the configured agent with the selected
> incident context. The response identifies confirmed evidence, explains the
> revenue exposure, and labels unconfirmed causes as hypotheses. The chat is an
> advisory channel; it cannot execute a financial action.

Optional prompt:

```text
Explain the verified evidence and the safest next step for this incident.
```

### 4:10–4:40 — Generate and approve a bounded proposal

**Screen:** Click **Plan recovery**. Let the authority-boundary animation finish.

> I will now ask for a bounded recovery proposal. Evidence is verified first,
> deterministic policy is applied, and the persisted proposal appears in the
> authority panel. It shows the maximum amount, included actions, omitted
> payments, expiry, and policy version. This is still not execution.
>
> I now approve the exact plan. The decision is bound to its content hash and
> written to the immutable audit record. Editing the plan would require a new
> approval.

### 4:40–5:05 — Razorpay test-mode execution

**Screen:** Click the separate execution control and show the Razorpay dashboard.

> Only after approval can the restricted executor call the official Razorpay MCP
> and create test-mode Payment Links. Each link carries a unique reference and
> recovery metadata, so it maps to one proposal action and one original failed
> payment. The merchant can copy these links into an existing CRM, support, or
> messaging workflow. No customer contact details are exposed to the AI.

### 5:05–5:25 — Verified recovery and audit

**Screen:** If practical, complete one test Payment Link, refresh Revenue SRE,
then open **Audit & outcomes**.

> After a test link is paid, Razorpay sends a signed `payment_link.paid` webhook.
> Revenue SRE attributes the new payment to the approved action and increases
> recovered revenue only after that outcome is verified. The audit page preserves
> the incident, evidence verification, proposal, policy result, merchant decision,
> execution, and measured outcome in one traceable timeline.

### 5:25–5:35 — Closing line

> Revenue SRE turns payment failures into an evidence-driven and merchant-controlled
> recovery workflow: AI recommends, policy bounds, the merchant decides, and the
> outcome is measured.

## Recording checklist

- Keep `.env`, API keys, webhook secrets, and customer data off screen.
- Keep Razorpay and Revenue SRE in **test mode**.
- Start with an empty database and dashboard.
- Keep the worker, API, MCP, TrueForge, Ollama proxy, frontend, and tunnel running.
- Pre-type the loader command before recording.
- Show HDFC as highest risk and AXIS as the second incident.
- Do not call the verifier a native sandbox; this build uses deterministic backend verification.
- Do not say approval created Payment Links; execution is a separate explicit step.
- If a live test payment is too slow, stop after showing created links and say recovery remains zero until a paid webhook arrives.
