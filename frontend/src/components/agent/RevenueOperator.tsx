import {
  type DragEvent,
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, LoaderCircle, Send, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'

import {
  createBoundedProposal,
  getActiveProposal,
  type Incident,
  type Proposal,
} from '../../lib/api'
import { formatMoney } from '../../lib/format'
import { evidenceIsVerified, incidentLabel } from '../../lib/incidents'
import { getExecutiveBriefing, streamIncidentTurn } from '../../lib/trueforge'
import {
  isRecoveryProposalRequest,
  isProposalStatusRequest,
  type RecoveryWorkflowStage,
} from '../../lib/recoveryWorkflow'
import { RobotAvatar } from '../ui/RobotAvatar'

type ChatMessage = {
  id: number
  role: 'assistant' | 'user'
  content: string
  specialist?: string
  streaming?: boolean
}

interface RevenueOperatorProps {
  activeIncident: Incident | null
  incidents: Incident[]
  proposal: Proposal | null | undefined
  onAttachIncident: (incidentId: string) => void
  onRecoveryStage: (stage: RecoveryWorkflowStage, error?: string) => void
}

export function RevenueOperator({
  activeIncident,
  incidents,
  proposal,
  onAttachIncident,
  onRecoveryStage,
}: RevenueOperatorProps) {
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const briefing = useMemo(
    () => getExecutiveBriefing(activeIncident, incidents.length),
    [activeIncident, incidents.length],
  )
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 0, role: 'assistant', content: '', specialist: 'Revenue operator', streaming: true },
  ])
  const [activity, setActivity] = useState('Preparing live incident briefing')
  const [briefingStreaming, setBriefingStreaming] = useState(true)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const messageSequence = useRef(1)

  useEffect(() => {
    let characterIndex = 0
    const timer = window.setInterval(() => {
      characterIndex = Math.min(characterIndex + 3, briefing.length)
      setMessages((current) => current.map((message) =>
        message.id === 0
          ? { ...message, content: briefing.slice(0, characterIndex), streaming: characterIndex < briefing.length }
          : message,
      ))
      if (characterIndex === briefing.length) {
        window.clearInterval(timer)
        setBriefingStreaming(false)
        setActivity('Specialists ready')
      }
    }, 18)
    return () => window.clearInterval(timer)
  }, [briefing])

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript) transcript.scrollTop = transcript.scrollHeight
  }, [activity, messages])

  const chat = useMutation({
    mutationFn: async ({
      message,
      responseId,
      proposalRequested,
      proposalStatusRequested,
    }: {
      message: string
      responseId: number
      proposalRequested: boolean
      proposalStatusRequested: boolean
    }) => {
      if (!activeIncident) throw new Error('Attach an incident before asking a question.')
      if (proposalStatusRequested) {
        setActivity('Checking persisted proposal state')
        const persistedProposal = await getActiveProposal(activeIncident.incident_id)
        return {
          content: proposalStatusConfirmation(persistedProposal),
          toolsCompleted: true,
          proposalCreated: false,
          proposalAvailable: Boolean(persistedProposal),
          proposalReused: true,
        }
      }
      if (proposalRequested) {
        const persistedProposal = proposal ?? await getActiveProposal(activeIncident.incident_id)
        if (persistedProposal) {
          onRecoveryStage('ready')
          return {
            content: proposalStatusConfirmation(persistedProposal),
            toolsCompleted: true,
            proposalCreated: false,
            proposalAvailable: true,
            proposalReused: true,
          }
        }
        setActivity('Verifying evidence for bounded recovery')
        onRecoveryStage('investigating')
        const createdProposal = await createBoundedProposal(activeIncident.incident_id)
        onRecoveryStage('evidence_verified')
        await pause(260)
        setActivity('Applying deterministic recovery policy')
        onRecoveryStage('policy_checking')
        await pause(320)
        setActivity('Loading proposal for merchant review')
        onRecoveryStage('persisting')
        return {
          content: proposalConfirmation(createdProposal),
          toolsCompleted: true,
          proposalCreated: true,
          proposalAvailable: true,
          proposalReused: false,
        }
      }
      const result = await streamIncidentTurn(activeIncident, message, proposal, {
        onDelta: (content) => setMessages((current) => current.map((item) =>
          item.id === responseId ? { ...item, content, streaming: true } : item,
        )),
        onStatus: setActivity,
        onRecoveryStage: proposalRequested ? (stage) => onRecoveryStage(stage) : undefined,
      })
      return { ...result, proposalAvailable: false, proposalReused: false }
    },
    onSuccess: (result, { responseId, proposalRequested }) => {
      setMessages((current) => current.map((item) =>
        item.id === responseId ? { ...item, content: result.content, streaming: false } : item,
      ))
      setActivity(result.toolsCompleted ? 'Investigation complete · evidence connected' : 'Response complete')
      if (proposalRequested) {
        onRecoveryStage(
          result.proposalReused ? 'ready' : result.proposalCreated ? 'persisting' : 'failed',
          result.proposalCreated || result.proposalReused
            ? undefined
            : 'The agent completed without creating a bounded proposal.',
        )
      }
      if (activeIncident) {
        void queryClient.invalidateQueries({ queryKey: ['incident-proposal', activeIncident.incident_id] })
        void queryClient.invalidateQueries({ queryKey: ['merchant-audit'] })
        void queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] })
      }
    },
    onError: (error, { responseId, proposalRequested }) => {
      setMessages((current) => current.map((item) => item.id === responseId
        ? {
            ...item,
            content: `I could not complete the TrueForge investigation: ${error.message}`,
            streaming: false,
          }
        : item,
      ))
      setActivity('Agent connection needs attention')
      if (proposalRequested) onRecoveryStage('failed', error.message)
    },
  })

  const send = (question: string) => {
    const message = question.trim()
    if (!message || chat.isPending || briefingStreaming || !activeIncident) return
    const userId = messageSequence.current++
    const responseId = messageSequence.current++
    const proposalStatusRequested = isProposalStatusRequest(message)
    const proposalRequested = !proposalStatusRequested && isRecoveryProposalRequest(message)
    if (proposalRequested) onRecoveryStage('requested')
    setMessages((current) => [
      ...current,
      { id: userId, role: 'user', content: message },
      {
        id: responseId,
        role: 'assistant',
        content: '',
        specialist: 'Incident Commander',
        streaming: true,
      },
    ])
    setInput('')
    setActivity('Opening TrueForge agent session')
    chat.mutate({ message, responseId, proposalRequested, proposalStatusRequested })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    send(input)
  }

  const acceptDrop = (event: DragEvent) => {
    event.preventDefault()
    const incidentId = event.dataTransfer.getData('application/revenue-sre-incident')
    if (incidentId) onAttachIncident(incidentId)
  }

  const clear = () => {
    chat.reset()
    setInput('')
    setMessages([])
    setActivity('Conversation cleared · agent context retained')
  }

  const prompts = [
    ['Summarize incident', 'Give me a concise summary of this incident.'],
    ['Explain evidence', 'Explain the evidence and what is confirmed.'],
    ['Safest next step', 'What is the safest next step?'],
    [
      'Plan recovery',
      'Prepare one bounded recovery proposal for this incident using verified evidence and deterministic policy limits. Keep it pending merchant approval and do not execute any Razorpay action.',
    ],
  ] as const

  return (
    <section
      className={`mission-panel operator-panel ${activeIncident ? '' : 'is-empty'}`}
      onDragOver={(event) => event.preventDefault()}
      onDrop={acceptDrop}
      aria-label="Revenue operator"
    >
      <div className="mission-panel-heading operator-heading">
        <div><span className="panel-index">02</span><div><p>AI operations</p><h2>Revenue operator</h2></div></div>
        <div className="operator-status"><span className="live-orb" />Online</div>
      </div>

      <div className="operator-roster">
        <RobotAvatar active={chat.isPending || briefingStreaming} />
        <div>
          <strong>{activity}</strong>
          <span>Incident Commander · Evidence Verifier · Recovery Planner</span>
        </div>
        <span className="roster-count">3 agents</span>
      </div>

      <div className="attached-context">
        <div><span>Attached context</span><strong>{activeIncident ? incidentLabel(activeIncident) : 'Drop an incident here'}</strong></div>
        {activeIncident && (
          <span className={evidenceIsVerified(activeIncident) ? 'context-verified' : 'context-warning'}>
            {evidenceIsVerified(activeIncident) ? <ShieldCheck /> : <Bot />}
            {evidenceIsVerified(activeIncident) ? 'Verified' : 'Check evidence'}
          </span>
        )}
      </div>

      <div className="operator-transcript" aria-live="polite" ref={transcriptRef}>
        <AnimatePresence initial={false}>
          {messages.map((message) => (
            <motion.div
              key={message.id}
              className={`operator-message ${message.role}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
            >
              {message.role === 'assistant' && (
                <span className="message-author"><Sparkles />{message.specialist}</span>
              )}
              {message.role === 'assistant'
                ? <AgentMessageBody content={message.content} />
                : <p>{message.content}</p>}
              {message.streaming && message.content && <i className="typing-caret" aria-hidden="true" />}
            </motion.div>
          ))}
        </AnimatePresence>
        {chat.isPending && !messages.at(-1)?.content && (
          <div className="agent-handoff">
            <span><Check />Context secured</span><i /><span><LoaderCircle className="spin" />Incident Commander</span>
          </div>
        )}
      </div>

      <div className="operator-composer">
        <div className="operator-prompts">
          {prompts.map(([label, prompt]) => (
            <button key={label} disabled={!activeIncident || chat.isPending || briefingStreaming} onClick={() => send(prompt)}>{label}</button>
          ))}
        </div>
        <form onSubmit={submit}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={activeIncident ? 'Ask the operator about this incident…' : 'Attach an incident to begin…'}
            disabled={!activeIncident}
            maxLength={1000}
            aria-label="Message Revenue operator"
          />
          <button type="submit" disabled={!input.trim() || chat.isPending || briefingStreaming || !activeIncident} aria-label="Send message"><Send /></button>
          <button type="button" onClick={clear} disabled={chat.isPending || messages.length === 0} aria-label="Clear visible conversation while retaining agent context"><Trash2 /></button>
        </form>
        <p><ShieldCheck />Advisory channel only. Money actions cannot execute from chat.</p>
      </div>
    </section>
  )
}

function AgentMessageBody({ content }: { content: string }) {
  const lines = content.split('\n').map((line) => line.trim()).filter(Boolean)

  return (
    <div className="operator-message-body">
      {lines.map((line, index) => {
        const heading = line.match(/^#{1,4}\s+(.+)$/)
        if (heading) {
          return <strong className="agent-section-title" key={`${line}-${index}`}>{renderInline(heading[1])}</strong>
        }

        const bullet = line.match(/^[-*]\s+(.+)$/)
        if (bullet) {
          return <span className="agent-bullet" key={`${line}-${index}`}><span>{renderInline(bullet[1])}</span></span>
        }

        return <p key={`${line}-${index}`}>{renderInline(line)}</p>
      })}
    </div>
  )
}

function renderInline(value: string) {
  return value.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
    }
    return part
  })
}

function pause(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function proposalConfirmation(proposal: Proposal) {
  const approvalState = proposal.status === 'pending_approval'
    ? 'Merchant approval is required'
    : `Merchant decision is already ${proposal.status.replaceAll('_', ' ')}`
  return `Bounded proposal **${proposal.proposal_id}** is ready for review.\n` +
    `- **Status:** ${proposal.status.replaceAll('_', ' ')}\n` +
    `- **Scope:** ${proposal.action_count} actions, up to ${formatMoney(proposal.maximum_recoverable_amount_subunits)}\n` +
    `- **Policy:** ${proposal.policy_version}; ${proposal.omitted_payment_count ?? 0} eligible payments omitted by limits\n` +
    `- **Safety:** ${approvalState}. No Razorpay action was executed.`
}

function proposalStatusConfirmation(proposal: Proposal | null) {
  if (!proposal) {
    return 'No bounded recovery proposal is currently persisted for this incident. ' +
      'If you approve, I can prepare one from verified evidence and deterministic policy limits.'
  }

  const execution = proposal.execution_performed
    ? 'Recovery execution is recorded.'
    : 'No Razorpay recovery action has been executed.'
  return `Yes. Proposal **${proposal.proposal_id}** exists in the backend.\n` +
    `- **Status:** ${proposal.status.replaceAll('_', ' ')}\n` +
    `- **Scope:** ${proposal.action_count} actions, up to ${formatMoney(proposal.maximum_recoverable_amount_subunits)}\n` +
    `- **Decision:** ${proposal.status === 'pending_approval' ? 'Awaiting merchant approval' : `Merchant decision is ${proposal.status.replaceAll('_', ' ')}`}\n` +
    `- **Execution:** ${execution}`
}
