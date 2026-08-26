# Revenue SRE

Revenue SRE is a human-gated payment incident commander for Razorpay merchants.
It detects payment degradation across a batch, calculates revenue at risk,
prepares a bounded recovery plan, requests merchant approval, executes through
Razorpay test-mode tools, and records an auditable outcome.

The project targets Razorpay Buildathon Track 3 (AI Revenue Recovery) and uses
TrueForge as an agent orchestration and sandbox layer. Financial rules remain
deterministic and provider-independent; an LLM can explain or recommend but
cannot authorize a money action.

## Day 1 status

- FastAPI application and `/health` endpoint
- Razorpay-aligned Pydantic domain contracts
- Fail-closed recovery policy with execution disabled by default
- Unit and API contract tests
- Architecture and threat model
- TrueForge running in WSL2 with the `sidd-ai` Qwen model
- Razorpay remote MCP connected to test mode; `fetch_all_payments` verified

Not implemented yet: webhook ingestion, authentication, persistence, incident
detection, approval APIs, write execution, outcome verification, and dashboard.

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000/health` or API docs at
`http://127.0.0.1:8000/docs`.

## Verify the foundation

```powershell
python -m pytest -v
python -m ruff check .
python -m ruff format --check .
```

## Safety principles

- Razorpay amounts are integer currency subunits: `100000` INR means ₹1,000.
- Every write is merchant-approved, bounded, idempotent, and audited.
- Payment status is rechecked immediately before recovery execution.
- LLM output is untrusted input and must pass Pydantic and policy validation.
- Secrets belong in `.env` or a secret manager and never in Git.
- Production execution fails closed; `EXECUTION_ENABLED=false` is the default.

Read [the architecture](docs/architecture.md),
[the threat model](docs/threat-model.md), and
[the contribution workflow](CONTRIBUTING.md) before implementation.

## AI disclosure

Qwen 3.6 (`sidd-ai`) is used through TrueForge for reasoning experiments. Qodo
is used for pull-request review, and Codex assisted with design and code. All
generated changes remain subject to tests, human review, and deterministic
financial controls.
