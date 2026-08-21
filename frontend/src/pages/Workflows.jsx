import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import Topbar from '../components/Topbar'
import AIGeneratePanel from '../components/workflow/AIGeneratePanel'
import {
  GitBranch, Plus, Trash2, Clock, ArrowRight,
  Loader2, AlertCircle, Sparkles, BookOpen, X,
  FileJson, Download,
} from 'lucide-react'

export default function Workflows() {
  const [workflows,  setWorkflows]  = useState([])
  const [loading,    setLoading]    = useState(true)
  const [creating,   setCreating]   = useState(false)
  const [error,      setError]      = useState(null)
  const [showAI,     setShowAI]     = useState(false)
  const [showExamples, setShowExamples] = useState(false)
  const [examples,   setExamples]   = useState([])
  const [exLoading,  setExLoading]  = useState(false)
  const [importing,  setImporting]  = useState(null)
  const navigate = useNavigate()

  useEffect(() => { load() }, [])

  async function load() {
    try {
      setLoading(true)
      const data = await api.workflows()
      setWorkflows(data.workflows || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleCreate() {
    try {
      setCreating(true)
      const res = await api.createWorkflow({
        name: 'New Workflow', description: 'Created from Visual Builder',
        nodes: [], edges: [],
      })
      const wfId = res?.workflow?.id || res?.id
      if (wfId) navigate(`/workflows/${wfId}`)
      else load()
    } catch (e) {
      setError(e.message)
      setCreating(false)
    }
  }

  async function handleDelete(e, id) {
    e.preventDefault()
    if (!confirm('Delete this workflow?')) return
    await api.deleteWorkflow(id)
    setWorkflows(w => w.filter(x => x.id !== id))
  }

  async function handleOpenExamples() {
    setShowExamples(true)
    if (examples.length > 0) return
    setExLoading(true)
    try {
      const res = await api.workflowExamples()
      setExamples(res.examples || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setExLoading(false)
    }
  }

  async function handleImportExample(ex) {
    setImporting(ex.file)
    try {
      const res = await api.createWorkflow({
        name:        ex.workflow.name || ex.name,
        description: ex.workflow.description || ex.description,
        nodes:       ex.workflow.nodes || [],
        edges:       ex.workflow.edges || [],
        variables:   ex.workflow.variables || [],
        triggers:    ex.workflow.triggers || [],
      })
      setShowExamples(false)
      const wfId = res?.workflow?.id || res?.id
      if (wfId) navigate(`/workflows/${wfId}`)
      else load()
    } catch (e) {
      setError(e.message)
    } finally {
      setImporting(null)
    }
  }

  function handleGenerated(wf) {
    setShowAI(false)
    const wfId = wf?.workflow?.id || wf?.id
    if (wfId) navigate(`/workflows/${wfId}`)
    else load()
  }

  return (
    <div className="min-h-screen bg-dark-900">
      <Topbar />
      <div className="mx-auto max-w-5xl px-4 py-10 page-enter">
        {/* Header */}
        <div className="flex items-center justify-between mb-8 gap-3">
          <div>
            <h1 className="section-title flex items-center gap-3">
              <GitBranch className="h-6 w-6 text-brand-400" /> Workflows
            </h1>
            <p className="section-sub">Visual workflow builder — drag, connect, and automate.</p>
          </div>
          <div className="flex gap-2">
            <button onClick={handleOpenExamples} className="btn btn-secondary">
              <BookOpen className="h-4 w-4" /> Examples
            </button>
            <button onClick={() => setShowAI(true)} className="btn btn-secondary">
              <Sparkles className="h-4 w-4 text-purple-400" /> AI Generate
            </button>
            <button onClick={handleCreate} disabled={creating} className="btn btn-primary">
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              New Workflow
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 flex items-center gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-4 py-3 text-sm text-red-400">
            <AlertCircle className="h-4 w-4 flex-shrink-0" /> {error}
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-24 text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin mr-2" /> Loading workflows…
          </div>
        )}

        {/* Empty */}
        {!loading && !error && workflows.length === 0 && (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-500/10 mb-4">
              <GitBranch className="h-8 w-8 text-brand-400" />
            </div>
            <div className="text-lg font-bold text-white mb-1">No workflows yet</div>
            <div className="text-sm text-slate-400 mb-6">Create your first workflow or import an example.</div>
            <div className="flex gap-3">
              <button onClick={handleOpenExamples} className="btn btn-secondary">
                <BookOpen className="h-4 w-4" /> Import Example
              </button>
              <button onClick={handleCreate} className="btn btn-primary">
                <Plus className="h-4 w-4" /> Create Workflow
              </button>
            </div>
          </div>
        )}

        {/* Grid */}
        {!loading && workflows.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {workflows.map(wf => (
              <Link key={wf.id} to={`/workflows/${wf.id}`} className="card card-hover group flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-brand-500/15">
                    <GitBranch className="h-5 w-5 text-brand-400" />
                  </div>
                  <button onClick={(e) => handleDelete(e, wf.id)} className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-all p-1 rounded">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                <div className="flex-1">
                  <div className="font-bold text-white text-sm mb-0.5">{wf.name}</div>
                  <div className="text-xs text-slate-400 line-clamp-2">{wf.description || 'No description'}</div>
                </div>
                <div className="flex items-center justify-between text-[10px] text-slate-500 pt-1 border-t border-white/[0.04]">
                  <span className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {wf.updated_at ? new Date(wf.updated_at).toLocaleDateString() : 'Never'}
                  </span>
                  <span className="flex items-center gap-1 text-brand-400 font-semibold">
                    Open <ArrowRight className="h-3 w-3" />
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* AI Generate Modal */}
      {showAI && <AIGeneratePanel onGenerated={handleGenerated} onClose={() => setShowAI(false)} />}

      {/* Examples Modal */}
      {showExamples && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-dark-900/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-xl rounded-2xl border border-white/[0.08] bg-dark-800 shadow-2xl flex flex-col max-h-[85vh]">
            <div className="flex items-center gap-3 px-6 py-4 border-b border-white/[0.06]">
              <BookOpen className="h-5 w-5 text-brand-400" />
              <span className="font-bold text-white flex-1">Example Workflows</span>
              <button onClick={() => setShowExamples(false)} className="text-slate-500 hover:text-white transition-colors">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {exLoading && (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-6 w-6 animate-spin text-brand-400" />
                </div>
              )}
              {!exLoading && examples.length === 0 && (
                <div className="text-center py-10 text-slate-500 text-sm">
                  No example files found in <code className="text-xs font-mono">workflows/examples/</code>
                </div>
              )}
              {examples.map(ex => (
                <div key={ex.file} className="flex items-start gap-3 rounded-xl border border-white/[0.06] bg-dark-700 p-3">
                  <FileJson className="h-5 w-5 text-brand-400 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-white text-sm">{ex.name}</div>
                    <div className="text-xs text-slate-400 line-clamp-2">{ex.description || '—'}</div>
                    <div className="text-[10px] text-slate-600 mt-1">{ex.node_count} nodes · {ex.file}</div>
                  </div>
                  <button
                    onClick={() => handleImportExample(ex)}
                    disabled={importing === ex.file}
                    className="btn btn-primary btn-sm flex-shrink-0"
                  >
                    {importing === ex.file
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Download className="h-3.5 w-3.5" />}
                    Import
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
