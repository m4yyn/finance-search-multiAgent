import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  let hasKnowledgeBase = false
  let chatSessions: Array<{
    id: string
    user_id: string
    title: string
    created_at: string
    updated_at: string
    is_active: boolean
  }> = []
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'authorization,content-type',
    'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
  }
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: corsHeaders })
      return
    }
    await route.fallback()
  })
  await page.route('**/api/v1/auth/login', async (route) => {
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: 'token', token_type: 'bearer' }),
    })
  })
  await page.route('**/api/v1/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'user-1',
        username: 'researcher',
        email: 'researcher@example.com',
        is_active: true,
        is_superuser: false,
        created_at: '2026-04-28T00:00:00Z',
        updated_at: '2026-04-28T00:00:00Z',
      }),
    })
  })
  await page.route('**/api/v1/chat/sessions', async (route) => {
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify(chatSessions),
    })
  })
  await page.route('**/api/v1/chat/session', async (route) => {
    chatSessions = [
      {
        id: 'session-1',
        user_id: 'user-1',
        title: '新会话',
        created_at: '2026-04-28T00:00:00Z',
        updated_at: '2026-04-28T00:00:00Z',
        is_active: true,
      },
    ]
    await route.fulfill({
      status: 201,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 'session-1', title: '新会话' }),
    })
  })
  await page.route('**/api/v1/chat/session/session-1/messages', async (route) => {
    await route.fulfill({ status: 200, headers: corsHeaders, contentType: 'application/json', body: '[]' })
  })
  await page.route('**/api/v1/chat/session**', async (route) => {
    const url = route.request().url()
    if (url.endsWith('/chat/sessions')) {
      await route.fallback()
      return
    }
    if (route.request().method() === 'DELETE') {
      const sessionId = url.split('/chat/session/')[1]?.split('/')[0]
      chatSessions = chatSessions.filter((session) => session.id !== sessionId)
      await route.fulfill({ status: 204, headers: corsHeaders })
      return
    }
    if (url.endsWith('/messages')) {
      await route.fulfill({ status: 200, headers: corsHeaders, contentType: 'application/json', body: '[]' })
      return
    }
    chatSessions = [
      {
        id: 'session-1',
        user_id: 'user-1',
        title: '新会话',
        created_at: '2026-04-28T00:00:00Z',
        updated_at: '2026-04-28T00:00:00Z',
        is_active: true,
      },
    ]
    await route.fulfill({
      status: 201,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify({ session_id: 'session-1', title: '新会话' }),
    })
  })
  await page.route('**/api/v1/chat/stream', async (route) => {
    const payload = route.request().postDataJSON() as { search_mode: string }
    const sourceType = payload.search_mode === 'web' ? 'web' : 'local'
    const messageId = payload.search_mode === 'web' ? 'message-web' : 'message-local'
    await route.fulfill({
      status: 200,
      headers: { ...corsHeaders, 'Content-Type': 'text/event-stream' },
      body:
        'data: {"type":"delta","session_id":"session-1","delta":"回答"}\n\n' +
        'data: {"type":"delta","session_id":"session-1","delta":"完成"}\n\n' +
        `data: {"type":"done","session_id":"session-1","message_id":"${messageId}","done":true,"references":[{"index":1,"content":"来源片段","filename":"来源","score":0,"source_type":"${sourceType}","url":"https://example.com"}]}\n\n`,
    })
  })
  await page.route('**/api/v1/knowledge/bases', async (route) => {
    if (route.request().method() === 'POST') {
      hasKnowledgeBase = true
      await route.fulfill({
        status: 201,
        headers: corsHeaders,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'kb-1',
          user_id: 'user-1',
          name: '年报库',
          description: null,
          collection_name: 'kb_1',
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify(
        hasKnowledgeBase
          ? [
              {
                id: 'kb-1',
                user_id: 'user-1',
                name: '年报库',
                description: null,
                collection_name: 'kb_1',
                created_at: '2026-04-28T00:00:00Z',
                updated_at: '2026-04-28T00:00:00Z',
              },
            ]
          : [],
      ),
    })
  })
  await page.route('**/api/v1/knowledge/bases/kb-1/documents', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 201,
        headers: corsHeaders,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'doc-1',
          kb_id: 'kb-1',
          filename: 'annual.pdf',
          file_size: 1024,
          mime_type: 'application/pdf',
          status: 'pending',
          chunk_count: null,
          error_message: null,
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      headers: corsHeaders,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'doc-1',
          kb_id: 'kb-1',
          filename: 'annual.pdf',
          file_size: 1024,
          mime_type: 'application/pdf',
          status: 'success',
          chunk_count: 12,
          error_message: null,
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
      ]),
    })
  })
})

test('logs in, streams chat, toggles search mode, and uploads to a knowledge base', async ({
  page,
}) => {
  await page.goto('/login')
  await page.getByLabel('邮箱或用户名').fill('researcher')
  await page.getByPlaceholder('至少 8 位').fill('password123')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('heading', { name: 'Finance Research Assistant', level: 2 })).toBeVisible()
  await page.getByPlaceholder('输入研究问题。Enter 发送，Shift+Enter 换行。').fill('分析银行股')
  await expect(page.getByRole('button', { name: '发送' })).toBeEnabled()
  const sessionRequest = page.waitForRequest(
    (request) => request.url().includes('/api/v1/chat/session') && request.method() === 'POST',
  )
  const streamRequest = page.waitForRequest(
    (request) => request.url().includes('/api/v1/chat/stream') && request.method() === 'POST',
  )
  await page.getByRole('button', { name: '发送' }).click()
  await sessionRequest
  await streamRequest
  await expect(page.getByText('回答完成')).toBeVisible()
  await expect(page.getByRole('button', { name: /^新会话/ })).toBeVisible()

  const deleteRequest = page.waitForRequest(
    (request) =>
      request.url().includes('/api/v1/chat/session/session-1') &&
      request.method() === 'DELETE',
  )
  await page.getByRole('button', { name: /删除研究记录/ }).click()
  await deleteRequest
  await expect(page.getByText('暂无研究记录')).toBeVisible()

  await page.getByRole('button', { name: '网络搜索', exact: true }).click()
  await page.getByPlaceholder('输入研究问题。Enter 发送，Shift+Enter 换行。').fill('搜索A股市场')
  await expect(page.getByRole('button', { name: '发送' })).toBeEnabled()
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('引用来源')).toBeVisible()

  await page.getByRole('button', { name: '文件' }).click()
  await page.getByPlaceholder('新建知识库分类').fill('年报库')
  await page.getByRole('button', { name: '新建知识库' }).click()
  await expect(page.getByRole('button', { name: '年报库' })).toBeVisible()
})
