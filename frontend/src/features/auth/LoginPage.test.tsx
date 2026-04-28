import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from './AuthProvider'
import { LoginPage } from './LoginPage'

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

test('login page submits credentials and navigates to app', async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/login')) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: 'token', token_type: 'bearer' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.endsWith('/auth/me')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: 'user-1',
              username: 'researcher',
              email: 'researcher@example.com',
              is_active: true,
              is_superuser: false,
              created_at: '2026-04-28T00:00:00Z',
              updated_at: '2026-04-28T00:00:00Z',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        )
      }
      return Promise.resolve(new Response('{}', { status: 404 }))
    }),
  )

  render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText('邮箱或用户名'), 'researcher')
  await user.type(screen.getByLabelText('密码'), 'password123')
  await user.click(screen.getByRole('button', { name: '登录' }))

  await waitFor(() => {
    expect(window.localStorage.getItem('finance-research-token')).toBe('token')
  })
})
