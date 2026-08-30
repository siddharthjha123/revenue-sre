"""Deterministic, evidence-grounded answers for the dashboard commander.

The TrueForge agent remains the generative investigation surface. This service
gives the merchant dashboard an immediately usable chat experience without
inventing facts or allowing conversational text to trigger a money action.
"""

from collections.abc import Sequence

from ..database.models.incident import Incident, IncidentEvidenceRecord
from ..schemas.incident import EvidenceKind, IncidentCommanderChatResponse

SAFETY_NOTICE = (
    "Advisory only. Chat cannot execute a Razorpay action. Any recovery proposal "
    "must pass policy checks and receive separate merchant approval."
)


def answer_incident_question(
    incident: Incident,
    evidence: Sequence[IncidentEvidenceRecord],
    message: str,
) -> IncidentCommanderChatResponse:
    """Answer common incident questions using only persisted incident evidence."""

    question = " ".join(message.lower().split())
    facts = [item for item in evidence if item.kind == EvidenceKind.RAZORPAY_FACT]
    metrics = [item for item in evidence if item.kind == EvidenceKind.SANDBOX_METRIC]
    evidence_risk = sum(int(item.details.get("amount_subunits", 0)) for item in facts)
    verified = (
        len(facts) == incident.current_failure_count
        and evidence_risk == incident.revenue_at_risk_subunits
        and len(metrics) == 1
    )

    bank = incident.bank or "the affected provider"
    method = incident.method.upper()
    money = _format_inr(incident.revenue_at_risk_subunits)
    current_rate = _percent(incident.current_failure_rate)
    baseline_rate = _percent(incident.baseline_failure_rate)
    confirmed = [
        (
            f"{incident.current_failure_count} of {incident.current_attempt_count} "
            "current-window attempts failed."
        ),
        f"Failure rate moved from {baseline_rate} to {current_rate}.",
        f"{money} is at risk across the persisted failed-payment evidence.",
        f"The provider boundary reported {incident.error_reason} for {bank} {method} payments.",
    ]
    hypotheses = [
        (
            f"{bank} or its {method} path may be degraded; provider-side confirmation "
            "is still required."
        ),
        (
            "A broader payment-network timeout is possible if the same signature "
            "appears across providers."
        ),
        (
            "A merchant regression is less likely from this evidence, but has not "
            "been independently disproven."
        ),
    ]

    if _is_greeting(question):
        answer = (
            f"Hi! I’m connected to the verified {bank} {method} incident. I can "
            "explain why it opened, show how revenue at risk was calculated, review "
            "its evidence, or describe the safest next step."
        )
    elif _contains(question, "execute", "send link", "refund", "retry payment", "recover now"):
        answer = (
            "I cannot execute a money or customer-contact action from chat. I can "
            "explain the evidence or help frame a bounded proposal; execution remains "
            "behind policy checks and explicit approval."
        )
    elif _contains(question, "why", "cause", "root", "diagnos"):
        answer = (
            f"The confirmed boundary signal is `{incident.error_reason}` reported "
            f"against {bank} {method}. That identifies where the failure surfaced, "
            "not the provider's internal root cause. The most plausible explanation is "
            "provider-path degradation, but it remains a hypothesis until confirmed."
        )
    elif _contains(question, "impact", "money", "risk", "revenue", "amount"):
        answer = (
            f"The verified revenue exposure is {money}: "
            f"{incident.current_failure_count} failed payments during the current "
            f"window. The failure rate is {current_rate}, compared with a "
            f"{baseline_rate} baseline. This is money at risk, not confirmed permanent loss."
        )
    elif _contains(question, "evidence", "verify", "proof", "trust", "consistent"):
        state = "reconciles" if verified else "does not reconcile"
        answer = (
            f"The incident snapshot {state} with its persisted evidence. I found "
            f"{len(facts)} payment facts "
            f"and {len(metrics)} metric snapshot. The evidence totals {money}."
        )
    elif _contains(question, "next", "recommend", "action", "plan", "do"):
        answer = (
            f"First continue read-only monitoring of the {bank} {method} segment and "
            "check whether the timeout signature persists. If recovery is needed, "
            "prepare a bounded proposal referencing this evidence. The proposal must "
            "remain non-executable until the merchant approves it."
        )
    else:
        answer = (
            f"This is an open {method} payment-failure incident affecting {bank}. "
            f"{incident.current_failure_count} of {incident.current_attempt_count} "
            f"current attempts failed, raising the failure rate from {baseline_rate} "
            f"to {current_rate}. Verified revenue at risk is {money}. Ask me about the "
            "evidence, likely cause, revenue impact, or safest next step."
        )

    return IncidentCommanderChatResponse(
        answer=answer,
        confirmed_facts=confirmed,
        hypotheses=hypotheses if _contains(question, "why", "cause", "root", "diagnos") else [],
        suggested_prompts=[
            "Why did this incident open?",
            "How was revenue at risk calculated?",
            "What is the safest next step?",
        ],
        evidence_count=len(evidence),
        evidence_verified=verified,
        safety_notice=SAFETY_NOTICE,
    )


def _contains(question: str, *terms: str) -> bool:
    return any(term in question for term in terms)


def _is_greeting(question: str) -> bool:
    greetings = {
        "hi",
        "hii",
        "hiii",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    }
    return question in greetings or any(
        question.startswith(f"{greeting} ") for greeting in ("hi", "hello", "hey")
    )


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_inr(subunits: int) -> str:
    return f"₹{subunits / 100:,.0f}"
