import { Link, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import { Workflow, Zap, GitBranch, Lock, Gauge, ArrowRight } from 'lucide-react'
import { getUser } from '../lib/auth'

export default function Welcome() {
  const navigate = useNavigate()
  const user = getUser()

  useEffect(() => {
    // Redirect logged-in users to workflows
    if (user) {
      navigate('/workflows', { replace: true })
    }
  }, [user, navigate])

  return (
    <div className="min-h-screen bg-dark-800 flex flex-col">
      {/* Nav */}
      <nav className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-blue-600 shadow-lg shadow-blue-500/30">
            <Workflow className="h-5 w-5 text-white" />
          </div>
          <span className="text-sm font-extrabold text-white">Flow-<span className="text-blue-400">AI</span></span>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/login"    className="btn btn-sm btn-secondary">Login</Link>
          <Link to="/register" className="btn btn-sm btn-primary">Get Started</Link>
        </div>
      </nav>

      {/* Hero */}
      <main className="flex-1 flex flex-col items-center justify-center px-4 py-20 text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/30 bg-blue-500/10 px-4 py-1.5 text-xs font-semibold text-blue-400 mb-8">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-400" />
          </span>
          Workflow Automation Platform
        </div>

        <h1 className="text-5xl md:text-7xl font-black tracking-tight text-white leading-tight max-w-4xl">
          Build. Connect.{' '}
          <span className="bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">Automate.</span>
        </h1>
        <p className="mt-6 text-lg text-slate-400 max-w-2xl leading-relaxed">
          Create powerful automation workflows by connecting plugins together. 
          Integrate APIs, manage data, and orchestrate complex processes—<em className="text-slate-200 not-italic">visually</em>.
        </p>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
          <Link to="/register" className="btn btn-primary btn-lg gap-2">
            Start Building <ArrowRight className="h-5 w-5" />
          </Link>
          <Link to="/login" className="btn btn-secondary btn-lg">
            Sign In
          </Link>
        </div>

        {/* Stats */}
        <div className="mt-16 grid grid-cols-2 gap-4 sm:grid-cols-4 max-w-2xl w-full">
          {[
            { label: 'Plugins', value: '8+' },
            { label: 'Node Types', value: '∞' },
            { label: 'Execution', value: 'Live' },
            { label: 'API Support', value: 'Full' },
          ].map(s => (
            <div key={s.label} className="card text-center">
              <div className="text-3xl font-black text-blue-400 font-mono">{s.value}</div>
              <div className="text-xs text-slate-500 mt-1 font-semibold uppercase tracking-wider">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Features */}
        <div className="mt-16 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 max-w-5xl w-full">
          {[
            { icon: Workflow, color: 'blue', title: 'Visual Builder', desc: 'Drag-and-drop workflow designer with React Flow' },
            { icon: Zap,      color: 'cyan', title: 'Live Execution', desc: 'Real HTTP calls and API integration with plugins' },
            { icon: GitBranch, color: 'emerald', title: 'Complex Logic', desc: 'Conditional branching, transforms, and loops' },
            { icon: Gauge,    color: 'purple', title: 'Full Control', desc: 'Variables, retries, timeouts, and error handling' },
          ].map(({ icon: Icon, color, title, desc }) => (
            <div key={title} className="card card-hover text-left">
              <div className={`flex h-10 w-10 items-center justify-center rounded-xl mb-4
                ${color === 'blue'    ? 'bg-blue-500/15'    : ''}
                ${color === 'cyan'    ? 'bg-cyan-500/15'    : ''}
                ${color === 'emerald' ? 'bg-emerald-500/15' : ''}
                ${color === 'purple'  ? 'bg-purple-500/15'  : ''}`}>
                <Icon className={`h-5 w-5
                  ${color === 'blue'    ? 'text-blue-400'    : ''}
                  ${color === 'cyan'    ? 'text-cyan-400'    : ''}
                  ${color === 'emerald' ? 'text-emerald-400' : ''}
                  ${color === 'purple'  ? 'text-purple-400'  : ''}`} />
              </div>
              <div className="text-sm font-bold text-white">{title}</div>
              <div className="mt-1 text-xs text-slate-400 leading-relaxed">{desc}</div>
            </div>
          ))}
        </div>

        {/* Plugins Section */}
        <div className="mt-20 max-w-5xl w-full">
          <h2 className="text-2xl font-bold text-white mb-8">Available Plugins</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-4">
            {[
              { name: 'REST API', icon: '🌐' },
              { name: 'Email', icon: '📧' },
              { name: 'Slack', icon: '💬' },
              { name: 'GitHub', icon: '🐙' },
              { name: 'Google Sheets', icon: '📊' },
              { name: 'Weather', icon: '🌤️' },
              { name: 'Currency', icon: '💱' },
              { name: 'OpenAI', icon: '🤖' },
            ].map(({ name, icon }) => (
              <div key={name} className="card text-center">
                <div className="text-3xl mb-2">{icon}</div>
                <div className="text-xs font-semibold text-slate-300">{name}</div>
              </div>
            ))}
          </div>
        </div>
      </main>

      <footer className="text-center py-6 text-xs text-slate-600 border-t border-white/[0.04]">
        Flow-AI · Workflow Automation Platform · Built with FastAPI + React
      </footer>
    </div>
  )
}
