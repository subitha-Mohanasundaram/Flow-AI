import { useState, useRef, useEffect } from 'react'
import { api } from '../../lib/api'
import {
  Sparkles, Loader2, AlertCircle, CheckCircle2, X,
  FileJson, Plus, Send, User, Bot
} from 'lucide-react'

export default function AIGeneratePanel({ onGenerated, onClose }) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([]) // {role: 'user' | 'assistant', content: string}
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading, result])

  async function handleSend() {
    if (!input.trim()) return
    const userMsg = { role: 'user', content: input.trim() }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInput('')
    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const res = await api.aiChat({ messages: newMessages })
      if (res.type === 'workflow') {
        setResult(res)
        setMessages(prev => [...prev, { 
          role: 'assistant', 
          content: `I've generated the workflow! Click "Load into Builder" to proceed.` 
        }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: res.content }])
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleLoad() {
    if (!result?.workflow_json) return
    try {
      const saved = await api.createWorkflow({
        name: result.workflow_json.name || 'AI Generated Workflow',
        description: result.workflow_json.description || '',
        nodes: result.workflow_json.nodes || [],
        edges: result.workflow_json.edges || [],
      })
      if (onGenerated) onGenerated(saved)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-dark-900/80 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl rounded-2xl border border-white/[0.08] bg-dark-800 shadow-2xl flex flex-col max-h-[90vh] h-[800px]">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-white/[0.06]">
          <Sparkles className="h-5 w-5 text-purple-400" />
          <span className="font-bold text-white flex-1">Chat & Generate Workflow</span>
          <button onClick={onClose} className="text-slate-500 hover:text-white transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Chat History */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.length === 0 && !loading && (
            <div className="text-center text-slate-500 mt-10">
              <Sparkles className="h-10 w-10 mx-auto mb-3 opacity-20" />
              <p>Hi! Tell me what kind of workflow you want to build.</p>
              <p className="text-xs mt-2">Example: "Send a Slack message when a new user signs up."</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${msg.role === 'user' ? 'bg-brand-500/20 text-brand-400' : 'bg-purple-500/20 text-purple-400'}`}>
                {msg.role === 'user' ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
              </div>
              <div className={`max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-relaxed ${msg.role === 'user' ? 'bg-brand-500/10 text-white' : 'bg-dark-700 text-slate-200'}`}>
                {msg.content}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex gap-3 flex-row">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-purple-500/20 text-purple-400">
                <Bot className="h-4 w-4" />
              </div>
              <div className="rounded-2xl px-4 py-3 bg-dark-700 flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-purple-400" />
                <span className="text-sm text-slate-400">Thinking...</span>
              </div>
            </div>
          )}

          {/* Result Card */}
          {result && (
            <div className="ml-11 max-w-[80%] mt-2">
              <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-4 space-y-3">
                <div className="flex items-center gap-2 text-sm text-emerald-400 font-medium">
                  <CheckCircle2 className="h-4 w-4" />
                  Generated Workflow Ready
                </div>
                <div className="text-xs text-slate-400 space-y-1">
                  <p><strong className="text-slate-300">Name:</strong> {result.workflow_json?.name}</p>
                  <p><strong className="text-slate-300">Nodes:</strong> {result.node_count}</p>
                </div>
                <button onClick={handleLoad} className="btn btn-primary w-full py-2 mt-2 text-xs">
                  <Plus className="h-4 w-4" /> Load into Builder
                </button>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-400 ml-11 max-w-[80%] mt-2">
              <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" /> {error}
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-4 border-t border-white/[0.06] bg-dark-900/50">
          <div className="flex gap-2 relative">
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              placeholder="Type your message..."
              rows={1}
              className="input w-full resize-none py-3 pr-12 text-sm max-h-32"
              disabled={loading || !!result}
              style={{ minHeight: '44px' }}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || loading || !!result}
              className="absolute right-2 top-1.5 bottom-1.5 px-2 bg-brand-500 hover:bg-brand-400 text-white rounded-md disabled:opacity-50 disabled:hover:bg-brand-500 transition-colors flex items-center justify-center"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
          <p className="text-[10px] text-slate-500 mt-2 text-center">Press Enter to send, Shift+Enter for new line</p>
        </div>
      </div>
    </div>
  )
}
