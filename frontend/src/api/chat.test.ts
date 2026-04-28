import { API_BASE_URL, clearStoredToken, setStoredToken } from './client'
import { deleteChatSession } from './chat'

afterEach(() => {
  clearStoredToken()
  vi.restoreAllMocks()
})

test('deleteChatSession sends an authenticated DELETE request', async () => {
  setStoredToken('token-delete')
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)

  await deleteChatSession('session-123')

  expect(fetchMock).toHaveBeenCalledWith(
    `${API_BASE_URL}/chat/session/session-123`,
    expect.objectContaining({ method: 'DELETE' }),
  )
  const request = fetchMock.mock.calls[0][1] as RequestInit
  expect((request.headers as Headers).get('Authorization')).toBe('Bearer token-delete')
})
