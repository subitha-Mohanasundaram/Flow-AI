// Points to Render backend in production, proxied locally via vite.config.js
const BASE = import.meta.env.VITE_API_URL || ''

function getToken() {
  return localStorage.getItem('token') || ''
}

async function req(method, path, body, isForm = false) {
  const headers = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (!isForm && body) headers['Content-Type'] = 'application/json'

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  })

  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`)
  return data
}

export const api = {
  // Auth
  login:    (body) => req('POST', '/api/auth/login', body),
  register: (body) => req('POST', '/api/auth/register', body),
  me:       ()     => req('GET',  '/api/me'),

  // Workflows CRUD
  workflows:      ()         => req('GET',    '/api/workflows'),
  workflow:       (id)       => req('GET',    `/api/workflows/${id}`).then(r => r.workflow || r),
  createWorkflow: (body)     => req('POST',   '/api/workflows', body).then(r => r.workflow || r),
  updateWorkflow: (id, body) => req('PUT',    `/api/workflows/${id}`, body).then(r => r.workflow || r),
  deleteWorkflow: (id)       => req('DELETE', `/api/workflows/${id}`),

  // Workflow Examples
  workflowExamples: () => req('GET', '/api/workflow-examples'),

  // Execution
  runWorkflow:  (id, body) => req('POST', `/api/workflows/${id}/run`, body),
  workflowRuns: (id)       => req('GET',  `/api/workflows/${id}/runs`),
  runStatus:    (runId)    => req('GET',  `/api/runs/${runId}`),
  approveRun:   (runId, body) => req('POST', `/api/runs/${runId}/approve`, body),
  rejectRun:    (runId, body) => req('POST', `/api/runs/${runId}/reject`, body),

  // SSE stream URL (append token as query param for EventSource)
  runStreamUrl: (runId) => {
    const token = getToken()
    return `${BASE}/api/runs/${runId}/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`
  },

  // AI NL Edit
  aiEditPreview: (id, body) => req('POST', `/api/workflows/${id}/ai-edit`, body),
  aiEditApply:   (id, body) => req('POST', `/api/workflows/${id}/ai-edit/apply`, body),

  // AI Generate
  generateWorkflow: (body) => req('POST', '/api/ai/generate-workflow', body),
  aiChat:           (body) => req('POST', '/api/ai/chat', body),

  // Versioning
  workflowVersions: (id)             => req('GET',  `/api/workflows/${id}/versions`),
  restoreVersion:   (id, version_ts) => req('POST', `/api/workflows/${id}/versions/${version_ts}/restore`),

  // Plugin Settings
  pluginConfigs:    ()     => req('GET',  '/api/plugin-configs'),
  savePluginConfigs: (body) => req('POST', '/api/plugin-configs', body),

  // Health
  health: () => req('GET', '/api/health'),
}

