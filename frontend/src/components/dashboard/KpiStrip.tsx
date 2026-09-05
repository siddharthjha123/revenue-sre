import type { ReactNode } from 'react'
import { AlertTriangle, BanknoteArrowUp, CheckCircle2, CircleDollarSign, ScanLine, TrendingUp } from 'lucide-react'
import { motion } from 'motion/react'

import type { CurrencyAmount, DashboardSummary } from '../../lib/api'
import { formatMoney } from '../../lib/format'

interface KpiStripProps {
  summary: DashboardSummary
}

interface KpiCardProps {
  icon: ReactNode
  label: string
  value: string
  detail: ReactNode
  tone: 'neutral' | 'success' | 'warning' | 'danger' | 'ai'
  pending?: boolean
}

function KpiCard({ icon, label, value, detail, tone, pending }: KpiCardProps) {
  return (
    <motion.article
      className={`kpi-card tone-${tone}`}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div className="kpi-heading"><span>{label}</span><span className="kpi-icon">{icon}</span></div>
      <strong className={pending ? 'is-pending' : ''}>{value}</strong>
      <small>{detail}</small>
      <span className="kpi-meter" aria-hidden="true"><i /></span>
    </motion.article>
  )
}

function preferredAmount(amounts: CurrencyAmount[]) {
  return amounts.find((amount) => amount.currency === 'INR') ?? amounts[0] ?? {
    currency: 'INR',
    amount_subunits: 0,
  }
}

export function KpiStrip({ summary }: KpiStripProps) {
  const capturedRevenue = preferredAmount(summary.captured_revenue_today)
  const risk = preferredAmount(summary.open_revenue_at_risk)

  return (
    <section className="kpi-strip" aria-label="Today's payment overview">
      <KpiCard
        icon={<ScanLine />}
        label="Payment attempts"
        value={summary.total_payment_attempts.toLocaleString('en-IN')}
        detail="All recorded attempts"
        tone="neutral"
      />
      <KpiCard
        icon={<CheckCircle2 />}
        label="Payments captured"
        value={summary.captured_payment_count.toLocaleString('en-IN')}
        detail={(
          <span className="kpi-positive-detail">
            <TrendingUp /> Today's revenue {formatMoney(capturedRevenue.amount_subunits, capturedRevenue.currency)}
          </span>
        )}
        tone="success"
      />
      <KpiCard
        icon={<AlertTriangle />}
        label="Total incidents"
        value={summary.total_incident_count.toLocaleString('en-IN')}
        detail={`${summary.open_incident_count} currently open`}
        tone="warning"
      />
      <KpiCard
        icon={<CircleDollarSign />}
        label="Amount at risk"
        value={formatMoney(risk.amount_subunits, risk.currency)}
        detail={`Across ${summary.open_incident_count} active segments`}
        tone="danger"
      />
      <KpiCard
        icon={<BanknoteArrowUp />}
        label="Amount recovered"
        value="—"
        detail="Outcome engine pending"
        tone="ai"
        pending
      />
    </section>
  )
}
