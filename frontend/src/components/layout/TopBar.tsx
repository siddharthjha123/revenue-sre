import { CalendarDays, Menu, RefreshCcw } from 'lucide-react'

interface TopBarProps {
  isRefreshing: boolean
  view: 'dashboard' | 'audit'
  onMenu: () => void
  onRefresh: () => void
}

export function TopBar({ isRefreshing, view, onMenu, onRefresh }: TopBarProps) {
  return (
    <header className="topbar-glass">
      <button className="mobile-menu" onClick={onMenu} aria-label="Open navigation"><Menu /></button>
      <div className="page-intro">
        <span className="section-kicker">{view === 'dashboard' ? 'Live operations' : 'Control record'}</span>
        <h1>{view === 'dashboard' ? 'Good morning, UrbanClothes' : 'Audit & outcomes'}</h1>
        <p>{view === 'dashboard'
          ? 'Your payment health and recovery decisions for today.'
          : 'Every incident, decision and measured recovery in one traceable timeline.'}</p>
      </div>
      <div className="topbar-actions">
        <div className="today-chip"><CalendarDays /><span>Today · {new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short' }).format(new Date())}</span></div>
        <button className="icon-action" onClick={onRefresh} aria-label="Refresh data">
          <RefreshCcw className={isRefreshing ? 'spin' : ''} />
        </button>
      </div>
    </header>
  )
}
