import { useState } from 'react'
import { Check, Clock3, FileCheck2, LockKeyhole, ShieldCheck, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'

import type { Incident, Proposal } from '../../lib/api'
import { formatMoney, formatTime } from '../../lib/format'
import {
  type RecoveryWorkflowState,
  recoveryWorkflowIsActive,
} from '../../lib/recoveryWorkflow'
import { ProposalGeneration } from './ProposalGeneration'

interface DecisionPanelProps {
  incident: Incident | null
  proposal: Proposal | null | undefined
  loading: boolean
  deciding: boolean
  workflow: RecoveryWorkflowState
  onDecide: (proposalId: string, action: 'approve' | 'reject', reason?: string) => void
}

export function DecisionPanel({
  incident,
  proposal,
  loading,
  deciding,
  workflow,
  onDecide,
}: DecisionPanelProps) {
  const [reason, setReason] = useState('')
  const pending = proposal?.status === 'pending_approval'
  const generating = recoveryWorkflowIsActive(workflow.stage)
  const generationFailed = workflow.stage === 'failed'

  return (
    <section className="mission-panel decision-panel" aria-label="Merchant decision">
      <div className="mission-panel-heading">
        <div><span className="panel-index">03</span><div><p>Authority boundary</p><h2>Decision required</h2></div></div>
        <span className={`decision-status ${pending ? 'needs-attention' : ''}`}>
          {generating
            ? 'Agent working'
            : generationFailed
              ? 'Needs attention'
              : loading
                ? 'Checking'
                : pending
                  ? 'Review now'
                  : proposal
                    ? proposal.status.replaceAll('_', ' ')
                    : 'Standby'}
        </span>
      </div>

      <AnimatePresence mode="wait">
        {generating || generationFailed ? (
          <ProposalGeneration key={`workflow-${workflow.stage}`} stage={workflow.stage} error={workflow.error} />
        ) : loading ? (
          <motion.div className="decision-loading" key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <span /><span /><span />
          </motion.div>
        ) : proposal ? (
          <motion.div className="decision-content" key={proposal.proposal_id} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }}>
            <div className="approval-path">
              <ApprovalStep label="Incident investigated" done />
              <ApprovalStep label="Evidence verified" done />
              <ApprovalStep label="Policy checks passed" done={proposal.policy_allowed} />
              <ApprovalStep label="Merchant decision" done={!pending} active={pending} />
            </div>

            <div className="proposal-brief">
              <div className="proposal-brief-heading"><span>Bounded proposal</span><Clock3 />Expires {formatTime(proposal.expires_at)}</div>
              <h3>{proposal.actions[0]?.action_type.replaceAll('_', ' ') ?? 'Recovery action'}</h3>
              <strong>{formatMoney(proposal.total_amount_subunits)} <small>maximum scope</small></strong>
              <div className="proposal-facts">
                <span><small>Included</small><b>{proposal.actions.length} actions</b></span>
                <span><small>Eligible</small><b>{proposal.eligible_payment_count ?? proposal.actions.length} payments</b></span>
                <span><small>Omitted</small><b>{proposal.omitted_payment_count ?? 0} by policy</b></span>
              </div>
              <div className="policy-stamp"><ShieldCheck /><span><strong>{proposal.policy_version}</strong><small>Exact amounts and evidence locked</small></span></div>
            </div>

            {pending ? (
              <div className="merchant-decision-form">
                <label htmlFor="decision-reason">Decision note <span>optional</span></label>
                <textarea id="decision-reason" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} placeholder="Why are you approving or rejecting this plan?" />
                <div>
                  <button className="reject-action" disabled={deciding} onClick={() => onDecide(proposal.proposal_id, 'reject', reason || undefined)}><X />Reject</button>
                  <button className="approve-action" disabled={deciding} onClick={() => onDecide(proposal.proposal_id, 'approve', reason || undefined)}><LockKeyhole />Approve exact plan</button>
                </div>
              </div>
            ) : (
              <div className={`decision-record ${proposal.status}`}><FileCheck2 /><div><strong>Immutable decision recorded</strong><span>The audit timeline preserves this exact proposal state.</span></div></div>
            )}
          </motion.div>
        ) : (
          <motion.div className="decision-empty" key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <div className="empty-lock"><LockKeyhole /></div>
            <strong>No proposal awaiting review</strong>
            <p>{incident
              ? 'Ask the connected recovery agent to prepare a bounded proposal. It will appear here after policy evaluation.'
              : 'Select an incident to inspect its recovery state.'}</p>
            <div className="empty-boundary"><span>AI proposes</span><i /><span>You authorize</span><i /><span>Executor acts</span></div>
          </motion.div>
        )}
      </AnimatePresence>

      <footer className="decision-safety"><ShieldCheck /><span><strong>No conversational execution</strong><small>Approval is mandatory for every recovery action.</small></span></footer>
    </section>
  )
}

function ApprovalStep({ label, done, active = false }: { label: string; done: boolean; active?: boolean }) {
  return <div className={`approval-step ${done ? 'is-done' : ''} ${active ? 'is-active' : ''}`}><span>{done ? <Check /> : null}</span><strong>{label}</strong></div>
}
