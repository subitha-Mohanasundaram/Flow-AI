import { Link, useNavigate, useLocation } from 'react-router-dom'
import { getUser, logout } from '../lib/auth'
import { Code2, BrainCircuit, LayoutDashboard, ListChecks, Trophy, LogOut, User, GitBranch, Settings2 } from 'lucide-react'

export default function Topbar() {
  const user = getUser()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  function handleLogout() {
    logout()
    navigate('/login')
  }

  const navItems = [
    { to: '/assignments',    label: 'Problems',  icon: ListChecks  },
    { to: '/workflows',      label: 'Workflows', icon: GitBranch   },
    { to: '/me',             label: 'Dashboard', icon: LayoutDashboard },
    { to: '/plugin-settings',label: 'Plugins',   icon: Settings2   },
  ]

  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-dark-800/90 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">

        {/* Logo */}
        <Link to="/" className="flex items-center gap-2.5 group">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-600 shadow-lg shadow-brand-500/30 transition group-hover:scale-105">
            <BrainCircuit className="h-5 w-5 text-white" />
          </div>
          <div className="leading-tight hidden sm:block">
            <div className="text-sm font-extrabold tracking-tight text-white">
              Flow <span className="text-brand-400">AI</span>
            </div>
            <div className="text-[10px] text-slate-500 tracking-widest font-medium uppercase">Automation</div>
          </div>
        </Link>

        {/* Nav */}
        {user && (
          <nav className="hidden items-center gap-1 md:flex">
            {navItems.map(({ to, label, icon: Icon }) => {
              const active = pathname === to || pathname.startsWith(to + '/')
              return (
                <Link
                  key={to}
                  to={to}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition-all
                    ${active
                      ? 'bg-brand-500/15 text-brand-400'
                      : 'text-slate-400 hover:text-white hover:bg-white/5'}`}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </Link>
              )
            })}
          </nav>
        )}

        {/* Right */}
        <div className="flex items-center gap-2">
          {user ? (
            <>
              <div className="hidden sm:flex items-center gap-2 rounded-lg border border-white/10 bg-dark-600 px-3 py-1.5">
                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-brand-500 text-[11px] font-bold text-white">
                  {user.username[0].toUpperCase()}
                </div>
                <span className="text-sm font-semibold text-slate-200">{user.username}</span>
                <span className={`badge text-[10px] ${user.role === 'admin' ? 'badge-orange' : 'badge-teal'}`}>
                  {user.role}
                </span>
              </div>
              <button onClick={handleLogout} className="btn btn-sm btn-secondary gap-1.5">
                <LogOut className="h-3.5 w-3.5" /> Sign out
              </button>
            </>
          ) : (
            <>
              <Link to="/login"    className="btn btn-sm btn-secondary">Login</Link>
              <Link to="/register" className="btn btn-sm btn-primary">Register</Link>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
