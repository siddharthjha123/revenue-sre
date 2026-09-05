import { Bot } from 'lucide-react'

export function RobotAvatar({ active = false }: { active?: boolean }) {
  return (
    <div className={`robot-avatar ${active ? 'is-active' : ''}`} aria-hidden="true">
      <span className="robot-ring" />
      <span className="robot-core"><Bot /></span>
    </div>
  )
}
