import { API_BASE_URL, clearStoredToken, setStoredToken } from './client'
import { streamDeepResearch } from './deepResearch'

function streamFromText(text: string): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text))
      controller.close()
    },
  })
}

afterEach(() => {
  clearStoredToken()
  vi.restoreAllMocks()
})

test('streamDeepResearch sends authenticated payload and parses events', async () => {
  setStoredToken('token-research')
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      streamFromText(
        'data: {"type":"research_step","content":{"title":"规划"}}\n\n' +
          'data: {"type":"done","done":true}\n\n',
      ),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  const events: Array<{ type: string; done?: boolean }> = []

  await streamDeepResearch(
    {
      session_id: 'session-1',
      content: '分析银行业',
      search_web: true,
      search_local: false,
    },
    (event) => events.push({ type: event.type, done: event.done }),
  )

  expect(fetchMock).toHaveBeenCalledWith(
    `${API_BASE_URL}/deep-research/stream`,
    expect.objectContaining({ method: 'POST' }),
  )
  const request = fetchMock.mock.calls[0][1] as RequestInit
  expect((request.headers as Headers).get('Authorization')).toBe('Bearer token-research')
  expect(JSON.parse(String(request.body))).toEqual({
    session_id: 'session-1',
    content: '分析银行业',
    search_web: true,
    search_local: false,
  })
  expect(events).toEqual([{ type: 'research_step' }, { type: 'done', done: true }])
})

test('streamDeepResearch sends resume flag when requested', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(streamFromText('data: {"type":"done","done":true}\n\n'), {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)

  await streamDeepResearch(
    {
      session_id: 'session-1',
      content: '分析银行业',
      search_web: true,
      search_local: false,
      resume: true,
    },
    vi.fn(),
  )

  const request = fetchMock.mock.calls[0][1] as RequestInit
  expect(JSON.parse(String(request.body))).toEqual({
    session_id: 'session-1',
    content: '分析银行业',
    search_web: true,
    search_local: false,
    resume: true,
  })
})
