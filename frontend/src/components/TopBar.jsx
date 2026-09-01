import { useState, useEffect } from 'react'
import { apiFetch, logout } from '../lib/auth.js'

export default function TopBar() {
  const [user, setUser] = useState(null)

  // The email is not in the token (it must not sit in localStorage) - fetch it.
  useEffect(() => {
    apiFetch('/api/auth/me')
      .then(r => (r?.ok ? r.json() : null))
      .then(d => setUser(d))
      .catch(() => {})
  }, [])

  return (
    <header
      className="fixed top-0 left-0 right-0 z-50 flex items-center px-5"
      style={{
        height: 'var(--topbar-h)',
        background: 'rgba(var(--surface) / 0.85)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid rgba(var(--border) / 0.08)',
      }}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 flex-shrink-0" style={{ width: 'var(--sidebar-w)', paddingRight: '16px' }}>
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgb(var(--accent))' }}
        >
          <svg width="13" height="13" viewBox="0 0 16 16" fill="white">
            <path d="M8 1L1 4.5v4c0 3.5 2.8 6.2 7 7.5 4.2-1.3 7-4 7-7.5v-4L8 1z"/>
          </svg>
        </div>
        <span className="text-[15px] font-bold tracking-tight text-t1" style={{ fontFamily: '"Plus Jakarta Sans", Inter, sans-serif' }}>
          Jobs<span style={{ color: 'rgb(var(--accent))' }}>Fit</span>AI
        </span>
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-1.5">
        {user?.email && (
          <span className="text-[12px] text-t3 mr-2 hidden sm:block max-w-[200px] truncate">{user.email}</span>
        )}
        <button
          onClick={logout}
          className="h-8 px-3 text-[12.5px] font-medium text-t2 hover:text-t1 rounded-sm transition-colors"
          style={{ '--tw-bg-opacity': '0' }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgb(var(--surface-2))'}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
        >
          Sign out
        </button>
      </div>
    </header>
  )
}
