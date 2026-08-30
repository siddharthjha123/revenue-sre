# Contributing to Revenue SRE

Contributions should preserve the system's core guarantees: merchant isolation,
explicit approval for financial actions, idempotent execution, and an auditable
record of every decision.

## Development setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Branches

- `main` contains releasable versions.
- `develop` contains changes planned for the next release.
- `feat/<scope>` and `fix/<scope>` are short-lived development branches.

Feature and fix branches are merged into `develop`. Release changes are merged
from `develop` into `main` after verification.

## Code standards

- Keep provider payload conversion at adapter boundaries.
- Represent monetary amounts as integer currency subunits.
- Treat model output and external event data as untrusted input.
- Enforce financial rules in deterministic services.
- Add tests for success, rejection, retries, and tenant isolation.
- Never commit credentials or customer-identifying information.

Use Conventional Commits:

```text
feat(incidents): detect payment failure spikes
fix(recovery): prevent duplicate payment links
test(policy): reject expired recovery plans
docs(api): describe approval lifecycle
```

## Local verification

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest -v
git diff --check
```

A change is ready for review when its behavior is documented, failure handling
is tested, and the automated checks pass.
