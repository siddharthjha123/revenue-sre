import type { ReactNode } from 'react'
import {
  Activity,
  BadgeCheck,
  CheckCircle2,
  CircleAlert,
  CircleDollarSign,
  FileCheck2,
  History,
  ShieldCheck,
} from 'lucide-react'
import { motion } from 'motion/react'

import type { AuditEvent, Incident } from '../../lib/api'
import { formatDate, formatMoney, formatTime } from '../../lib/format'
import { incidentIsOpen, incidentLabel } from '../../lib/incidents'

interface AuditViewProps {
  incidents: Incident[]
  selectedIncident: Incident | null
  auditEvents: AuditEvent[]
  onSelect: (incidentId: string) => void
}

const DECISION_EVENTS = new Set(['plan_approved', 'plan_rejected'])

export function AuditView({ incidents, selectedIncident, auditEvents, onSelect }: AuditViewProps) {
  const closedIncidents = incidents.filter((incident) => !incidentIsOpen(incident))
  const decisions = auditEvents.filter((event) => DECISION_EVENTS.has(event.event_type))
  const recoveredAmount = auditEvents
    .filter((event) => event.event_type === 'outcome_verified')
    .reduce((total, event) => total + numericDetail(event, 'recovered_amount_subunits'), 0)
  const selectedEvents = selectedIncident
    ? auditEvents.filter((event) => event.incident_id === selectedIncident.incident_id)
    : []
  const incidentGroups = groupIncidentsByDate(incidents)
  const outcomeVerified = selectedEvents.some((event) => event.event_type === 'outcome_verified')

  return (
    <div className="audit-layout">
      <section className="audit-summary">
        <SummaryCard icon={<History />} label="Recorded incidents" value={incidents.length.toLocaleString('en-IN')} />
        <SummaryCard icon={<CheckCircle2 />} label="Closed incidents" value={closedIncidents.length.toLocaleString('en-IN')} tone="success" />
        <SummaryCard icon={<BadgeCheck />} label="Merchant decisions" value={decisions.length.toLocaleString('en-IN')} />
        <SummaryCard icon={<CircleDollarSign />} label="Verified recovery" value={formatMoney(recoveredAmount)} tone="success" />
      </section>

      <section className="audit-workspace">
        <div className="mission-panel audit-index">
          <div className="mission-panel-heading">
            <div><span className="panel-index">A1</span><div><p>Daily archive</p><h2>Incident record</h2></div></div>
            <span className="append-only-badge"><ShieldCheck />Append-only</span>
          </div>
          {incidentGroups.map(([date, groupedIncidents]) => (
            <div className="audit-day-group" key={date}>
              <div className="audit-date"><span>{date}</span><i /></div>
              {groupedIncidents.map((incident) => (
                <button
                  key={incident.incident_id}
                  className={selectedIncident?.incident_id === incident.incident_id ? 'is-selected' : ''}
                  onClick={() => onSelect(incident.incident_id)}
                >
                  <span className={`audit-state ${incidentIsOpen(incident) ? 'open' : 'closed'}`} />
                  <span><strong>{incidentLabel(incident)}</strong><small>{incident.error_reason.replaceAll('_', ' ')}</small></span>
                  <span><strong>{formatMoney(incident.revenue_at_risk_subunits)}</strong><small>{friendlyIncidentStatus(incident.status)}</small></span>
                </button>
              ))}
            </div>
          ))}
        </div>

        <motion.section className="mission-panel audit-timeline" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <div className="mission-panel-heading">
            <div><span className="panel-index">A2</span><div><p>Control history</p><h2>{selectedIncident ? incidentLabel(selectedIncident) : 'Select an incident'}</h2></div></div>
            <span className="panel-count">{selectedEvents.length} events</span>
          </div>
          {selectedIncident && (
            <div className={`incident-outcome-banner ${incidentIsOpen(selectedIncident) ? 'is-open' : 'is-closed'}`}>
              {incidentIsOpen(selectedIncident) ? <CircleAlert /> : <CheckCircle2 />}
              <div>
                <strong>{incidentIsOpen(selectedIncident) ? 'Incident remains open' : `Incident ${friendlyIncidentStatus(selectedIncident.status)}`}</strong>
                <span>{incidentIsOpen(selectedIncident)
                  ? 'Approval records authority only. The incident closes after recovery is executed and its outcome is verified.'
                  : outcomeVerified
                    ? 'A verified recovery outcome is recorded in the timeline below.'
                    : 'The backend marked this incident non-actionable; inspect the timeline for the closing decision.'}</span>
              </div>
            </div>
          )}
          {!selectedIncident ? (
            <div className="audit-empty"><CircleAlert /><strong>No incident selected</strong><p>Select an incident to inspect its timeline.</p></div>
          ) : selectedEvents.length === 0 ? (
            <div className="audit-empty"><Activity /><strong>No control events found</strong><p>This incident has no persisted audit events yet.</p></div>
          ) : (
            <div className="timeline-list">
              {selectedEvents.map((event, index) => {
                const presentation = eventPresentation(event)
                return (
                  <article className={`tone-${presentation.tone}`} key={event.audit_id}>
                    <span className="timeline-marker"><FileCheck2 /></span>
                    <div>
                      <span>{event.event_type.replaceAll('_', ' ')}</span>
                      <strong>{presentation.title}</strong>
                      <small>{presentation.detail}</small>
                      <small>{formatTime(event.occurred_at)} · {event.actor_type ?? 'system'}{event.actor_id ? ` · ${event.actor_id}` : ''}</small>
                    </div>
                    <span className="timeline-order">{String(index + 1).padStart(2, '0')}</span>
                  </article>
                )
              })}
            </div>
          )}
        </motion.section>
      </section>
    </div>
  )
}

function SummaryCard({ icon, label, value, tone = 'default' }: { icon: ReactNode; label: string; value: string; tone?: 'default' | 'success' }) {
  return <article className={`tone-${tone}`}>{icon}<span><small>{label}</small><strong>{value}</strong></span></article>
}

function groupIncidentsByDate(incidents: Incident[]) {
  const groups = new Map<string, Incident[]>()
  for (const incident of incidents) {
    const date = formatDate(incident.opened_at)
    groups.set(date, [...(groups.get(date) ?? []), incident])
  }
  return [...groups.entries()]
}

function numericDetail(event: AuditEvent, key: string) {
  const value = Number(event.details?.[key] ?? 0)
  return Number.isFinite(value) ? value : 0
}

function friendlyIncidentStatus(status: string) {
  if (status === 'resolved') return 'closed · resolved'
  if (status === 'mitigated') return 'closed · mitigated'
  if (status === 'false_positive') return 'closed · false positive'
  return status
}

function eventPresentation(event: AuditEvent): { title: string; detail: string; tone: 'default' | 'success' | 'warning' } {
  const amount = numericDetail(event, 'recovered_amount_subunits')
  const actionCount = numericDetail(event, 'selected_action_count')
  const presentations: Record<string, { title: string; detail: string; tone: 'default' | 'success' | 'warning' }> = {
    incident_created: { title: 'Failure incident opened', detail: 'Deterministic thresholds created a durable incident record.', tone: 'warning' },
    analysis_completed: { title: 'Evidence analysis completed', detail: 'The investigation result was attached to the incident.', tone: 'default' },
    plan_proposed: { title: 'Bounded recovery proposal persisted', detail: actionCount ? `${actionCount} evidence-bound actions were proposed.` : 'Exact actions and evidence were locked for review.', tone: 'default' },
    policy_validated: { title: event.details?.allowed === false ? 'Policy validation failed' : 'Deterministic policy checks passed', detail: event.details?.allowed === false ? 'The proposal remained blocked.' : 'The bounded scope was eligible for merchant review.', tone: event.details?.allowed === false ? 'warning' : 'success' },
    approval_requested: { title: 'Merchant decision requested', detail: 'Execution remained blocked at the authority boundary.', tone: 'default' },
    plan_approved: { title: 'Exact proposal approved by merchant', detail: 'Authority was recorded; this did not execute a Razorpay action.', tone: 'success' },
    plan_rejected: { title: 'Proposal rejected by merchant', detail: 'The rejected scope cannot be executed.', tone: 'warning' },
    action_executed: { title: 'Recovery action executed', detail: 'The executor recorded a bounded Razorpay action.', tone: 'success' },
    action_failed: { title: 'Recovery action failed', detail: 'The attempted recovery did not complete.', tone: 'warning' },
    action_skipped: { title: 'Recovery action skipped', detail: 'A stopping condition prevented execution.', tone: 'warning' },
    outcome_verified: { title: 'Recovery outcome verified', detail: amount ? `${formatMoney(amount)} was confirmed recovered.` : 'The post-action payment outcome was verified.', tone: 'success' },
  }
  return presentations[event.event_type] ?? { title: 'Control event persisted', detail: 'This append-only event is part of the incident record.', tone: 'default' }
}
