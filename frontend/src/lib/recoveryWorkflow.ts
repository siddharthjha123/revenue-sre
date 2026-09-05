export type RecoveryWorkflowStage =
  | 'idle'
  | 'requested'
  | 'investigating'
  | 'evidence_verified'
  | 'policy_checking'
  | 'persisting'
  | 'ready'
  | 'failed'

export interface RecoveryWorkflowState {
  incidentId: string | null
  stage: RecoveryWorkflowStage
  error?: string
}

const PROPOSAL_VERBS = /\b(create|prepare|generate|build|draft|plan)\b/i
const PROPOSAL_NOUN = /\b(bounded\s+)?(recovery\s+)?proposal\b/i

export function isRecoveryProposalRequest(message: string) {
  return PROPOSAL_VERBS.test(message) && PROPOSAL_NOUN.test(message)
}

export function recoveryWorkflowIsActive(stage: RecoveryWorkflowStage) {
  return [
    'requested',
    'investigating',
    'evidence_verified',
    'policy_checking',
    'persisting',
  ].includes(stage)
}

export function workflowStageRank(stage: RecoveryWorkflowStage) {
  switch (stage) {
    case 'requested': return 0
    case 'investigating': return 1
    case 'evidence_verified': return 2
    case 'policy_checking': return 3
    case 'persisting': return 4
    case 'ready': return 5
    default: return -1
  }
}
