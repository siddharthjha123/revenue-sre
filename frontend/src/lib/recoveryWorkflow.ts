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
const PROPOSAL_STATUS_WORDS = /\b(exist|exists|existing|already|status|current|ready|created|made)\b/i
const AFFIRMATIVE_REPLY = /^\s*(yes|yeah|yep|sure|okay|ok|please|go ahead)\b/i

export function isRecoveryProposalRequest(message: string) {
  return PROPOSAL_NOUN.test(message) && (
    PROPOSAL_VERBS.test(message) || AFFIRMATIVE_REPLY.test(message)
  )
}

export function isProposalStatusRequest(message: string) {
  return (
    PROPOSAL_NOUN.test(message) &&
    PROPOSAL_STATUS_WORDS.test(message) &&
    !PROPOSAL_VERBS.test(message)
  )
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
