"""Tenant-scoped recovery proposal, approval, and audit persistence."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.audit import ApprovalDecisionType
from ...schemas.recovery import RecoveryPlanStatus
from ..models.recovery import (
    ApprovalRecord,
    AuditRecord,
    RecoveryProposal,
    RecoveryProposalAction,
)


class RecoveryRepository:
    """Persistence operations; the service owns policy and transaction scope."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_proposal(
        self,
        proposal: RecoveryProposal,
        actions: Sequence[RecoveryProposalAction],
    ) -> RecoveryProposal:
        self._session.add(proposal)
        self._session.add_all(actions)
        await self._session.flush()
        return proposal

    async def get_proposal(
        self,
        merchant_id: UUID,
        proposal_id: UUID,
        *,
        for_update: bool = False,
    ) -> RecoveryProposal | None:
        statement = select(RecoveryProposal).where(
            RecoveryProposal.id == proposal_id,
            RecoveryProposal.merchant_id == merchant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_actions(self, proposal_id: UUID) -> Sequence[RecoveryProposalAction]:
        result = await self._session.scalars(
            select(RecoveryProposalAction)
            .where(RecoveryProposalAction.proposal_id == proposal_id)
            .order_by(RecoveryProposalAction.id)
        )
        return result.all()

    async def get_action_for_payment_link(
        self,
        merchant_id: UUID,
        *,
        action_id: UUID,
        payment_link_id: str,
        reference_id: str,
    ) -> RecoveryProposalAction | None:
        return await self._session.scalar(
            select(RecoveryProposalAction)
            .join(RecoveryProposal, RecoveryProposal.id == RecoveryProposalAction.proposal_id)
            .where(
                RecoveryProposal.merchant_id == merchant_id,
                RecoveryProposalAction.id == action_id,
                or_(
                    RecoveryProposalAction.provider_payment_link_id == payment_link_id,
                    RecoveryProposalAction.execution_reference_id == reference_id,
                ),
            )
            .with_for_update()
        )

    async def has_recent_proposal(
        self,
        merchant_id: UUID,
        incident_id: UUID,
        *,
        since: datetime,
    ) -> bool:
        proposal_id = await self._session.scalar(
            select(RecoveryProposal.id)
            .where(
                RecoveryProposal.merchant_id == merchant_id,
                RecoveryProposal.incident_id == incident_id,
                RecoveryProposal.created_at >= since,
            )
            .limit(1)
        )
        return proposal_id is not None

    async def get_active_incident_proposal(
        self,
        merchant_id: UUID,
        incident_id: UUID,
    ) -> RecoveryProposal | None:
        """Return the latest proposal that still represents active authority."""

        return await self._session.scalar(
            select(RecoveryProposal)
            .where(
                RecoveryProposal.merchant_id == merchant_id,
                RecoveryProposal.incident_id == incident_id,
                RecoveryProposal.status.in_(
                    [
                        RecoveryPlanStatus.PENDING_APPROVAL,
                        RecoveryPlanStatus.APPROVED,
                        RecoveryPlanStatus.EXECUTING,
                        RecoveryPlanStatus.COMPLETED,
                        RecoveryPlanStatus.FAILED,
                    ]
                ),
            )
            .order_by(RecoveryProposal.created_at.desc(), RecoveryProposal.id.desc())
            .limit(1)
        )

    async def get_decision(self, proposal_id: UUID) -> ApprovalRecord | None:
        return await self._session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.proposal_id == proposal_id)
        )

    async def add_decision(
        self,
        *,
        proposal: RecoveryProposal,
        decision: ApprovalDecisionType,
        decided_by: str,
        reason: str | None,
        decided_at: datetime,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            merchant_id=proposal.merchant_id,
            proposal_id=proposal.id,
            incident_id=proposal.incident_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            decided_at=decided_at,
            plan_hash=proposal.content_hash,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def add_audit(self, record: AuditRecord) -> AuditRecord:
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_audit(
        self,
        merchant_id: UUID,
        *,
        incident_id: UUID | None = None,
        limit: int = 200,
    ) -> Sequence[AuditRecord]:
        statement = select(AuditRecord).where(AuditRecord.merchant_id == merchant_id)
        if incident_id is not None:
            statement = statement.where(AuditRecord.incident_id == incident_id)
        result = await self._session.scalars(
            statement.order_by(AuditRecord.occurred_at, AuditRecord.id).limit(
                min(max(limit, 1), 500)
            )
        )
        return result.all()
