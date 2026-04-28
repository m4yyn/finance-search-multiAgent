import { apiRequest, clearStoredToken, setStoredToken, TOKEN_STORAGE_KEY } from './client'

afterEach(() => {
  clearStoredToken()
  vi.restoreAllMocks()
})

test('apiRequest attaches bearer token and parses json', async () => {
  setStoredToken('token-123')
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)

  const result = await apiRequest<{ ok: boolean }>('/demo')

  expect(result.ok).toBe(true)
  const request = fetchMock.mock.calls[0][1] as RequestInit
  expect((request.headers as Headers).get('Authorization')).toBe('Bearer token-123')
})

test('apiRequest clears stored token after 401', async () => {
  setStoredToken('expired')
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid authentication credentials.' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )

  await expect(apiRequest('/private')).rejects.toThrow('Invalid authentication credentials.')

  expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
})
