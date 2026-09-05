import { useId, useRef, useState } from 'react'
import { ChevronDown, CircleAlert, CircleHelp, GripVertical, ShieldCheck } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'

import type { Incident } from '../../lib/api'
import { formatDateTime, formatMoney, formatPercent, formatTime } from '../../lib/format'
import { evidenceIsVerified, incidentLabel, incidentSeverity } from '../../lib/incidents'

interface IncidentQueueProps {
  incidents: Incident[]
  selectedId: string | null
  onSelect: (incidentId: string) => void
}

type MetricKey = 'failure-rate' | 'baseline-rate' | 'failed-attempts'

interface IncidentMetricProps {
  metricKey: MetricKey
  label: string
  value: string
  calculation: string
  description: string
  windowLabel: string
  windowStart: string
  windowEnd: string
  align: 'start' | 'center' | 'end'
  openMetric: MetricKey | null
  onToggle: (metric: MetricKey | null) => void
}

function IncidentMetric({
  metricKey,
  label,
  value,
  calculation,
  description,
  windowLabel,
  windowStart,
  windowEnd,
  align,
  openMetric,
  onToggle,
}: IncidentMetricProps) {
  const popoverId = useId()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const isOpen = openMetric === metricKey

  return (
    <div
      ref={wrapperRef}
      className={`incident-metric align-${align}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) onToggle(null)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onToggle(null)
      }}
    >
      <button
        type="button"
        className="incident-metric-trigger"
        aria-expanded={isOpen}
        aria-controls={popoverId}
        onClick={() => onToggle(isOpen ? null : metricKey)}
      >
        <span>{label}<CircleHelp aria-hidden="true" /></span>
        <strong>{value}</strong>
      </button>
      <AnimatePresence>
        {isOpen && (
          <motion.div
            id={popoverId}
            role="dialog"
            aria-label={`${label} explanation`}
            className="metric-popover"
            initial={{ opacity: 0, y: 5, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 5, scale: 0.98 }}
            transition={{ duration: 0.14 }}
          >
            <span className="metric-popover-kicker">{windowLabel}</span>
            <strong>{calculation}</strong>
            <p>{description}</p>
            <time>{formatDateTime(windowStart)} — {formatDateTime(windowEnd)}</time>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export function IncidentQueue({ incidents, selectedId, onSelect }: IncidentQueueProps) {
  const [openMetric, setOpenMetric] = useState<MetricKey | null>(null)

  return (
    <section className="mission-panel incident-queue" aria-label="Open incident queue">
      <div className="mission-panel-heading">
        <div><span className="panel-index">01</span><div><p>Priority desk</p><h2>Incident queue</h2></div></div>
        <span className="panel-count">{incidents.length} open</span>
      </div>

      <div className="incident-stack">
        {incidents.length === 0 && (
          <div className="queue-empty"><ShieldCheck /><strong>No active incidents</strong><span>The detector is monitoring incoming payment events.</span></div>
        )}
        {incidents.map((incident) => {
          const selected = selectedId === incident.incident_id
          const verified = evidenceIsVerified(incident)
          const severity = incidentSeverity(incident)
          return (
            <article
              key={incident.incident_id}
              className={`incident-mission severity-${severity} ${selected ? 'is-selected' : ''}`}
              draggable
              onDragStart={(event) => {
                event.dataTransfer.setData('application/revenue-sre-incident', incident.incident_id)
                event.dataTransfer.effectAllowed = 'copy'
              }}
            >
              <button
                className="incident-summary"
                onClick={() => {
                  setOpenMetric(null)
                  onSelect(incident.incident_id)
                }}
              >
                <GripVertical className="drag-handle" aria-hidden="true" />
                <span className="incident-order" title={incident.incident_id}>
                  INC · {incident.incident_id.slice(0, 6).toUpperCase()}
                </span>
                <span className="incident-identity">
                  <strong>{incidentLabel(incident)}</strong>
                  <small>{incident.error_reason.replaceAll('_', ' ')}</small>
                </span>
                <span className={`severity-label ${severity}`}>{severity}</span>
                <span className="incident-risk">
                  {formatMoney(incident.revenue_at_risk_subunits, incident.currency)}
                </span>
                <ChevronDown className={selected ? 'is-rotated' : ''} />
              </button>

              <AnimatePresence initial={false}>
                {selected && (
                  <motion.div
                    className="incident-expanded"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                  >
                    <div className="incident-signal">
                      <IncidentMetric
                        metricKey="failure-rate"
                        label="Failure rate"
                        value={formatPercent(incident.current_failure_rate)}
                        calculation={`${incident.current_failure_count} failed ÷ ${incident.current_attempt_count} attempts`}
                        description="The share of payment attempts that failed in the detector's current observation window."
                        windowLabel="Current observation window"
                        windowStart={incident.current_window_start}
                        windowEnd={incident.current_window_end}
                        align="start"
                        openMetric={openMetric}
                        onToggle={setOpenMetric}
                      />
                      <IncidentMetric
                        metricKey="baseline-rate"
                        label="Baseline rate"
                        value={formatPercent(incident.baseline_failure_rate)}
                        calculation={`${incident.baseline_failure_count} failed ÷ ${incident.baseline_attempt_count} attempts`}
                        description="The normal comparison rate immediately before the current observation window."
                        windowLabel="Baseline comparison window"
                        windowStart={incident.baseline_window_start}
                        windowEnd={incident.current_window_start}
                        align="center"
                        openMetric={openMetric}
                        onToggle={setOpenMetric}
                      />
                      <IncidentMetric
                        metricKey="failed-attempts"
                        label="Failed"
                        value={`${incident.current_failure_count}/${incident.current_attempt_count}`}
                        calculation={`${incident.current_failure_count} failures from ${incident.current_attempt_count} attempts`}
                        description="The raw failure count used to calculate the current failure rate."
                        windowLabel="Current observation window"
                        windowStart={incident.current_window_start}
                        windowEnd={incident.current_window_end}
                        align="end"
                        openMetric={openMetric}
                        onToggle={setOpenMetric}
                      />
                    </div>
                    <div className="signal-track" aria-label={`${formatPercent(incident.current_failure_rate)} failure rate`}>
                      <i style={{ width: `${Math.min(100, incident.current_failure_rate * 100)}%` }} />
                    </div>
                    <div className="incident-footnote">
                      <span className={verified ? 'verified' : 'unverified'}>
                        {verified ? <ShieldCheck /> : <CircleAlert />}
                        {verified ? 'Evidence verified' : 'Verification required'}
                      </span>
                      <span>Detected {formatTime(incident.last_detected_at)}</span>
                    </div>
                    <button className="attach-control" onClick={() => onSelect(incident.incident_id)}>
                      Attach to Revenue operator
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </article>
          )
        })}
      </div>
      <p className="drag-hint"><GripVertical />Drag an incident into the operator to change context.</p>
    </section>
  )
}
