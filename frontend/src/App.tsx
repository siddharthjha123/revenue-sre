import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import './App.css'
import { AuditView } from './components/audit/AuditView'
import { RevenueOperator } from './components/agent/RevenueOperator'
import { KpiStrip } from './components/dashboard/KpiStrip'
import { IncidentQueue } from './components/incidents/IncidentQueue'
import { DashboardSkeleton } from './components/DashboardSkeleton'
import { type AppView, Sidebar } from './components/layout/Sidebar'
import { TopBar } from './components/layout/TopBar'
import { DecisionPanel } from './components/recovery/DecisionPanel'
import { ErrorState, NoIncidentsState } from './components/states/AppStates'
import {
  decideProposal,
  getActiveProposal,
  getDashboardSummary,
  getIncident,
  getIncidentAudit,
  getIncidents,
} from './lib/api'
import { incidentIsOpen } from './lib/incidents'
import {
  recoveryWorkflowIsActive,
  type RecoveryWorkflowStage,
  type RecoveryWorkflowState,
} from './lib/recoveryWorkflow'

function App() {
  const queryClient = useQueryClient()
  const [view, setView] = useState<AppView>('dashboard')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mobileNav, setMobileNav] = useState(false)
  const [recoveryWorkflow, setRecoveryWorkflow] = useState<RecoveryWorkflowState>({
    incidentId: null,
    stage: 'idle',
  })

  const incidentsQuery = useQuery({
    queryKey: ['incidents'],
    queryFn: getIncidents,
    refetchInterval: 10_000,
  })
  const dashboardQuery = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: getDashboardSummary,
    refetchInterval: 10_000,
  })

  const incidents = useMemo(() => incidentsQuery.data ?? [], [incidentsQuery.data])
  const openIncidents = useMemo(
    () => incidents
      .filter(incidentIsOpen)
      .sort((left, right) => right.revenue_at_risk_subunits - left.revenue_at_risk_subunits),
    [incidents],
  )
  const selectedFallback = view === 'dashboard' ? openIncidents[0] : incidents[0]
  const activeIncidentId = selectedId ?? selectedFallback?.incident_id ?? null
  const selectedSummary =
    incidents.find((incident) => incident.incident_id === activeIncidentId) ?? selectedFallback

  const detailQuery = useQuery({
    queryKey: ['incident', activeIncidentId],
    queryFn: () => getIncident(activeIncidentId!),
    enabled: Boolean(activeIncidentId),
    refetchInterval: view === 'dashboard' ? 10_000 : false,
  })
  const auditQuery = useQuery({
    queryKey: ['incident-audit', activeIncidentId],
    queryFn: () => getIncidentAudit(activeIncidentId!),
    enabled: Boolean(activeIncidentId),
  })
  const proposalQuery = useQuery({
    queryKey: ['incident-proposal', activeIncidentId],
    queryFn: () => getActiveProposal(activeIncidentId!),
    enabled: Boolean(activeIncidentId),
    refetchInterval: recoveryWorkflowIsActive(recoveryWorkflow.stage) ? 1_000 : 5_000,
  })

  useEffect(() => {
    if (
      activeIncidentId &&
      recoveryWorkflow.incidentId === activeIncidentId &&
      recoveryWorkflow.stage === 'persisting' &&
      proposalQuery.data
    ) {
      const timer = window.setTimeout(() => {
        setRecoveryWorkflow({ incidentId: activeIncidentId, stage: 'ready' })
      }, 650)
      return () => window.clearTimeout(timer)
    }
  }, [activeIncidentId, proposalQuery.data, recoveryWorkflow])

  const updateRecoveryWorkflow = (stage: RecoveryWorkflowStage, error?: string) => {
    setRecoveryWorkflow({ incidentId: activeIncidentId, stage, error })
    if (stage === 'persisting' && activeIncidentId) {
      void queryClient.invalidateQueries({ queryKey: ['incident-proposal', activeIncidentId] })
      void queryClient.invalidateQueries({ queryKey: ['incident-audit', activeIncidentId] })
    }
  }

  const decision = useMutation({
    mutationFn: ({
      proposalId,
      action,
      reason,
    }: {
      proposalId: string
      action: 'approve' | 'reject'
      reason?: string
    }) => decideProposal(proposalId, action, reason),
    onSuccess: (result) => {
      toast.success(`Proposal ${result.decision}`, {
        description: 'The immutable merchant decision is now in the audit timeline.',
      })
      void queryClient.invalidateQueries({ queryKey: ['incident-proposal'] })
      void queryClient.invalidateQueries({ queryKey: ['incident-audit'] })
    },
    onError: (error) => toast.error('Decision could not be recorded', { description: error.message }),
  })

  const refresh = () => {
    void incidentsQuery.refetch()
    void dashboardQuery.refetch()
    if (activeIncidentId) {
      void detailQuery.refetch()
      void proposalQuery.refetch()
      void auditQuery.refetch()
    }
  }

  if (incidentsQuery.isPending || dashboardQuery.isPending) return <DashboardSkeleton />
  if (incidentsQuery.isError || dashboardQuery.isError) {
    const message = incidentsQuery.error?.message ?? dashboardQuery.error?.message ?? 'Dashboard unavailable'
    return <ErrorState message={message} retry={refresh} />
  }

  const activeIncident = detailQuery.data ?? selectedSummary ?? null
  const displayedOpenIncidents = openIncidents.map((incident) =>
    incident.incident_id === activeIncident?.incident_id ? activeIncident : incident,
  )

  return (
    <div className="app-shell">
      <Sidebar
        currentView={view}
        open={mobileNav}
        onClose={() => setMobileNav(false)}
        onNavigate={(nextView) => {
          setView(nextView)
          setSelectedId(null)
        }}
      />
      <main className="app-main">
        <TopBar
          view={view}
          isRefreshing={incidentsQuery.isFetching || dashboardQuery.isFetching || detailQuery.isFetching}
          onMenu={() => setMobileNav(true)}
          onRefresh={refresh}
        />

        {view === 'dashboard' ? (
          <>
            <KpiStrip summary={dashboardQuery.data} />
            {openIncidents.length === 0 ? (
              <NoIncidentsState />
            ) : (
              <section className="command-grid">
                <IncidentQueue
                  incidents={displayedOpenIncidents}
                  selectedId={activeIncident?.incident_id ?? null}
                  onSelect={setSelectedId}
                />
                <RevenueOperator
                  key={activeIncident?.incident_id ?? 'no-incident'}
                  incidents={openIncidents}
                  activeIncident={activeIncident}
                  onAttachIncident={setSelectedId}
                  onRecoveryStage={updateRecoveryWorkflow}
                />
                <DecisionPanel
                  incident={activeIncident}
                  proposal={proposalQuery.data}
                  loading={proposalQuery.isPending && Boolean(activeIncident)}
                  deciding={decision.isPending}
                  workflow={recoveryWorkflow.incidentId === activeIncidentId
                    ? recoveryWorkflow
                    : { incidentId: activeIncidentId, stage: 'idle' }}
                  onDecide={(proposalId, action, reason) => decision.mutate({ proposalId, action, reason })}
                />
              </section>
            )}
          </>
        ) : (
          <AuditView
            incidents={incidents}
            selectedIncident={activeIncident}
            auditEvents={auditQuery.data ?? []}
            onSelect={setSelectedId}
          />
        )}
      </main>
    </div>
  )
}

export default App
