# Revenue SRE — Hackathon Demo Script

> This original walkthrough is retained for reference. Use
> [`final-video-script.md`](final-video-script.md) for the current two-incident,
> approval-and-execution demonstration.

Target length: **4 minutes 30 seconds**  
Recording style: confident, fast, evidence-first, and honest about the safety boundary.

## Before recording

Use a dedicated demo database. The reset below permanently removes the local
Docker database volume, so never run it against data you need to keep.

```powershell
cd C:\SJ\revenue-sre
docker compose down -v
docker compose up -d db
.\venv\Scripts\Activate.ps1
python -m alembic upgrade head
docker compose up -d worker
```

Start the remaining processes in separate terminals:

```powershell
# Terminal 2 — API
cd C:\SJ\revenue-sre
.\venv\Scripts\Activate.ps1
python -m uvicorn backend.app.main:app --reload --port 8000
```

```powershell
# Terminal 3 — Dashboard
cd C:\SJ\revenue-sre\frontend
pnpm dev
```

```powershell
# Terminal 4 — Revenue SRE MCP
cd C:\SJ\revenue-sre
.\venv\Scripts\Activate.ps1
revenue-sre-mcp
```

Also start TrueForge in WSL and confirm that its saved agent has both the
Revenue SRE MCP and Razorpay test-mode MCP connectors.

Before pressing Record:

1. Open the empty dashboard at `http://127.0.0.1:5173`.
2. Open TrueForge in another tab with the Revenue SRE agent ready.
3. Open `demo/architecture-whiteboard.html` in a browser.
4. Increase terminal font size and pre-type the demo-load command.
5. Hide `.env`, tokens, customer identifiers, and unrelated notifications.

---

## Recording script

### 0:00–0:20 — Hook

**Screen:** Empty Revenue SRE dashboard.

**Say:**

> A payment dashboard tells a merchant that transactions failed. But when a
> failure spike starts, the merchant still has to work out whether it is noise,
> which payment route is affected, how much revenue is at risk, and what action
> is safe. Every minute of uncertainty loses recoverable revenue. Revenue SRE
> turns that fragmented investigation into one controlled incident workflow.

**Judge takeaway:** This is operational revenue recovery, not another payments
table or generic chatbot.

### 0:20–1:00 — Architecture whiteboard

**Screen:** `demo/architecture-whiteboard.html`. Select Phase 1, then advance
through the phases while drawing one short arrow with the pen.

**Say:**

> Razorpay test-mode events enter through a signature-verified FastAPI webhook.
> The API stores the event and a processing job in one database transaction, so
> returning HTTP 200 never risks silently losing the work. A durable PostgreSQL
> worker normalizes the event, preserves full event history, and updates the
> current payment state without allowing out-of-order regression.
>
> A deterministic detector compares the current five-minute segment with its
> thirty-minute baseline. When thresholds are crossed, it persists an incident,
> the exact payment facts, the metric snapshot, and money at risk. TrueForge then
> investigates through read-only MCP tools. AI explains and proposes; deterministic
> policy and explicit merchant approval remain the authority boundary.

**Important wording:** Never say the LLM detects the incident or authorizes a
money action. The detector finds the anomaly; AI investigates it.

### 1:00–1:15 — Prove the empty state

**Screen:** Return to the empty dashboard.

**Say:**

> Right now the system is live, but there are no open incidents and no recovery
> action. I will now replay a realistic, PII-free payment stream through the same
> signed webhook boundary used by Razorpay.

Pause briefly so the empty state is visible.

### 1:15–1:35 — Generate the incident

**Screen:** Terminal.

Run:

```powershell
revenue-sre-load-demo --base-url http://127.0.0.1:8000
```

**Say while it runs:**

> This sends 150 signed Razorpay-format events across healthy control segments
> and one degrading UPI route. It does not insert an incident directly. Each
> event travels through webhook verification, durable processing, normalization,
> and the detector.

Show the loader’s success summary, then switch back to the dashboard.

### 1:35–2:15 — Incident and money at risk

**Screen:** Dashboard automatically populated.

**Say:**

> Revenue SRE opened a UPI timeout incident. The route moved from a five-percent
> baseline failure rate to sixty percent in the current window. Twelve payments
> failed, exposing twelve thousand rupees. The healthy control traffic did not
> become an incident, which demonstrates that this is segmented detection rather
> than a global failure counter.
>
> Notice the evidence state. The incident is backed by twelve exact payment facts
> and one deterministic metric snapshot. Money at risk is calculated from those
> failed payments, not invented by the model.

Click the incident row, failure chart, evidence cards, and audit count.

### 2:15–2:55 — Agent investigation

**Screen:** TrueForge agent.

Prompt:

```text
Investigate the highest-risk open incident. Verify its evidence, separate
confirmed facts from hypotheses, explain the revenue impact, and recommend only
a bounded next step. Do not execute a recovery action.
```

**Say:**

> TrueForge first calls the Revenue SRE MCP to list incidents, then retrieves the
> selected incident’s evidence. The verification tool recomputes counts, rates,
> and money at risk. If any value disagrees, the workflow stops instead of
> allowing AI to reason from corrupted evidence.

Expand the two MCP tool calls. Point out `verified: true`, the evidence counts,
and the disclosed verification runtime.

Then say:

> The agent clearly separates confirmed provider facts from hypotheses. A bank
> timeout is plausible, but it is not presented as a proven internal root cause.
> That distinction is essential for financial operations.

### 2:55–3:30 — Dashboard Incident Commander

**Screen:** Dashboard chat.

Ask:

```text
How was revenue at risk calculated?
```

Then ask:

```text
Execute the recovery now.
```

**Say:**

> The merchant dashboard also has an evidence-grounded Incident Commander. It
> answers from the selected incident and automatically changes context when the
> merchant selects another segment. More importantly, conversational text cannot
> execute money movement or customer contact. The request is refused and routed
> back to the approval-gated workflow.

Use the clear-chat icon to reset the conversation.

### 3:30–4:05 — Proposal and human approval boundary

**Screen:** TrueForge and then dashboard recovery card.

Ask TrueForge to prepare a proposal only if the proposal demo is configured:

```text
Prepare a bounded recovery proposal for the verified incident. Reference only
eligible incident evidence. Do not execute it.
```

Approve the TrueForge write-tool popup for proposal creation. Then return to the
dashboard and show the pending proposal.

**Say:**

> A proposal is not an execution. The backend validates evidence ownership,
> amount limits, action count, expiry, cooldown, and customer-contact limits.
> The exact plan receives an immutable content hash. Only the merchant can approve
> or reject that exact hash, and test-mode execution remains disabled in this MVP.

If proposal creation is not configured, do not fake it. Show “Not created” and say:

> This build intentionally stops before execution. The safety boundary is real,
> and no Razorpay write is implied by the demonstration.

### 4:05–4:30 — Close

**Screen:** Dashboard overview with incident, evidence, and safety state visible.

**Say:**

> Revenue SRE is the incident commander between payment events and recovery:
> durable ingestion, segmented detection, traceable evidence, AI-assisted
> investigation through MCP, bounded proposals, and human authority before any
> action. It does not replace Razorpay’s dashboard; it closes the operational loop
> between a failure spike and safe, measurable revenue recovery.

End on the line:

> AI recommends. Policy bounds. The merchant decides.

---

## Backup plan if a live dependency fails

- If TrueForge streaming fails, show the previously saved successful agent run
  and continue with the real dashboard/MCP output.
- If Daytona is unavailable, explicitly disclose the Revenue SRE deterministic
  verification fallback. Never call it native sandbox execution.
- If the loader reports duplicates, recreate only the dedicated demo database
  before recording and repeat the migration/startup sequence.
- If the proposal is unavailable, show the read-only investigation and safety
  boundary. Do not manufacture approval or execution state.
- Keep the raw API available at `http://127.0.0.1:8000/docs` as technical proof.

## Recording checklist

- [ ] No secrets, `.env`, contacts, API keys, or authorization headers visible.
- [ ] Empty dashboard shown before loading data.
- [ ] Loader command and signed-webhook claim shown.
- [ ] 150-event batch mentioned.
- [ ] Baseline 5%, current 60%, 12 failures, ₹12,000 at risk shown.
- [ ] Exact evidence and verification result shown.
- [ ] MCP tool calls expanded.
- [ ] Facts and hypotheses explicitly separated.
- [ ] Chat refusal of an execution request shown.
- [ ] Human approval boundary explained.
- [ ] No unsupported production or recovery claim made.
- [ ] Final value proposition delivered in one sentence.
