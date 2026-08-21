import { Routes, Route, Navigate } from 'react-router-dom'
import { getUser } from './lib/auth'
import Welcome         from './pages/Welcome'
import Login           from './pages/Login'
import Workflows       from './pages/Workflows'
import WorkflowBuilder from './pages/WorkflowBuilder'
import PluginSettings  from './pages/PluginSettings'

function Protected({ children }) {
  return getUser() ? children : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/"            element={<Welcome />} />
      <Route path="/login"       element={<Login />} />
      <Route path="/register"    element={<Login register />} />
      <Route path="/workflows"   element={<Protected><Workflows /></Protected>} />
      <Route path="/workflows/:id" element={<Protected><WorkflowBuilder /></Protected>} />
      <Route path="/plugin-settings" element={<Protected><PluginSettings /></Protected>} />
      <Route path="*"            element={<Navigate to="/" replace />} />
    </Routes>
  )
}
