"""Policy-gated recovery proposal and immutable approval workflow."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database.models.incident import Incident, IncidentEvidenceRecord
from ..database.models.payment_attempt import PaymentAttempt
from ..database.models.payment_event_fact import PaymentEventFact
from ..database.models.recovery import (
    AuditRecord,
    RecoveryProposal,
    RecoveryProposalAction,
)
from ..database.repositories.incident_repository import IncidentRepository
from ..database.repositories.recovery_repository import RecoveryRepository
from ..schemas.audit import ApprovalDecisionType, AuditActorType, AuditEventType
from ..schemas.payment import PaymentStatus
from ..schemas.recovery import (
    ProposalDecisionResponse,
    ProposalExecutionResponse,
    RecoveryAction,
    RecoveryActionResponse,
    RecoveryActionType,
    RecoveryExecutionStatus,
    RecoveryPlan,
    RecoveryPlanStatus,
    RecoveryProposalCreate,
    RecoveryProposalResponse,
)
from .policy_engine import ProposalPolicyContext, evaluate_recovery_proposal
from .razorpay_mcp_adapter import (
    PaymentLinkAdapter,
    RazorpayMCPError,
    RazorpayMCPPaymentLinkAdapter,
)


class RecoveryWorkflowError(RuntimeError):
    """Safe domain error translated to a controlled API response."""


class RecoveryNotFoundError(RecoveryWorkflowError):
    pass


class RecoveryConflictError(RecoveryWorkflowError):
    pass


class RecoveryPolicyRejectedError(RecoveryWorkflowError):
    pass


class RecoveryExecutionDisabledError(RecoveryWorkflowError):
    pass


class RecoveryService:
    """Coordinate proposal persistence, policy checks, decisions, and audit."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def create_proposal(
        self,
        session: AsyncSession,
        *,
        merchant_id: UUID,
        incident_id: UUID,
        request: RecoveryProposalCreate,
        correlation_id: UUID,
        generation_metadata: dict[str, int] | None = None,
    ) -> RecoveryProposalResponse:
        incident = await IncidentRepository(session).get(merchant_id, incident_id)
        if incident is None:
            raise RecoveryNotFoundError("Incident was not found")
        repository = RecoveryRepository(session)
        now = datetime.now(UTC)
        cooldown_active = False
        if self._settings.recovery_proposal_cooldown_minutes > 0:
            cooldown_active = await repository.has_recent_proposal(
                merchant_id,
                incident_id,
                since=now - timedelta(minutes=self._settings.recovery_proposal_cooldown_minutes),
            )
        eligible_payments = await self._eligible_payments(session, incident)
        eligible_evidence_ids = await self._eligible_evidence_ids(session, incident)
        evidence_ids = request.evidence_ids or [UUID(value) for value in eligible_evidence_ids]
        proposal_id = uuid4()
        actions = [
            RecoveryAction(
                payment_id=action.payment_id,
                action_type=action.action_type,
                amount_subunits=action.amount_subunits,
                rationale=action.rationale,
                requires_approval=action.requires_approval,
            )
            for action in request.actions
        ]
        total = sum(action.amount_subunits for action in actions)
        plan = RecoveryPlan(
            plan_id=proposal_id,
            merchant_id=merchant_id,
            incident_id=incident_id,
            evidence_ids=evidence_ids,
            actions=actions,
            total_amount_subunits=total,
            currency=request.currency,
            maximum_customer_contacts=request.maximum_customer_contacts,
            expires_at=request.expires_at,
            status=RecoveryPlanStatus.PENDING_APPROVAL,
        )
        decision = evaluate_recovery_proposal(
            plan,
            self._policy_context(
                incident,
                eligible_payments=eligible_payments,
                eligible_evidence_ids=eligible_evidence_ids,
                cooldown_active=cooldown_active,
            ),
            now=now,
            policy_version=self._settings.recovery_policy_version,
        )
        content_hash = proposal_content_hash(plan)
        proposal = RecoveryProposal(
            id=proposal_id,
            merchant_id=merchant_id,
            incident_id=incident_id,
            status=(
                RecoveryPlanStatus.PENDING_APPROVAL
                if decision.allowed
                else RecoveryPlanStatus.DRAFT
            ),
            total_amount_subunits=total,
            currency=request.currency,
            maximum_customer_contacts=request.maximum_customer_contacts,
            expires_at=request.expires_at,
            content_hash=content_hash,
            policy_version=decision.policy_version,
            policy_allowed=decision.allowed,
            policy_reasons=list(decision.reasons),
            eligible_payment_count=len(eligible_payments),
            omitted_payment_count=max(0, len(eligible_payments) - len(actions)),
            evidence_ids=[str(value) for value in evidence_ids],
            created_by=request.created_by,
        )
        stored_actions = [
            RecoveryProposalAction(
                id=action.action_id,
                proposal_id=proposal_id,
                payment_id=action.payment_id,
                action_type=action.action_type,
                amount_subunits=action.amount_subunits,
                rationale=action.rationale,
                requires_approval=action.requires_approval,
            )
            for action in actions
        ]
        await repository.add_proposal(proposal, stored_actions)
        await repository.add_audit(
            self._audit(
                merchant_id=merchant_id,
                correlation_id=correlation_id,
                incident_id=incident_id,
                proposal_id=proposal_id,
                event_type=AuditEventType.PLAN_PROPOSED,
                actor_type=AuditActorType.AGENT,
                actor_id=request.created_by,
                details={
                    "plan_hash": content_hash,
                    "total_amount_subunits": total,
                    "evidence_ids": [str(value) for value in evidence_ids],
                    **(generation_metadata or {}),
                },
            )
        )
        await repository.add_audit(
            self._audit(
                merchant_id=merchant_id,
                correlation_id=correlation_id,
                incident_id=incident_id,
                proposal_id=proposal_id,
                event_type=AuditEventType.POLICY_VALIDATED,
                actor_type=AuditActorType.SYSTEM,
                actor_id=decision.policy_version,
                details={"allowed": decision.allowed, "reasons": list(decision.reasons)},
            )
        )
        if decision.allowed:
            await repository.add_audit(
                self._audit(
                    merchant_id=merchant_id,
                    correlation_id=correlation_id,
                    incident_id=incident_id,
                    proposal_id=proposal_id,
                    event_type=AuditEventType.APPROVAL_REQUESTED,
                    actor_type=AuditActorType.SYSTEM,
                    actor_id=decision.policy_version,
                    details={"plan_hash": content_hash},
                )
            )
        return self._proposal_response(proposal, stored_actions)

    async def decide(
        self,
        session: AsyncSession,
        *,
        merchant_id: UUID,
        proposal_id: UUID,
        decision: ApprovalDecisionType,
        decided_by: str,
        reason: str | None,
        correlation_id: UUID,
    ) -> ProposalDecisionResponse:
        repository = RecoveryRepository(session)
        proposal = await repository.get_proposal(merchant_id, proposal_id, for_update=True)
        if proposal is None:
            raise RecoveryNotFoundError("Recovery proposal was not found")
        existing = await repository.get_decision(proposal_id)
        if existing is not None:
            if existing.decision != decision:
                raise RecoveryConflictError("Proposal already has a different decision")
            return self._decision_response(existing)
        actions = list(await repository.list_actions(proposal_id))
        if proposal_content_hash(self._stored_plan(proposal, actions)) != proposal.content_hash:
            raise RecoveryConflictError("Proposal content no longer matches its review hash")
        incident = await IncidentRepository(session).get(merchant_id, proposal.incident_id)
        if incident is None:
            raise RecoveryNotFoundError("Incident was not found")
        if decision == ApprovalDecisionType.APPROVED:
            if proposal.status != RecoveryPlanStatus.PENDING_APPROVAL:
                raise RecoveryConflictError("Proposal is not awaiting approval")
            eligible_payments = await self._eligible_payments(session, incident)
            eligible_evidence_ids = await self._eligible_evidence_ids(session, incident)
            policy = evaluate_recovery_proposal(
                self._stored_plan(proposal, actions),
                self._policy_context(
                    incident,
                    eligible_payments=eligible_payments,
                    eligible_evidence_ids=eligible_evidence_ids,
                    cooldown_active=False,
                ),
                policy_version=proposal.policy_version,
            )
            if not policy.allowed:
                raise RecoveryPolicyRejectedError("Proposal no longer passes policy")
            proposal.status = RecoveryPlanStatus.APPROVED
            event_type = AuditEventType.PLAN_APPROVED
        else:
            if proposal.status not in {
                RecoveryPlanStatus.DRAFT,
                RecoveryPlanStatus.PENDING_APPROVAL,
            }:
                raise RecoveryConflictError("Proposal can no longer be rejected")
            proposal.status = RecoveryPlanStatus.REJECTED
            event_type = AuditEventType.PLAN_REJECTED
        decided_at = datetime.now(UTC)
        record = await repository.add_decision(
            proposal=proposal,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            decided_at=decided_at,
        )
        await repository.add_audit(
            self._audit(
                merchant_id=merchant_id,
                correlation_id=correlation_id,
                incident_id=proposal.incident_id,
                proposal_id=proposal.id,
                event_type=event_type,
                actor_type=AuditActorType.MERCHANT,
                actor_id=decided_by,
                details={
                    "plan_hash": proposal.content_hash,
                    "reason": reason,
                },
            )
        )
        await session.flush()
        return self._decision_response(record)

    async def get_proposal(
        self,
        session: AsyncSession,
        *,
        merchant_id: UUID,
        proposal_id: UUID,
    ) -> RecoveryProposalResponse:
        """Return one merchant-owned proposal and its immutable actions."""

        repository = RecoveryRepository(session)
        proposal = await repository.get_proposal(merchant_id, proposal_id)
        if proposal is None:
            raise RecoveryNotFoundError("Recovery proposal was not found")
        actions = list(await repository.list_actions(proposal_id))
        return self._proposal_response(proposal, actions)

    async def execute_proposal(
        self,
        session: AsyncSession,
        *,
        merchant_id: UUID,
        proposal_id: UUID,
        executed_by: str,
        correlation_id: UUID,
        adapter: PaymentLinkAdapter | None = None,
    ) -> ProposalExecutionResponse:
        """Execute only approved, hash-bound Payment Link actions through Razorpay MCP."""

        if not self._settings.execution_enabled:
            raise RecoveryExecutionDisabledError("Recovery execution is disabled")
        repository = RecoveryRepository(session)
        proposal = await repository.get_proposal(merchant_id, proposal_id, for_update=True)
        if proposal is None:
            raise RecoveryNotFoundError("Recovery proposal was not found")
        actions = list(await repository.list_actions(proposal_id))
        if proposal_content_hash(self._stored_plan(proposal, actions)) != proposal.content_hash:
            raise RecoveryConflictError("Proposal content no longer matches its approval hash")
        approval = await repository.get_decision(proposal_id)
        if approval is None or approval.decision != ApprovalDecisionType.APPROVED:
            raise RecoveryConflictError("Proposal does not have merchant approval")
        if approval.plan_hash != proposal.content_hash:
            raise RecoveryConflictError("Merchant approval does not match the proposal hash")
        if proposal.status == RecoveryPlanStatus.COMPLETED:
            return self._execution_response(proposal, actions)
        if proposal.status not in {RecoveryPlanStatus.APPROVED, RecoveryPlanStatus.EXECUTING}:
            raise RecoveryConflictError("Proposal is not executable")
        if _as_utc(proposal.expires_at) <= datetime.now(UTC):
            raise RecoveryConflictError("Proposal has expired")
        if any(action.action_type != RecoveryActionType.CREATE_PAYMENT_LINK for action in actions):
            raise RecoveryConflictError("Proposal contains an unsupported execution action")

        provider = adapter or self._payment_link_adapter()
        proposal.status = RecoveryPlanStatus.EXECUTING
        for action in actions:
            if action.execution_status == RecoveryExecutionStatus.SUCCEEDED:
                continue
            if action.execution_status == RecoveryExecutionStatus.FAILED:
                continue
            payment = await session.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.merchant_id == merchant_id,
                    PaymentAttempt.payment_id == action.payment_id,
                )
            )
            if payment is None or payment.status != PaymentStatus.FAILED:
                action.execution_status = RecoveryExecutionStatus.SKIPPED
                action.execution_error_code = "payment_no_longer_failed"
                await repository.add_audit(
                    self._audit(
                        merchant_id=merchant_id,
                        correlation_id=correlation_id,
                        incident_id=proposal.incident_id,
                        proposal_id=proposal.id,
                        event_type=AuditEventType.ACTION_SKIPPED,
                        actor_type=AuditActorType.SYSTEM,
                        actor_id=executed_by,
                        details={
                            "action_id": str(action.id),
                            "reason": action.execution_error_code,
                        },
                    )
                )
                continue

            reference_id = f"rsre-{action.id.hex}"
            arguments = {
                "amount": action.amount_subunits,
                "currency": proposal.currency,
                "description": "Merchant-approved recovery payment",
                "accept_partial": False,
                "expire_by": int(_as_utc(proposal.expires_at).timestamp()),
                "reference_id": reference_id,
                "notify_sms": False,
                "notify_email": False,
                "reminder_enable": False,
                "notes": {
                    "revenue_sre_proposal_id": str(proposal.id),
                    "revenue_sre_action_id": str(action.id),
                    "revenue_sre_incident_id": str(proposal.incident_id),
                    "original_payment_id": action.payment_id,
                },
            }
            try:
                result = await provider.create_payment_link(arguments)
            except RazorpayMCPError:
                action.execution_status = RecoveryExecutionStatus.FAILED
                action.execution_reference_id = reference_id
                action.execution_error_code = "razorpay_mcp_create_failed"
                await repository.add_audit(
                    self._audit(
                        merchant_id=merchant_id,
                        correlation_id=correlation_id,
                        incident_id=proposal.incident_id,
                        proposal_id=proposal.id,
                        event_type=AuditEventType.ACTION_FAILED,
                        actor_type=AuditActorType.RAZORPAY,
                        actor_id="razorpay-mcp",
                        details={
                            "action_id": str(action.id),
                            "error_code": action.execution_error_code,
                        },
                    )
                )
                continue

            action.execution_status = RecoveryExecutionStatus.SUCCEEDED
            action.execution_reference_id = result.reference_id
            action.provider_payment_link_id = result.payment_link_id
            action.payment_link_url = result.short_url
            action.executed_at = datetime.now(UTC)
            action.execution_error_code = None
            await repository.add_audit(
                self._audit(
                    merchant_id=merchant_id,
                    correlation_id=correlation_id,
                    incident_id=proposal.incident_id,
                    proposal_id=proposal.id,
                    event_type=AuditEventType.ACTION_EXECUTED,
                    actor_type=AuditActorType.RAZORPAY,
                    actor_id="razorpay-mcp",
                    details={
                        "action_id": str(action.id),
                        "payment_link_id": result.payment_link_id,
                        "reference_id": result.reference_id,
                        "amount_subunits": action.amount_subunits,
                    },
                )
            )

        proposal.status = (
            RecoveryPlanStatus.COMPLETED
            if actions
            and all(
                action.execution_status == RecoveryExecutionStatus.SUCCEEDED for action in actions
            )
            else RecoveryPlanStatus.FAILED
        )
        await session.flush()
        return self._execution_response(proposal, actions)

    def _payment_link_adapter(self) -> RazorpayMCPPaymentLinkAdapter:
        if not self._settings.razorpay_key_id or self._settings.razorpay_key_secret is None:
            raise RecoveryExecutionDisabledError(
                "Razorpay execution credentials are not configured"
            )
        return RazorpayMCPPaymentLinkAdapter(
            url=self._settings.razorpay_mcp_url,
            key_id=self._settings.razorpay_key_id,
            key_secret=self._settings.razorpay_key_secret.get_secret_value(),
            timeout_seconds=self._settings.razorpay_mcp_timeout_seconds,
        )

    async def _eligible_payments(self, session: AsyncSession, incident: Incident) -> dict[str, int]:
        evidence_payment_ids = set(
            (
                await session.scalars(
                    select(PaymentEventFact.payment_id)
                    .join(
                        IncidentEvidenceRecord,
                        IncidentEvidenceRecord.payment_event_fact_id == PaymentEventFact.id,
                    )
                    .where(
                        IncidentEvidenceRecord.merchant_id == incident.merchant_id,
                        IncidentEvidenceRecord.incident_id == incident.id,
                    )
                )
            ).all()
        )
        current = (
            await session.scalars(
                select(PaymentAttempt).where(
                    PaymentAttempt.merchant_id == incident.merchant_id,
                    PaymentAttempt.payment_id.in_(evidence_payment_ids),
                )
            )
        ).all()
        return {
            payment.payment_id: payment.amount_subunits
            for payment in current
            if payment.status == PaymentStatus.FAILED and payment.currency == incident.currency
        }

    @staticmethod
    async def _eligible_evidence_ids(
        session: AsyncSession,
        incident: Incident,
    ) -> frozenset[str]:
        values = await session.scalars(
            select(IncidentEvidenceRecord.id).where(
                IncidentEvidenceRecord.merchant_id == incident.merchant_id,
                IncidentEvidenceRecord.incident_id == incident.id,
            )
        )
        return frozenset(str(value) for value in values.all())

    def _policy_context(
        self,
        incident: Incident,
        *,
        eligible_payments: dict[str, int],
        eligible_evidence_ids: frozenset[str],
        cooldown_active: bool,
    ) -> ProposalPolicyContext:
        return ProposalPolicyContext(
            incident_status=incident.status,
            incident_currency=incident.currency,
            incident_money_at_risk_subunits=incident.revenue_at_risk_subunits,
            eligible_payment_amounts=eligible_payments,
            eligible_evidence_ids=eligible_evidence_ids,
            maximum_plan_amount_subunits=self._settings.recovery_max_plan_amount_subunits,
            maximum_actions=self._settings.recovery_max_actions_per_plan,
            maximum_plan_lifetime_minutes=self._settings.recovery_max_plan_lifetime_minutes,
            maximum_customer_contacts=self._settings.recovery_max_customer_contacts,
            cooldown_active=cooldown_active,
        )

    @staticmethod
    def _stored_plan(
        proposal: RecoveryProposal,
        actions: list[RecoveryProposalAction],
    ) -> RecoveryPlan:
        return RecoveryPlan(
            plan_id=proposal.id,
            merchant_id=proposal.merchant_id,
            incident_id=proposal.incident_id,
            evidence_ids=[UUID(value) for value in proposal.evidence_ids],
            actions=[
                RecoveryAction(
                    action_id=action.id,
                    payment_id=action.payment_id,
                    action_type=action.action_type,
                    amount_subunits=action.amount_subunits,
                    rationale=action.rationale,
                    requires_approval=action.requires_approval,
                )
                for action in actions
            ],
            total_amount_subunits=proposal.total_amount_subunits,
            currency=proposal.currency,
            maximum_customer_contacts=proposal.maximum_customer_contacts,
            expires_at=_as_utc(proposal.expires_at),
            status=RecoveryPlanStatus.PENDING_APPROVAL,
        )

    @staticmethod
    def _proposal_response(
        proposal: RecoveryProposal,
        actions: list[RecoveryProposalAction],
    ) -> RecoveryProposalResponse:
        return RecoveryProposalResponse(
            proposal_id=proposal.id,
            merchant_id=proposal.merchant_id,
            incident_id=proposal.incident_id,
            evidence_ids=[UUID(value) for value in proposal.evidence_ids],
            status=proposal.status,
            actions=[
                RecoveryActionResponse(
                    action_id=action.id,
                    payment_id=action.payment_id,
                    action_type=action.action_type,
                    amount_subunits=action.amount_subunits,
                    rationale=action.rationale,
                    requires_approval=action.requires_approval,
                    execution_status=action.execution_status,
                    provider_payment_link_id=action.provider_payment_link_id,
                    payment_link_url=action.payment_link_url,
                    execution_reference_id=action.execution_reference_id,
                    executed_at=_as_utc(action.executed_at) if action.executed_at else None,
                    execution_error_code=action.execution_error_code,
                    recovered_payment_id=action.recovered_payment_id,
                    recovered_amount_subunits=action.recovered_amount_subunits,
                    recovered_at=_as_utc(action.recovered_at) if action.recovered_at else None,
                )
                for action in actions
            ],
            total_amount_subunits=proposal.total_amount_subunits,
            currency=proposal.currency,
            maximum_customer_contacts=proposal.maximum_customer_contacts,
            expires_at=_as_utc(proposal.expires_at),
            content_hash=proposal.content_hash,
            policy_allowed=proposal.policy_allowed,
            policy_reasons=proposal.policy_reasons,
            policy_version=proposal.policy_version,
            eligible_payment_count=proposal.eligible_payment_count,
            omitted_payment_count=proposal.omitted_payment_count,
            created_by=proposal.created_by,
            created_at=_as_utc(proposal.created_at),
            action_count=len(actions),
            maximum_recoverable_amount_subunits=proposal.total_amount_subunits,
            stopping_conditions=[
                "merchant_approval_required",
                "proposal_must_not_be_expired",
                "payment_must_still_be_failed",
                f"maximum_actions:{len(actions)}",
                f"maximum_amount_subunits:{proposal.total_amount_subunits}",
                f"maximum_customer_contacts:{proposal.maximum_customer_contacts}",
            ],
            execution_performed=any(
                action.execution_status == RecoveryExecutionStatus.SUCCEEDED for action in actions
            ),
        )

    @classmethod
    def _execution_response(
        cls,
        proposal: RecoveryProposal,
        actions: list[RecoveryProposalAction],
    ) -> ProposalExecutionResponse:
        proposal_response = cls._proposal_response(proposal, actions)
        return ProposalExecutionResponse(
            proposal_id=proposal.id,
            incident_id=proposal.incident_id,
            status=proposal.status,
            actions=proposal_response.actions,
            executed_count=sum(
                action.execution_status == RecoveryExecutionStatus.SUCCEEDED for action in actions
            ),
            failed_count=sum(
                action.execution_status == RecoveryExecutionStatus.FAILED for action in actions
            ),
            execution_performed=proposal_response.execution_performed,
        )

    @staticmethod
    def _decision_response(record) -> ProposalDecisionResponse:
        return ProposalDecisionResponse(
            approval_id=record.id,
            proposal_id=record.proposal_id,
            incident_id=record.incident_id,
            decision=record.decision.value,
            decided_by=record.decided_by,
            reason=record.reason,
            decided_at=_as_utc(record.decided_at),
            plan_hash=record.plan_hash,
        )

    @staticmethod
    def _audit(**values) -> AuditRecord:
        return AuditRecord(occurred_at=datetime.now(UTC), **values)


def proposal_content_hash(plan: RecoveryPlan) -> str:
    """Hash canonical immutable plan fields, independent of action ordering."""

    content = {
        "plan_id": str(plan.plan_id),
        "merchant_id": str(plan.merchant_id),
        "incident_id": str(plan.incident_id),
        "evidence_ids": sorted(str(value) for value in plan.evidence_ids),
        "actions": sorted(
            [
                {
                    "action_id": str(action.action_id),
                    "payment_id": action.payment_id,
                    "action_type": action.action_type.value,
                    "amount_subunits": action.amount_subunits,
                    "rationale": action.rationale,
                    "requires_approval": action.requires_approval,
                }
                for action in plan.actions
            ],
            key=lambda action: action["action_id"],
        ),
        "total_amount_subunits": plan.total_amount_subunits,
        "currency": plan.currency,
        "maximum_customer_contacts": plan.maximum_customer_contacts,
        "expires_at": plan.expires_at.astimezone(UTC).isoformat(),
        "approval_required": plan.approval_required,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
