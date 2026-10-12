export interface SseEvent {
  event: string
  data: string
}

/**
 * Reads a text/event-stream body and calls onEvent once per frame. Comment lines (": ping") and
 * blank keep-alives are skipped. A frame is `event:` + one or more `data:` lines ended by a blank line.
 * Resolves when the stream closes; rejects if the read fails (including abort).
 */
export async function readEventStream(body: ReadableStream<Uint8Array>, onEvent: (ev: SseEvent) => void): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  const emit = (block: string) => {
    let event = 'message'
    const data: string[] = []
    let seen = false
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue
      const colon = line.indexOf(':')
      const field = colon === -1 ? line : line.slice(0, colon)
      let value = colon === -1 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') {
        event = value
        seen = true
      } else if (field === 'data') {
        data.push(value)
        seen = true
      }
    }
    if (seen) onEvent({ event, data: data.join('\n') })
  }

  for (;;) {
    const { done, value } = await reader.read()
    buf += decoder.decode(value, { stream: !done }).replace(/\r\n?/g, '\n')
    let sep = buf.indexOf('\n\n')
    while (sep !== -1) {
      const block = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      emit(block)
      sep = buf.indexOf('\n\n')
    }
    if (done) break
  }
  if (buf.trim() !== '') emit(buf)
}
