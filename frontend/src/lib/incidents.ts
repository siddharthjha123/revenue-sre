import type { Incident } from './api'

export function incidentLabel(incident: Incident) {
  return `${incident.bank ?? 'Unknown provider'} · ${incident.method.toUpperCase()}`
}

export function incidentIsOpen(incident: Incident) {
  return ['open', 'investigating'].includes(incident.status)
}

export function evidenceIsVerified(incident: Incident) {
  const facts = incident.evidence.filter((item) => item.kind === 'razorpay_fact')
  const snapshots = incident.evidence.filter((item) => item.kind === 'sandbox_metric')
  const evidenceRisk = facts.reduce(
    (total, item) => total + Number(item.details.amount_subunits ?? 0),
    0,
  )
  return (
    facts.length === incident.current_failure_count &&
    evidenceRisk === incident.revenue_at_risk_subunits &&
    snapshots.length === 1
  )
}

export function incidentSeverity(incident: Incident) {
  if (incident.current_failure_rate >= 0.5 || incident.revenue_at_risk_subunits >= 1_000_000) {
    return 'critical'
  }
  if (incident.current_failure_rate >= 0.3) return 'high'
  return 'watch'
}
