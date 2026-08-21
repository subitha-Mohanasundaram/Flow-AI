import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { Zap, GitBranch, Brain, Bell, Globe, Clock, User, Repeat, AlertCircle, Box, ChevronDown, ChevronUp } from 'lucide-react'

// ── Status colours ─────────────────────────────────────────────
const STATUS = {
  idle:    { ring: 'border-white/10',          dot: 'bg-slate-500' },
  running: { ring: 'border-brand-500/60',      dot: 'bg-brand-400 animate-pulse' },
  success: { ring: 'border-emerald-500/60',    dot: 'bg-emerald-400' },
  failed:  { ring: 'border-red-500/60',        dot: 'bg-red-400' },
}

// ── Node type metadata ──────────────────────────────────────────
const TYPE_META = {
  trigger:        { icon: Zap,         label: 'Trigger',        bg: 'from-brand-600/30 to-brand-700/20',      border: 'border-brand-500/40',    iconBg: 'bg-brand-500/20 text-brand-400' },
  action:         { icon: Box,         label: 'Action',         bg: 'from-blue-600/20 to-blue-800/10',        border: 'border-blue-500/30',      iconBg: 'bg-blue-500/20 text-blue-400' },
  condition:      { icon: GitBranch,   label: 'Condition',      bg: 'from-amber-600/20 to-amber-800/10',      border: 'border-amber-500/30',     iconBg: 'bg-amber-500/20 text-amber-400' },
  ai:             { icon: Brain,       label: 'AI',             bg: 'from-purple-600/20 to-purple-800/10',    border: 'border-purple-500/30',    iconBg: 'bg-purple-500/20 text-purple-400' },
  notification:   { icon: Bell,        label: 'Notification',   bg: 'from-pink-600/20 to-pink-800/10',        border: 'border-pink-500/30',      iconBg: 'bg-pink-500/20 text-pink-400' },
  webhook:        { icon: Globe,       label: 'Webhook',        bg: 'from-orange-600/20 to-orange-800/10',   border: 'border-orange-500/30',    iconBg: 'bg-orange-500/20 text-orange-400' },
  delay:          { icon: Clock,       label: 'Delay',          bg: 'from-slate-600/20 to-slate-800/10',     border: 'border-slate-500/30',     iconBg: 'bg-slate-500/20 text-slate-400' },
  human_approval: { icon: User,        label: 'Approval',       bg: 'from-red-600/20 to-red-800/10',         border: 'border-red-500/30',       iconBg: 'bg-red-500/20 text-red-400' },
  loop:           { icon: Repeat,      label: 'Loop',           bg: 'from-cyan-600/20 to-cyan-800/10',       border: 'border-cyan-500/30',      iconBg: 'bg-cyan-500/20 text-cyan-400' },
}

function WorkflowNode({ data, selected }) {
  const nodeType = data.nodeType || 'action'
  const meta     = TYPE_META[nodeType] || TYPE_META.action
  const Icon     = meta.icon
  const status   = data.status || 'idle'
  const s        = STATUS[status] || STATUS.idle
  const isTrigger = nodeType === 'trigger'
  const [outputOpen, setOutputOpen] = useState(false)

  // Flatten node output to displayable key-value pairs
  const outputEntries = (() => {
    if (status !== 'success' || !data.output) return []
    try {
      const obj = typeof data.output === 'string' ? JSON.parse(data.output) : data.output
      return Object.entries(obj).slice(0, 5)  // cap to 5 rows for compactness
    } catch {
      return [['result', String(data.output).slice(0, 80)]]
    }
  })()

  return (
    <div
      className={`
        relative min-w-[180px] max-w-[220px] rounded-xl border bg-gradient-to-br
        ${meta.bg} ${meta.border}
        ${s.ring} ${selected ? 'ring-2 ring-brand-400/70 ring-offset-1 ring-offset-dark-800' : ''}
        shadow-lg transition-all duration-150 cursor-pointer
      `}
    >
      {/* top handle — hidden for trigger nodes */}
      {!isTrigger && (
        <Handle
          type="target"
          position={Position.Top}
          className="!w-3 !h-3 !border-2 !border-dark-700 !bg-brand-400 !rounded-full"
        />
      )}

      <div className="px-3.5 py-2.5">
        {/* header row */}
        <div className="flex items-center gap-2 mb-1.5">
          <div className={`flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md ${meta.iconBg}`}>
            <Icon className="h-3.5 w-3.5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
              {meta.label}
            </div>
            <div className="text-xs font-bold text-white truncate leading-tight">
              {data.label || 'Unnamed'}
            </div>
          </div>
          {/* status dot */}
          <span className={`flex-shrink-0 h-2 w-2 rounded-full ${s.dot}`} title={status} />
        </div>

        {/* description */}
        {data.description && (
          <p className="text-[10px] text-slate-400 leading-relaxed line-clamp-2">
            {data.description}
          </p>
        )}

        {/* integration badge */}
        {data.integration && (
          <span className="mt-1.5 inline-block rounded-md bg-dark-600/60 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-slate-400">
            {data.integration}
          </span>
        )}

        {/* retry / timeout indicators */}
        <div className="mt-1.5 flex items-center gap-1.5">
          {data.hasRetry   && <span className="badge badge-teal  text-[8px] px-1.5 py-0">↻ retry</span>}
          {data.hasTimeout && <span className="badge badge-blue  text-[8px] px-1.5 py-0">⏱ timeout</span>}
          {data.hasError   && <span className="badge badge-red   text-[8px] px-1.5 py-0">⚠ err</span>}
        </div>

        {/* ── Output preview (success nodes only) ── */}
        {outputEntries.length > 0 && (
          <div className="mt-2 border-t border-emerald-500/20 pt-1.5">
            <button
              className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-emerald-400 hover:text-emerald-300 transition-colors w-full"
              onClick={(e) => { e.stopPropagation(); setOutputOpen(o => !o) }}
            >
              {outputOpen ? <ChevronUp className="h-2.5 w-2.5" /> : <ChevronDown className="h-2.5 w-2.5" />}
              Output
            </button>
            {outputOpen && (
              <div className="mt-1 rounded-md bg-dark-700/60 px-2 py-1.5 space-y-0.5 max-h-20 overflow-y-auto">
                {outputEntries.map(([k, v]) => (
                  <div key={k} className="flex items-start gap-1 text-[9px]">
                    <span className="text-emerald-400 font-semibold shrink-0">{k}:</span>
                    <span className="text-slate-300 font-mono break-all">{String(v).slice(0, 60)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* bottom handle */}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-3 !h-3 !border-2 !border-dark-700 !bg-brand-400 !rounded-full"
      />
    </div>
  )
}


// Export one component per nodeType — React Flow requires separate registrations
export const nodeTypes = Object.fromEntries(
  Object.keys(TYPE_META).map((t) => [
    t,
    (props) => <WorkflowNode {...props} />,
  ])
)

export { TYPE_META }
