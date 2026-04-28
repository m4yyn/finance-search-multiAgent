import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import * as authApi from '../../api/auth'
import { clearStoredToken, getStoredToken, setStoredToken } from '../../api/client'
import type { User } from '../../types'

interface AuthContextValue {
  user: User | null
  token: string | null
  loading: boolean
  login: (identifier: string, password: string, remember: boolean) => Promise<void>
  register: (username: string, email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken())
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(Boolean(token))

  useEffect(() => {
    let active = true
    if (!token) {
      setLoading(false)
      return
    }
    setLoading(true)
    authApi
      .getMe()
      .then((me) => {
        if (active) setUser(me)
      })
      .catch(() => {
        clearStoredToken()
        if (active) {
          setToken(null)
          setUser(null)
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [token])

  const login = useCallback(
    async (identifier: string, password: string, remember: boolean) => {
      const response = await authApi.login({
        username_or_email: identifier,
        password,
      })
      if (remember) setStoredToken(response.access_token)
      else window.localStorage.setItem('finance-research-token', response.access_token)
      setToken(response.access_token)
      setUser(await authApi.getMe())
    },
    [],
  )

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      await authApi.register({ username, email, password })
      await login(username, password, true)
    },
    [login],
  )

  const logout = useCallback(async () => {
    try {
      if (token) await authApi.logout()
    } finally {
      clearStoredToken()
      setToken(null)
      setUser(null)
    }
  }, [token])

  const value = useMemo(
    () => ({ user, token, loading, login, register, logout }),
    [user, token, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
