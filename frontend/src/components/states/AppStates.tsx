import { AlertTriangle, RefreshCcw, ShieldCheck } from 'lucide-react'

export function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <main className="full-page-state">
      <div className="state-icon error"><AlertTriangle /></div>
      <span>Connection interrupted</span>
      <h1>The command center could not load.</h1>
      <p>{message}</p>
      <button onClick={retry}><RefreshCcw />Retry connection</button>
    </main>
  )
}

export function NoIncidentsState() {
  return (
    <div className="no-incidents-state">
      <ShieldCheck />
      <div><strong>Payment health is clear</strong><span>No active incidents need your attention.</span></div>
    </div>
  )
}
