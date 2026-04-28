import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from './AuthProvider'

export function ProtectedRoute() {
  const { token, loading } = useAuth()

  if (loading) {
    return <div className="boot-screen">正在载入研究工作台…</div>
  }
  return token ? <Outlet /> : <Navigate to="/login" replace />
}
