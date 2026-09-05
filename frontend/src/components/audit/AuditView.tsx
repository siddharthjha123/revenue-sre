import { Activity, CheckCircle2, CircleAlert, FileCheck2, History, ShieldCheck } from 'lucide-react'
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

export function AuditView({ incidents, selectedIncident, auditEvents, onSelect }: AuditViewProps) {
  const closedIncidents = incidents.filter((incident) => !incidentIsOpen(incident))

  return (
    <div className="audit-layout">
      <section className="audit-summary">
        <article><History /><span><small>Recorded incidents</small><strong>{incidents.length}</strong></span></article>
        <article><CheckCircle2 /><span><small>Closed incidents</small><strong>{closedIncidents.length}</strong></span></article>
        <article><ShieldCheck /><span><small>Audit integrity</small><strong>Append-only</strong></span></article>
      </section>

      <section className="audit-workspace">
        <div className="mission-panel audit-index">
          <div className="mission-panel-heading"><div><span className="panel-index">A1</span><div><p>Daily archive</p><h2>Incident record</h2></div></div></div>
          <div className="audit-date"><span>{formatDate(new Date().toISOString())}</span><i /></div>
          {incidents.map((incident) => (
            <button
              key={incident.incident_id}
              className={selectedIncident?.incident_id === incident.incident_id ? 'is-selected' : ''}
              onClick={() => onSelect(incident.incident_id)}
            >
              <span className={`audit-state ${incidentIsOpen(incident) ? 'open' : 'closed'}`} />
              <span><strong>{incidentLabel(incident)}</strong><small>{incident.error_reason.replaceAll('_', ' ')}</small></span>
              <span><strong>{formatMoney(incident.revenue_at_risk_subunits)}</strong><small>{incident.status}</small></span>
            </button>
          ))}
        </div>

        <motion.section className="mission-panel audit-timeline" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <div className="mission-panel-heading"><div><span className="panel-index">A2</span><div><p>Control history</p><h2>{selectedIncident ? incidentLabel(selectedIncident) : 'Select an incident'}</h2></div></div><span className="panel-count">{auditEvents.length} events</span></div>
          {!selectedIncident ? (
            <div className="audit-empty"><CircleAlert /><strong>No incident selected</strong><p>Select an incident to inspect its timeline.</p></div>
          ) : auditEvents.length === 0 ? (
            <div className="audit-empty"><Activity /><strong>Detection recorded</strong><p>No recovery proposal or merchant decision has been added yet.</p></div>
          ) : (
            <div className="timeline-list">
              {auditEvents.map((event, index) => (
                <article key={event.audit_id}>
                  <span className="timeline-marker"><FileCheck2 /></span>
                  <div>
                    <span>{event.event_type.replaceAll('_', ' ')}</span>
                    <strong>{event.details?.summary ? String(event.details.summary) : 'Control event persisted'}</strong>
                    <small>{formatTime(event.occurred_at)} · {event.actor_type ?? 'system'}</small>
                  </div>
                  <span className="timeline-order">{String(index + 1).padStart(2, '0')}</span>
                </article>
              ))}
            </div>
          )}
        </motion.section>
      </section>
    </div>
  )
}
