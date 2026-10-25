// The agent streams its answer as Server-Sent Events (SSE). This file is the
// client: it POSTs a query to /ask, then parses event frames as they arrive.
//
// The wire format is frames separated by a blank line. A frame is a `event:`
// line followed by a `data:` line:
//
//   event: answer
//   data: {"headline":"...","key_finding":"...","tables":[...],"quotes":[...]}
//
//   event: error
//   data: {"message":"..."}
//
// The server builds each frame with json.dumps, so the `data:` payload is always
// a single line (newlines inside strings are escaped, never literal). That's why
// splitting on a blank line is safe. There is no tool_call frame anymore: the
// menu builds the payload in code, so the only model left is prose.

import type { Answer, MenuQuery } from './types'
import type { Lang } from './i18n'

// A discriminated union: `event` tells us which shape `data` has. The answer's
// content arrives in one `answer` frame carrying the full contract.
export type StreamEvent =
  | { event: 'answer'; data: Answer }
  | { event: 'error'; data: { message: string } }

// Streams one query's answer, firing `onEvent` for every parsed frame.
// Resolves when the stream ends; rejects on a non-2xx response or an abort.
export async function streamAnswer(
  query: MenuQuery,
  params: Record<string, unknown>,
  lang: Lang,
  onEvent: (e: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  // The shared /ask key, when the server has one set. Sent from the build's
  // env (VITE_ASK_API_KEY); absent in dev, where /ask is open.
  const apiKey: string | undefined = import.meta.env.VITE_ASK_API_KEY
  const response = await fetch('/ask', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
    body: JSON.stringify({ query, params, lang }),
    signal, // cancels the request when the caller aborts
  })

  if (!response.ok) {
    throw new Error(`/ask failed: ${response.status}`)
  }
  if (!response.body) {
    throw new Error('/ask returned no body')
  }

  // A ReadableStream reader hands us chunks of bytes; a TextDecoder turns them
  // into a string. `stream: true` tells it more chunks are coming, so it does
  // NOT mangle a multi-byte character (e.g. Cyrillic) split across two chunks.
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // Chunks land at arbitrary boundaries, so a frame may be split across two
    // reads. Split on "\n\n" and keep the trailing leftover in `buffer`; the
    // last element is either "" (frame ended exactly on the boundary) or a
    // partial frame to complete on the next read.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      const event = parseFrame(frame)
      if (event) onEvent(event)
    }
  }
}

// Turns one raw frame (two lines, no trailing blank line) into a StreamEvent.
// Returns null for a frame missing its event or data line.
function parseFrame(frame: string): StreamEvent | null {
  let event: string | null = null
  let data: string | null = null

  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice('event:'.length).trim()
    if (line.startsWith('data:')) data = line.slice('data:'.length).trim()
  }

  if (event === null || data === null) return null
  return { event, data: JSON.parse(data) } as StreamEvent
}
