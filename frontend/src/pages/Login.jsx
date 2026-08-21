import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Workflow, LogIn, UserPlus, AlertCircle, Eye, EyeOff, Loader } from 'lucide-react'
import { api } from '../lib/api'
import { saveAuth } from '../lib/auth'

export default function Login({ register = false }) {
  const navigate = useNavigate()
  const [form, setForm]     = useState({ username: '', email: '', password: '', phone: '' })
  const [error, setError]   = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const isRegister = register

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    
    try {
      const data = isRegister
        ? await api.register({ 
            username: form.username, 
            email: form.email, 
            password: form.password,
            phone: form.phone || undefined
          })
        : await api.login({ 
            username: form.username, 
            password: form.password 
          })
      
      if (data.token) {
        saveAuth(data)
        navigate('/workflows', { replace: true })
      } else {
        setError('Authentication failed - no token received')
      }
    } catch (err) {
      const errorMsg = err.message || 'Authentication failed. Please try again.'
      setError(errorMsg)
      console.error('Auth error:', err)
    } finally {
      setLoading(false)
    }
  }

  function handleChange(e) {
    const { name, value } = e.target
    setForm(f => ({ ...f, [name]: value }))
  }

  const isFormValid = isRegister
    ? form.username && form.email && form.password && form.password.length >= 8
    : form.username && form.password

  return (
    <div className="min-h-screen bg-dark-800 flex">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 bg-gradient-to-br from-dark-700 to-dark-800 border-r border-white/[0.06]">
        <Link to="/" className="flex items-center gap-2.5 hover:opacity-80 transition">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-blue-600 shadow-lg shadow-blue-500/30">
            <Workflow className="h-5 w-5 text-white" />
          </div>
          <span className="text-sm font-extrabold text-white">Flow-<span className="text-blue-400">AI</span></span>
        </Link>

        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-[11px] font-semibold text-blue-400 mb-6">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-400" />
            </span>
            Live · Workflow Automation
          </div>
          <h2 className="text-4xl font-black text-white leading-tight tracking-tight">
            Build powerful<br />
            <span className="bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">automations.</span>
          </h2>
          <p className="mt-4 text-slate-400 text-sm leading-relaxed max-w-sm">
            Connect APIs, manage data, and orchestrate processes visually.
            No coding required—just drag, drop, and automate.
          </p>
          <div className="mt-8 space-y-3">
            {[
              'Visual Workflow Designer — Drag-and-drop canvas',
              'REST API, Email, Slack, GitHub & more plugins',
              'Live Execution — Real HTTP calls and data',
            ].map(item => (
              <div key={item} className="flex items-center gap-2 text-sm text-slate-400">
                <span className="h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
                {item}
              </div>
            ))}
          </div>
        </div>

        <div className="text-xs text-slate-600">Flow-AI · Workflow Automation Platform</div>
      </div>

      {/* Right: form */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <h1 className="text-2xl font-extrabold text-white tracking-tight">
              {isRegister ? 'Create your account' : 'Welcome back'}
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {isRegister ? 'Start building workflows today.' : 'Sign in to build automations.'}
            </p>
          </div>

          {error && (
            <div className="mb-5 flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3">
              <AlertCircle className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
              <p className="text-sm text-red-300">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="block text-sm font-semibold text-slate-200 mb-2">
                Username
              </label>
              <input
                type="text"
                name="username"
                placeholder="alice"
                value={form.username}
                onChange={handleChange}
                disabled={loading}
                className="w-full px-4 py-2.5 rounded-lg bg-slate-900/50 border border-slate-700 text-white placeholder-slate-500 transition-all focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                required
              />
            </div>

            {/* Email (Register only) */}
            {isRegister && (
              <div>
                <label className="block text-sm font-semibold text-slate-200 mb-2">
                  Email
                </label>
                <input
                  type="email"
                  name="email"
                  placeholder="alice@example.com"
                  value={form.email}
                  onChange={handleChange}
                  disabled={loading}
                  className="w-full px-4 py-2.5 rounded-lg bg-slate-900/50 border border-slate-700 text-white placeholder-slate-500 transition-all focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                  required
                />
              </div>
            )}

            {/* Phone (Register only, optional) */}
            {isRegister && (
              <div>
                <label className="block text-sm font-semibold text-slate-200 mb-2">
                  Phone <span className="text-slate-500 text-xs font-normal">(optional)</span>
                </label>
                <input
                  type="tel"
                  name="phone"
                  placeholder="+1 (555) 123-4567"
                  value={form.phone}
                  onChange={handleChange}
                  disabled={loading}
                  className="w-full px-4 py-2.5 rounded-lg bg-slate-900/50 border border-slate-700 text-white placeholder-slate-500 transition-all focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                />
              </div>
            )}

            {/* Password */}
            <div>
              <label className="block text-sm font-semibold text-slate-200 mb-2">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  name="password"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={handleChange}
                  disabled={loading}
                  className="w-full px-4 py-2.5 pr-10 rounded-lg bg-slate-900/50 border border-slate-700 text-white placeholder-slate-500 transition-all focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  disabled={loading}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-300 transition disabled:opacity-50"
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              {isRegister && form.password && (
                <p className={`text-xs mt-1.5 ${form.password.length >= 8 ? 'text-emerald-400' : 'text-amber-400'}`}>
                  {form.password.length >= 8 ? '✓ Strong password' : '✗ Minimum 8 characters'}
                </p>
              )}
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || !isFormValid}
              className="w-full mt-6 py-3 px-4 rounded-lg bg-gradient-to-r from-blue-500 to-blue-600 text-white font-semibold transition-all hover:shadow-lg hover:shadow-blue-500/50 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader className="h-4 w-4 animate-spin" />
                  {isRegister ? 'Creating account...' : 'Signing in...'}
                </>
              ) : (
                <>
                  {isRegister ? <UserPlus className="h-4 w-4" /> : <LogIn className="h-4 w-4" />}
                  {isRegister ? 'Create Account' : 'Sign In'}
                </>
              )}
            </button>
          </form>

          {/* Toggle */}
          <p className="mt-6 text-center text-sm text-slate-500">
            {isRegister
              ? <>Already have an account? <Link to="/login" className="text-blue-400 hover:underline font-semibold">Sign in</Link></>
              : <>New here? <Link to="/register" className="text-blue-400 hover:underline font-semibold">Create account</Link></>
            }
          </p>
        </div>
      </div>
    </div>
  )
}
