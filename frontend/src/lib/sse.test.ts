import { parseSseStream } from './sse'

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder()
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
}

test('parseSseStream parses delta done and error events across chunks', async () => {
  const events: Array<{ type: string; delta?: string; done?: boolean; error?: string }> = []
  const stream = streamFromChunks([
    'data: {"type":"delta","delta":"你"}\n\n',
    'data: {"type":"delta","delta":"好"}\n',
    '\n',
    'data: {"type":"done","done":true}\n\n',
    'data: {"type":"error","error":"bad"}\n\n',
  ])

  await parseSseStream<(typeof events)[number]>(stream, (event) => events.push(event))

  expect(events).toEqual([
    { type: 'delta', delta: '你' },
    { type: 'delta', delta: '好' },
    { type: 'done', done: true },
    { type: 'error', error: 'bad' },
  ])
})
