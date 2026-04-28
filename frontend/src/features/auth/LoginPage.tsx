import { Check, Eye, EyeOff } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from './AuthProvider'

export function LoginPage() {
  const navigate = useNavigate()
  const { token, login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [identifier, setIdentifier] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (token) return <Navigate to="/app" replace />

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (mode === 'login') {
        await login(identifier, password, remember)
      } else {
        await register(username, email, password)
      }
      navigate('/app', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '认证失败，请检查输入。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <div className="login-glow" />
      <div className="login-texture" />
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="brand-row">
          <div className="logo-mark">FR</div>
          <div>
            <div className="brand-title">Finance Research</div>
            <div className="brand-sub">Multi-Agent 深度研究平台</div>
          </div>
        </div>

        <h1>{mode === 'login' ? '欢迎回来' : '创建研究账户'}</h1>
        <p className="login-copy">
          {mode === 'login'
            ? '登录以继续您的金融研究工作'
            : '注册后即可上传资料并开始投研问答'}
        </p>

        {mode === 'register' && (
          <label className="field">
            <span>用户名</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="researcher"
              autoComplete="username"
              required
              minLength={3}
            />
          </label>
        )}

        <label className="field">
          <span>{mode === 'login' ? '邮箱或用户名' : '邮箱地址'}</span>
          <input
            value={mode === 'login' ? identifier : email}
            onChange={(event) =>
              mode === 'login'
                ? setIdentifier(event.target.value)
                : setEmail(event.target.value)
            }
            placeholder="researcher@example.com"
            autoComplete="email"
            required
          />
        </label>

        <label className="field password-field">
          <span>密码</span>
          <input
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="至少 8 位"
            type={showPassword ? 'text' : 'password'}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
            minLength={8}
          />
          <button
            type="button"
            className="icon-button"
            onClick={() => setShowPassword((value) => !value)}
            aria-label={showPassword ? '隐藏密码' : '显示密码'}
          >
            {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </label>

        {mode === 'login' && (
          <button
            type="button"
            className="remember-row"
            onClick={() => setRemember((value) => !value)}
          >
            <span className={`custom-check ${remember ? 'checked' : ''}`}>
              {remember && <Check size={11} />}
            </span>
            <span>记住我的账户</span>
          </button>
        )}

        {error && <div className="form-error">{error}</div>}

        <button className="primary-button" type="submit" disabled={loading}>
          {loading ? '处理中…' : mode === 'login' ? '登录' : '注册并进入'}
        </button>

        <div className="auth-switch">
          {mode === 'login' ? '还没有账号？' : '已有账号？'}
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setError('')
            }}
          >
            {mode === 'login' ? '立即注册' : '返回登录'}
          </button>
        </div>
      </form>
      <div className="brand-strip">FINANCE RESEARCH ASSISTANT · v1.0</div>
    </main>
  )
}
