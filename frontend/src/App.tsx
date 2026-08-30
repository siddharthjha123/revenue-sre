import { type FormEvent, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity, AlertTriangle, ArrowRight, Bot, Check, CheckCircle2, ChevronRight,
  CircleDollarSign, Clock3, Command, FileCheck2, History, LayoutDashboard,
  LoaderCircle, MessageSquare, RefreshCcw, RotateCcw, Send, ShieldCheck,
  Sparkles, Trash2, TrendingUp, X, Zap,
} from 'lucide-react'
import { motion } from 'motion/react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { toast } from 'sonner'

import './App.css'
import './chat.css'
import { DashboardSkeleton } from './components/DashboardSkeleton'
import {
  askIncidentCommander, decideProposal, getActiveProposal, getIncident, getIncidentAudit, getIncidents,
} from './lib/api'

const money = (value: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', maximumFractionDigits: 0,
}).format(value / 100)
const percent = (value: number) => `${(value * 100).toFixed(1)}%`
const time = (value: string) => new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
}).format(new Date(value))

function App() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mobileNav, setMobileNav] = useState(false)
  const incidentsQuery = useQuery({ queryKey: ['incidents'], queryFn: getIncidents, refetchInterval: 10_000 })
  const openIncidents = useMemo(
    () => (incidentsQuery.data ?? []).filter((item) => ['open', 'investigating'].includes(item.status))
      .sort((a, b) => b.revenue_at_risk_subunits - a.revenue_at_risk_subunits),
    [incidentsQuery.data],
  )
  const activeIncidentId = selectedId ?? openIncidents[0]?.incident_id ?? null

  const detailQuery = useQuery({
    queryKey: ['incident', activeIncidentId], queryFn: () => getIncident(activeIncidentId!),
    enabled: Boolean(activeIncidentId), refetchInterval: 10_000,
  })
  const auditQuery = useQuery({
    queryKey: ['incident-audit', activeIncidentId], queryFn: () => getIncidentAudit(activeIncidentId!),
    enabled: Boolean(activeIncidentId),
  })
  const proposalQuery = useQuery({
    queryKey: ['incident-proposal', activeIncidentId], queryFn: () => getActiveProposal(activeIncidentId!),
    enabled: Boolean(activeIncidentId), refetchInterval: 5_000,
  })
  const decision = useMutation({
    mutationFn: ({ proposalId, action }: { proposalId: string; action: 'approve' | 'reject' }) =>
      decideProposal(proposalId, action),
    onSuccess: (result) => {
      toast.success(`Proposal ${result.decision}`, { description: 'The immutable decision is now in the audit trail.' })
      void queryClient.invalidateQueries({ queryKey: ['incident-proposal'] })
      void queryClient.invalidateQueries({ queryKey: ['incident-audit'] })
    },
    onError: (error) => toast.error('Decision could not be recorded', { description: error.message }),
  })

  if (incidentsQuery.isPending || (activeIncidentId && detailQuery.isPending)) return <DashboardSkeleton />
  if (incidentsQuery.isError) return <ErrorState message={incidentsQuery.error.message} retry={() => void incidentsQuery.refetch()} />

  const incident = detailQuery.data ?? openIncidents[0]
  if (!incident) return <EmptyState />

  const totalRisk = openIncidents.reduce((sum, item) => sum + item.revenue_at_risk_subunits, 0)
  const evaluated = openIncidents.reduce((sum, item) => sum + item.baseline_attempt_count + item.current_attempt_count, 0)
  const increase = incident.current_failure_rate - incident.baseline_failure_rate
  const multiplier = incident.baseline_failure_rate ? incident.current_failure_rate / incident.baseline_failure_rate : 0
  const facts = incident.evidence.filter((item) => item.kind === 'razorpay_fact')
  const metrics = incident.evidence.filter((item) => item.kind === 'sandbox_metric')
  const evidenceRisk = facts.reduce((sum, item) => sum + Number(item.details.amount_subunits ?? 0), 0)
  const verified = facts.length === incident.current_failure_count && evidenceRisk === incident.revenue_at_risk_subunits && metrics.length === 1
  const proposal = proposalQuery.data
  const chartData = [
    { window: 'Baseline window', rate: incident.baseline_failure_rate * 100 },
    { window: 'Current window', rate: incident.current_failure_rate * 100 },
  ]

  return (
    <div className="app-shell" id="top">
      <Sidebar open={mobileNav} close={() => setMobileNav(false)} />
      <main className="dashboard">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Command size={20} /></button>
          <div><div className="title-row"><h1>Revenue Command Center</h1><span className="live-pill"><span />Live</span></div><p>Payment health, verified evidence and merchant-controlled recovery.</p></div>
          <button className="refresh-button" onClick={() => void incidentsQuery.refetch()}><RefreshCcw size={16} className={incidentsQuery.isFetching ? 'spin' : ''} /><span>Refresh</span></button>
        </header>

        <section className="metric-grid" aria-label="Revenue health overview">
          <Metric icon={<Activity />} label="Attempts evaluated" value={evaluated.toString()} detail="Across detected segments" tone="blue" />
          <Metric icon={<AlertTriangle />} label="Open incidents" value={openIncidents.length.toString()} detail="Requires attention" tone="red" />
          <Metric icon={<CircleDollarSign />} label="Revenue at risk" value={money(totalRisk)} detail={`Across ${openIncidents.length} incidents`} tone="orange" />
          <Metric icon={<ShieldCheck />} label="Recovery executed" value={money(0)} detail="Execution remains disabled" tone="green" />
        </section>

        <section className="workspace-grid">
          <div className="primary-column">
            <motion.section className="panel chart-panel" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
              <div className="panel-heading"><div><p className="eyebrow">Highest-risk incident</p><h2>Payment failure rate · {incident.method.toUpperCase()} · {incident.bank ?? 'Unknown bank'}</h2></div><span className="window-chip"><Clock3 size={14} />5 minute window</span></div>
              <div className="chart-layout">
                <div className="chart-wrap">
                  <div className="legend-row"><span><i className="legend-dot baseline" />Baseline {percent(incident.baseline_failure_rate)}</span><span><i className="legend-dot current" />Current {percent(incident.current_failure_rate)}</span></div>
                  <ResponsiveContainer width="100%" height={275}>
                    <AreaChart data={chartData} margin={{ top: 24, right: 18, left: -8, bottom: 0 }}>
                      <defs><linearGradient id="failureGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#ff5d63" stopOpacity={0.38} /><stop offset="100%" stopColor="#ff5d63" stopOpacity={0.02} /></linearGradient></defs>
                      <CartesianGrid stroke="rgba(143, 164, 196, .12)" vertical={false} />
                      <XAxis dataKey="window" axisLine={false} tickLine={false} tick={{ fill: '#8494ab', fontSize: 12 }} />
                      <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} axisLine={false} tickLine={false} tick={{ fill: '#8494ab', fontSize: 12 }} />
                      <Tooltip content={<RateTooltip />} />
                      <Area type="monotone" dataKey="rate" stroke="#ff5d63" strokeWidth={3} fill="url(#failureGradient)" dot={{ fill: '#ff5d63', strokeWidth: 4, stroke: '#3b1520', r: 6 }} animationDuration={900} />
                    </AreaChart>
                  </ResponsiveContainer>
                  <div className="rate-callout"><TrendingUp size={16} /><strong>+{(increase * 100).toFixed(0)} percentage points</strong><span>{multiplier.toFixed(1)}× above baseline</span></div>
                </div>
                <aside className="incident-card">
                  <span className="critical-badge"><Zap size={13} />Critical</span><h3>{incident.bank} {incident.method.toUpperCase()} timeouts</h3>
                  <div className="incident-number"><strong>{incident.current_failure_count}</strong><span>failed payments</span></div>
                  <div className="risk-number">{money(incident.revenue_at_risk_subunits)} <span>at risk</span></div>
                  <div className={`verification-box ${verified ? 'verified' : ''}`}>{verified ? <ShieldCheck /> : <AlertTriangle />}<div><strong>{verified ? 'Evidence verified' : 'Verification required'}</strong><span>{facts.length} current facts · {metrics.length} metric snapshot</span></div></div>
                </aside>
              </div>
            </motion.section>

            <section className="panel incidents-panel" id="incidents">
              <div className="panel-heading compact"><div><p className="eyebrow">Live segmentation</p><h2>Open incident segments</h2></div><span className="muted-count">{openIncidents.length} active</span></div>
              <div className="incident-table" role="table">
                <div className="table-row table-head" role="row"><span>Segment</span><span>Failure rate</span><span>Status</span><span>Attempts</span><span>Revenue at risk</span><span /></div>
                {openIncidents.map((item) => (
                  <button key={item.incident_id} className={`table-row ${item.incident_id === incident.incident_id ? 'selected' : ''}`} onClick={() => setSelectedId(item.incident_id)} role="row">
                    <span className="segment-cell"><span className="method-icon">{item.method === 'upi' ? 'UPI' : '••••'}</span><span><strong>{item.method.toUpperCase()} · {item.bank ?? 'Unknown'}</strong><small>{item.error_reason.replaceAll('_', ' ')}</small></span></span>
                    <span className="rate-cell"><strong>{percent(item.current_failure_rate)}</strong><i><b style={{ width: `${item.current_failure_rate * 100}%` }} /></i></span>
                    <span><em className="status-critical">Critical</em></span><span>{item.current_failure_count} / {item.current_attempt_count}</span><span className="money-cell">{money(item.revenue_at_risk_subunits)}</span><span><ChevronRight size={17} /></span>
                  </button>
                ))}
              </div>
            </section>
          </div>

          <aside className="commander panel">
            <div className="commander-header"><div className="ai-mark"><Sparkles size={18} /></div><div><p className="eyebrow">Revenue SRE · Verified incident context</p><h2>Incident Commander</h2></div><span className="advisory-chip">Advisory</span></div>
            <p className="control-note"><ShieldCheck size={14} />Evidence chat explains this incident. TrueForge handles generative investigation separately.</p>
            <IncidentChat key={incident.incident_id} incidentId={incident.incident_id} incidentLabel={`${incident.bank ?? 'Provider'} ${incident.method.toUpperCase()}`} />
            <Commander icon={<CheckCircle2 />} title="Confirmed facts" tone="green">
              <ul className="fact-list"><li>{incident.current_failure_count} {incident.method.toUpperCase()} payments failed on {incident.bank}.</li><li>Failure rate increased from {percent(incident.baseline_failure_rate)} to {percent(incident.current_failure_rate)}.</li><li>{money(incident.revenue_at_risk_subunits)} is currently exposed.</li><li>Provider boundary reports <code>{incident.error_reason}</code>.</li></ul>
              <button className="text-button" onClick={() => document.getElementById('evidence')?.scrollIntoView({ behavior: 'smooth' })}>View {facts.length} evidence records <ArrowRight size={14} /></button>
            </Commander>
            <Commander icon={<Bot />} title="Hypotheses" tone="purple"><ul className="hypothesis-list"><li>Bank-side UPI endpoint degradation.</li><li>Possible wider UPI timeout pattern across banks.</li><li>Merchant regression is less likely, but not disproven.</li></ul><p className="hypothesis-note">AI hypotheses require provider confirmation.</p></Commander>
            <Commander icon={<FileCheck2 />} title="Evidence readiness" tone="blue"><div className="execution-trail"><Trail label="Current evidence retrieved" done={facts.length > 0} /><Trail label="Snapshot consistency checked" done={verified} /><Trail label="Audit trail available" done={Boolean(auditQuery.data?.length)} /></div></Commander>
            <section className="proposal-card" id="recovery">
              <div className="proposal-title"><div><p className="eyebrow">Proposed next action</p><h3>Recovery plan</h3></div>{proposal ? <span className={`proposal-status ${proposal.status}`}>{proposal.status.replaceAll('_', ' ')}</span> : <span className="proposal-status empty">Not created</span>}</div>
              {proposal ? <><div className="proposal-summary"><RotateCcw /><div><strong>{proposal.actions.length} bounded recovery actions</strong><span>{money(proposal.total_amount_subunits)} maximum scope · expires {time(proposal.expires_at)}</span></div></div>{proposal.status === 'pending_approval' ? <div className="proposal-actions"><button className="primary-button" disabled={decision.isPending} onClick={() => decision.mutate({ proposalId: proposal.proposal_id, action: 'approve' })}><Check size={16} />Approve</button><button className="secondary-button danger" disabled={decision.isPending} onClick={() => decision.mutate({ proposalId: proposal.proposal_id, action: 'reject' })}><X size={16} />Reject</button></div> : <p className="decision-recorded"><CheckCircle2 size={15} />Immutable decision recorded.</p>}</> : <div className="empty-proposal"><Bot /><div><strong>No proposal has been requested.</strong><span>Ask the Incident Commander in TrueForge to prepare a bounded plan. It will appear here automatically.</span></div></div>}
            </section>
            <div className="safe-state"><ShieldCheck size={17} /><span><strong>No money action executed</strong><small>Execution adapter remains disabled</small></span></div>
          </aside>
        </section>

        <section className="panel evidence-panel" id="evidence">
          <div className="panel-heading compact"><div><p className="eyebrow">Traceable diagnosis</p><h2>Current evidence and audit</h2></div><span className="window-chip"><History size={14} />{auditQuery.data?.length ?? 0} audit events</span></div>
          <div className="evidence-grid">{facts.slice(0, 6).map((evidence) => <article className="evidence-item" key={evidence.evidence_id}><div><span className="fact-badge">Razorpay fact</span><time>{time(String(evidence.details.provider_event_at))}</time></div><strong>{String(evidence.details.payment_id)}</strong><p>{evidence.summary}</p><span>{money(Number(evidence.details.amount_subunits))}</span></article>)}</div>
        </section>
      </main>
    </div>
  )
}

function Sidebar({ open, close }: { open: boolean; close: () => void }) {
  return <>{open && <button className="nav-backdrop" onClick={close} aria-label="Close navigation" />}<aside className={`sidebar ${open ? 'open' : ''}`}><div className="brand"><div className="brand-mark"><Command /></div><span>Revenue <strong>SRE</strong></span></div><nav><a className="active" href="#top"><LayoutDashboard />Overview</a><a href="#incidents"><AlertTriangle />Incidents<span className="nav-dot" /></a><a href="#recovery"><RotateCcw />Recovery</a><a href="#evidence"><History />Audit trail</a></nav><div className="sidebar-spacer" /><div className="system-card"><span className="pulse" /><div><strong>All systems</strong><small>Monitoring operational</small></div></div><div className="sidebar-time"><Clock3 /><div><strong>{new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit' }).format(new Date())} IST</strong><small>Local operator time</small></div></div></aside></>
}
function Metric({ icon, label, value, detail, tone }: { icon: React.ReactNode; label: string; value: string; detail: string; tone: string }) { return <motion.article className="metric-card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}><div className={`metric-icon ${tone}`}>{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></motion.article> }
function Commander({ icon, title, tone, children }: { icon: React.ReactNode; title: string; tone: string; children: React.ReactNode }) { return <section className="commander-section"><div className={`section-title ${tone}`}>{icon}<h3>{title}</h3></div>{children}</section> }
type ChatMessage = { id: number; role: 'assistant' | 'user'; content: string }
function IncidentChat({ incidentId, incidentLabel }: { incidentId: string; incidentLabel: string }) {
  const [input, setInput] = useState('')
  const welcomeMessage = `I’m connected to the verified ${incidentLabel} incident. Ask me about its evidence, revenue impact, likely cause, or safest next step.`
  const [messages, setMessages] = useState<ChatMessage[]>([{
    id: 0,
    role: 'assistant',
    content: welcomeMessage,
  }])
  const prompts = ['Why did this open?', 'Revenue impact?', 'Safest next step?']
  const chat = useMutation({
    mutationFn: (message: string) => askIncidentCommander(incidentId, message),
    onSuccess: (reply) => setMessages((current) => [...current, { id: Date.now(), role: 'assistant', content: reply.answer }]),
    onError: (error) => setMessages((current) => [...current, { id: Date.now(), role: 'assistant', content: `I couldn’t read the incident context: ${error.message}` }]),
  })
  const sendQuestion = (question: string) => {
    const message = question.trim()
    if (!message || chat.isPending) return
    setMessages((current) => [...current, { id: Date.now(), role: 'user', content: message }])
    setInput('')
    chat.mutate(message)
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    sendQuestion(input)
  }
  const clearChat = () => {
    chat.reset()
    setInput('')
    setMessages([{ id: Date.now(), role: 'assistant', content: welcomeMessage }])
  }
  return <section className="commander-chat" aria-label="Chat with Incident Commander">
    <div className="chat-title"><span><MessageSquare size={14} />Ask Incident Commander</span><div className="chat-title-actions"><small><i />Evidence connected</small><button type="button" className="clear-chat" onClick={clearChat} disabled={chat.isPending || messages.length === 1} aria-label="Clear chat" title="Clear chat"><Trash2 size={13} /></button></div></div>
    <div className="chat-transcript" aria-live="polite">
      {messages.map((message) => <div className={`chat-message ${message.role}`} key={message.id}><span>{message.role === 'assistant' ? <Sparkles size={12} /> : 'You'}</span><p>{message.content}</p></div>)}
      {chat.isPending && <div className="chat-message assistant pending"><span><LoaderCircle className="spin" size={12} /></span><p>Checking the persisted evidence…</p></div>}
    </div>
    <div className="prompt-chips">{prompts.map((prompt) => <button type="button" key={prompt} disabled={chat.isPending} onClick={() => sendQuestion(prompt)}>{prompt}</button>)}</div>
    <form className="chat-form" onSubmit={submit}>
      <label className="sr-only" htmlFor="commander-question">Ask about this incident</label>
      <input id="commander-question" value={input} onChange={(event) => setInput(event.target.value)} maxLength={1000} placeholder="Ask about this incident…" autoComplete="off" />
      <button type="submit" aria-label="Send question" disabled={!input.trim() || chat.isPending}>{chat.isPending ? <LoaderCircle className="spin" /> : <Send />}</button>
    </form>
    <p className="chat-safety"><ShieldCheck size={11} />Chat is advisory and cannot execute money actions.</p>
  </section>
}
function Trail({ label, done }: { label: string; done: boolean }) { return <div className="trail-item"><span className={done ? 'done' : ''}>{done ? <Check size={12} /> : <Clock3 size={12} />}</span><strong>{label}</strong><small>{done ? 'Complete' : 'Waiting'}</small></div> }
function RateTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number; payload: { window: string } }> }) { if (!active || !payload?.length) return null; return <div className="chart-tooltip"><span>{payload[0].payload.window}</span><strong>{payload[0].value.toFixed(1)}%</strong></div> }
function EmptyState() { return <main className="fatal-state"><div className="fatal-icon safe"><ShieldCheck /></div><p className="eyebrow">Revenue Command Center</p><h1>No open payment incidents.</h1><p>The detector is monitoring payment traffic. Verified anomalies will appear automatically.</p></main> }
function ErrorState({ message, retry }: { message: string; retry: () => void }) { return <main className="fatal-state"><div className="fatal-icon"><AlertTriangle /></div><p className="eyebrow">Revenue SRE unavailable</p><h1>We couldn’t load the command center.</h1><p>{message}</p><button className="primary-button" onClick={retry}><RefreshCcw size={17} />Retry connection</button></main> }

export default App
