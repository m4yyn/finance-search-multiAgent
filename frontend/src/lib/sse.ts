export async function parseSseStream<T>(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: T) => void,
): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = drainBuffer(buffer, onEvent)
  }

  buffer += decoder.decode()
  drainBuffer(buffer, onEvent, true)
}

function drainBuffer<T>(
  buffer: string,
  onEvent: (event: T) => void,
  flush = false,
): string {
  const separator = /\r?\n\r?\n/
  let remaining = buffer

  while (true) {
    const match = remaining.match(separator)
    if (!match || match.index === undefined) break
    const block = remaining.slice(0, match.index)
    remaining = remaining.slice(match.index + match[0].length)
    emitBlock(block, onEvent)
  }

  if (flush && remaining.trim()) {
    emitBlock(remaining, onEvent)
    return ''
  }
  return remaining
}

function emitBlock<T>(block: string, onEvent: (event: T) => void): void {
  const dataLines = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.replace(/^data:\s?/, ''))

  if (!dataLines.length) return
  const payload = dataLines.join('\n').trim()
  if (!payload || payload === '[DONE]') return
  onEvent(JSON.parse(payload) as T)
}
