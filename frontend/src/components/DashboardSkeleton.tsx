import { Activity } from 'lucide-react'

import { Skeleton } from './ui/Skeleton'
import './skeleton.css'

export function DashboardSkeleton() {
  return (
    <div className="loading-shell" aria-label="Loading Revenue SRE" aria-busy="true">
      <aside className="loading-sidebar">
        <div className="loading-brand"><span><Activity /></span><Skeleton className="sk-brand" /></div>
        <Skeleton className="sk-nav" /><Skeleton className="sk-nav" />
        <div className="loading-spacer" /><Skeleton className="sk-system" />
      </aside>
      <main className="loading-main">
        <Skeleton className="sk-header" />
        <div className="loading-metrics">{Array.from({ length: 5 }, (_, index) => <Skeleton className="sk-metric" key={index} />)}</div>
        <div className="loading-command-grid">
          <Skeleton className="sk-panel" />
          <Skeleton className="sk-panel sk-operator" />
          <Skeleton className="sk-panel" />
        </div>
      </main>
    </div>
  )
}
