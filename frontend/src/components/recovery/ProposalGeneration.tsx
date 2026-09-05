import { AlertTriangle, Check, FileSearch, LoaderCircle, LockKeyhole, ShieldCheck } from 'lucide-react'
import { motion } from 'motion/react'

import {
  type RecoveryWorkflowStage,
  workflowStageRank,
} from '../../lib/recoveryWorkflow'

interface ProposalGenerationProps {
  stage: RecoveryWorkflowStage
  error?: string
}

const STEPS = [
  { label: 'Incident investigated', rank: 1 },
  { label: 'Evidence verified', rank: 2 },
  { label: 'Policy checks passed', rank: 4 },
  { label: 'Merchant decision', rank: 6 },
]

const STATUS_COPY: Partial<Record<RecoveryWorkflowStage, { title: string; detail: string }>> = {
  requested: {
    title: 'Recovery Planner engaged',
    detail: 'Attaching the selected incident and opening the approval-safe workflow.',
  },
  investigating: {
    title: 'Inspecting incident evidence',
    detail: 'The agent is reconciling failed payments, metrics and revenue exposure.',
  },
  evidence_verified: {
    title: 'Evidence is internally consistent',
    detail: 'Verified facts are now being converted into eligible recovery candidates.',
  },
  policy_checking: {
    title: 'Applying deterministic limits',
    detail: 'Action count, exact amounts, exclusions, expiry and contact limits are being enforced.',
  },
  persisting: {
    title: 'Preparing merchant review',
    detail: 'The immutable proposal scope is being loaded from the Revenue SRE backend.',
  },
}

export function ProposalGeneration({ stage, error }: ProposalGenerationProps) {
  if (stage === 'failed') {
    return (
      <motion.div className="proposal-generation failed" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <div className="generation-error-icon"><AlertTriangle /></div>
        <strong>Proposal was not created</strong>
        <p>{error ?? 'The agent did not return a persisted bounded proposal.'}</p>
        <small>No recovery or Razorpay action was executed.</small>
      </motion.div>
    )
  }

  const rank = workflowStageRank(stage)
  const copy = STATUS_COPY[stage] ?? STATUS_COPY.requested!

  return (
    <motion.div className="proposal-generation" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <div className="approval-path generation-path" aria-label="Proposal generation progress">
        {STEPS.map((step, index) => {
          const done = rank >= step.rank
          const active = !done && (index === 0 || rank >= STEPS[index - 1].rank)
          return (
            <div className={`approval-step ${done ? 'is-done' : ''} ${active ? 'is-active' : ''}`} key={step.label}>
              <motion.span
                animate={done ? { scale: [0.82, 1.14, 1] } : { scale: 1 }}
                transition={{ duration: 0.42 }}
              >
                {done ? <Check /> : active ? <LoaderCircle className="spin" /> : null}
              </motion.span>
              <strong>{step.label}</strong>
            </div>
          )
        })}
      </div>

      <div className="generation-visual">
        <div className="generation-orbit"><FileSearch /><i /><i /></div>
        <p>AI-GENERATED · POLICY-BOUND</p>
        <h3>{copy.title}</h3>
        <span>{copy.detail}</span>
      </div>

      <div className="generation-skeleton" aria-hidden="true">
        <span /><span /><span />
      </div>

      <div className="generation-guardrail">
        <ShieldCheck />
        <span><strong>Approval boundary active</strong><small>The agent can propose; only the merchant can authorize.</small></span>
        <LockKeyhole />
      </div>
    </motion.div>
  )
}
