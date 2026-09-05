import { Activity, History, PlugZap, Settings2, X } from 'lucide-react'

export type AppView = 'dashboard' | 'audit'

interface SidebarProps {
  currentView: AppView
  open: boolean
  onClose: () => void
  onNavigate: (view: AppView) => void
}

export function Sidebar({ currentView, open, onClose, onNavigate }: SidebarProps) {
  const navigate = (view: AppView) => {
    onNavigate(view)
    onClose()
  }

  return (
    <>
      {open && <button className="nav-backdrop" onClick={onClose} aria-label="Close navigation" />}
      <aside className={`app-sidebar ${open ? 'is-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-symbol"><Activity /></div>
          <div><strong>Revenue SRE</strong><span>Merchant operations</span></div>
          <button className="sidebar-close" onClick={onClose} aria-label="Close navigation"><X /></button>
        </div>

        <nav aria-label="Primary navigation">
          <button
            className={currentView === 'dashboard' ? 'is-active' : ''}
            onClick={() => navigate('dashboard')}
          >
            <Activity /><span>Command center</span>
          </button>
          <button
            className={currentView === 'audit' ? 'is-active' : ''}
            onClick={() => navigate('audit')}
          >
            <History /><span>Audit & outcomes</span>
          </button>
        </nav>

        <div className="sidebar-spacer" />
        <div className="system-state">
          <span className="live-orb" />
          <div><strong>Monitoring live</strong><span>Worker and detector online</span></div>
        </div>
        <div className="environment-row"><PlugZap /><span>Razorpay test mode</span></div>
        <button className="settings-link" type="button"><Settings2 /><span>Workspace settings</span></button>
      </aside>
    </>
  )
}
