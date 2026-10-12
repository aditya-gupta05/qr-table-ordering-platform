import { ApiError, httpError, request } from './client'
import { readEventStream } from './sse'
import type {
  ActionResult,
  AnswerFrame,
  ChatSessionCreated,
  ChatSessionDetail,
  DoneFrame,
  ErrorFrame,
  Message,
  StatusFrame,
  TurnFrame,
} from './types'

export function createChatSession(tableToken: string, signal?: AbortSignal) {
  return request<ChatSessionCreated>('/api/chat/sessions', {
    method: 'POST',
    body: JSON.stringify({ table_token: tableToken }),
    signal,
  })
}

export function getChatSession(sessionId: string, signal?: AbortSignal) {
  return request<ChatSessionDetail>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { signal })
}

export function decideAction(sessionId: string, actionId: string, decision: 'accepted' | 'dismissed') {
  return request<ActionResult>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(actionId)}`,
    { method: 'POST', body: JSON.stringify({ decision }) },
  )
}

export function setMessageFeedback(messageId: number, helpful: boolean) {
  return request<void>(`/api/chat/messages/${messageId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ helpful }),
  })
}

export interface TurnHandlers {
  onTurn?: (f: TurnFrame) => void
  onStatus: (f: StatusFrame) => void
  onAnswer: (m: Message) => void
  onError: (f: ErrorFrame) => void
  onDone: (f: DoneFrame) => void
}

/**
 * Sends one turn as SSE. Resolves after the `done` frame. Rejects with ApiError for pre-stream
 * failures (404, 409, 422, 429) and with a network ApiError when the stream ends before `done`.
 * Retrying with the same clientMessageId replays the stored reply or re-runs an errored turn.
 */
export async function sendTurn(
  sessionId: string,
  text: string,
  clientMessageId: string,
  signal: AbortSignal,
  handlers: TurnHandlers,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, client_message_id: clientMessageId }),
      signal,
    })
  } catch (err) {
    if (signal.aborted) throw err
    throw new ApiError(0, 'network', null, ['Cannot reach the server. Check your connection and try again.'])
  }
  if (!res.ok) throw await httpError(res)
  if (!res.body) throw new ApiError(0, 'network', null, ['The answer did not arrive. Try again.'])

  let finished = false
  await readEventStream(res.body, ({ event, data }) => {
    let payload: unknown
    try {
      payload = JSON.parse(data)
    } catch {
      return
    }
    switch (event) {
      case 'turn':
        handlers.onTurn?.(payload as TurnFrame)
        break
      case 'status':
        handlers.onStatus(payload as StatusFrame)
        break
      case 'answer':
        handlers.onAnswer((payload as AnswerFrame).message)
        break
      case 'error':
        handlers.onError(payload as ErrorFrame)
        break
      case 'done':
        finished = true
        handlers.onDone(payload as DoneFrame)
        break
    }
  })
  if (!finished) {
    throw new ApiError(0, 'network', null, ['The connection dropped before the answer arrived. Try again.'])
  }
}
