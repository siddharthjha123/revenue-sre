"""Approval immutability and measured-outcome tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.schemas.audit import ApprovalDecision, PlaybookOutcome


def test_approval_is_bound_to_sha256_plan_hash() -> None:
    approval = ApprovalDecision(
        plan_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
        decision="approved",
        decided_by="merchant-owner@example.invalid",
        decided_at=datetime.now(UTC),
        plan_hash="a" * 64,
    )

    with pytest.raises(ValidationError):
        approval.decision = "rejected"


def test_outcome_computes_honest_success_rate() -> None:
    outcome = PlaybookOutcome(
        playbook_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
        merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
        failure_segment="bank_decline",
        action="offer one retry link",
        version="1.0.0",
        approved=True,
        attempted_count=10,
        recovered_count=4,
        recovered_amount_subunits=400000,
        executed_at=datetime.now(UTC),
    )

    assert outcome.success_rate == 0.4


def test_recovered_count_cannot_exceed_attempted_count() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        PlaybookOutcome(
            playbook_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
            merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
            failure_segment="unknown",
            action="no action",
            version="1.0.0",
            attempted_count=1,
            recovered_count=2,
            recovered_amount_subunits=0,
            executed_at=datetime.now(UTC),
        )
