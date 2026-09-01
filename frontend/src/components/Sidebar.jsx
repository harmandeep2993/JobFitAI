import { motion } from 'framer-motion'

const NAV_ITEMS = [
  {
    id: 'analyzer',
    label: 'Resume Analyser',
    icon: (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="9" cy="9" r="6.5"/><path d="M14 14l3.5 3.5"/><path d="M9 6.5v2.5l1.5 1.5"/>
      </svg>
    ),
  },
  {
    id: 'matches',
    label: 'Job Matches',
    icon: (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="4" width="16" height="3" rx="1"/><rect x="2" y="9" width="12" height="3" rx="1"/><rect x="2" y="14" width="8" height="3" rx="1"/>
      </svg>
    ),
  },
  {
    id: 'resumes',
    label: 'Resumes',
    icon: (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="2" width="12" height="16" rx="2"/>
        <path d="M8 7h4M8 10h4M8 13h2"/>
      </svg>
    ),
  },
  {
    id: 'history',
    label: 'History',
    icon: (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3.5 9.5A6.5 6.5 0 1110 3.5H3.5"/><path d="M3.5 5.5v4h4"/><path d="M10 7v3l2.5 2"/>
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: (
      <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="10" cy="10" r="2.5"/>
        <path d="M10 1v2M10 17v2M1 10h2M17 10h2M3.5 3.5l1.4 1.4M15.1 15.1l1.4 1.4M3.5 16.5l1.4-1.4M15.1 4.9l1.4-1.4"/>
      </svg>
    ),
  },
]

export default function Sidebar({ active, onChange }) {
  return (
    <aside
      className="fixed left-0 bottom-0 flex flex-col z-40"
      style={{
        top: 'var(--topbar-h)',
        width: 'var(--sidebar-w)',
        background: 'rgb(var(--surface))',
        borderRight: '1px solid rgba(var(--border) / 0.08)',
      }}
    >
      <nav className="flex-1 p-2.5 overflow-y-auto space-y-0.5">
        {NAV_ITEMS.map(item => {
          const isActive = active === item.id
          return (
            <button
              key={item.id}
              onClick={() => onChange(item.id)}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-sm text-[13.5px] font-medium transition-colors text-left relative group"
              style={{
                color: isActive ? 'rgb(var(--accent))' : 'rgb(var(--t2))',
                background: isActive ? 'rgba(var(--accent) / 0.08)' : 'transparent',
              }}
              onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'rgb(var(--surface-2))' }}
              onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
            >
              <span style={{ color: isActive ? 'rgb(var(--accent))' : 'rgb(var(--t3))' }}>
                {item.icon}
              </span>
              <span>{item.label}</span>
              {isActive && (
                <motion.span
                  layoutId="sidebar-dot"
                  className="ml-auto w-1.5 h-1.5 rounded-full flex-shrink-0"
                  style={{ background: 'rgb(var(--accent))' }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
            </button>
          )
        })}
      </nav>

      <div className="p-4 border-t" style={{ borderColor: 'rgba(var(--border) / 0.08)' }}>
        <div className="text-[11px] text-t3">JobsFitAI</div>
      </div>
    </aside>
  )
}
